import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch, MagicMock

from claude_replica.base44 import Base44Client, DEFAULT_BASE44_URL, DEFAULT_APP_ID
from claude_replica.config import ConfigManager


class TestBase44Client(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.guard_file = Path(self.temp_dir.name) / "base44_guard.json"

    def tearDown(self):
        self.temp_dir.cleanup()

    @patch("claude_replica.base44.GUARD_FILE")
    def test_init_and_status(self, mock_guard_file):
        mock_guard_file.exists.return_value = False
        client = Base44Client()
        self.assertEqual(client.base_url, DEFAULT_BASE44_URL)
        self.assertEqual(client.app_id, DEFAULT_APP_ID)
        self.assertTrue(len(client.device_name) > 0)
        
        status = client.get_status()
        self.assertIn("device_name", status)
        self.assertIn("status", status)
        self.assertIn("max_usage", status)
        self.assertIn("current_usage", status)
        self.assertIn("is_over_limit", status)

    @patch("claude_replica.base44.GUARD_FILE")
    def test_over_limit_logic(self, mock_guard_file):
        mock_guard_file.exists.return_value = False
        client = Base44Client()
        client.max_usage = 500
        client.current_usage = 200
        client.status = "active"
        self.assertFalse(client.get_status()["is_over_limit"])

        # Exceed max_usage
        client.current_usage = 600
        self.assertTrue(client.get_status()["is_over_limit"])
        self.assertEqual(client.get_status()["status"], "over_limit")

        # Explicit status over_limit
        client.current_usage = 100
        client.status = "over_limit"
        self.assertTrue(client.get_status()["is_over_limit"])

    def test_local_state_persistence(self):
        with patch("claude_replica.base44.GUARD_FILE", self.guard_file):
            client = Base44Client()
            client.max_usage = 7777
            client.status = "active"
            client.current_usage = 350
            client.api_token = "secret-token"
            client.last_sync_str = "12:00:00"
            client._save_local_state()

            self.assertTrue(self.guard_file.exists())
            with open(self.guard_file, "r") as f:
                saved = json.load(f)
            self.assertEqual(saved["max_usage"], 7777)
            self.assertEqual(saved["current_usage"], 350)
            self.assertEqual(saved["api_token"], "***")

            # Load into fresh client
            client2 = Base44Client()
            self.assertEqual(client2.max_usage, 7777)
            self.assertEqual(client2.current_usage, 350)

    @patch("claude_replica.base44.httpx.Client")
    def test_send_heartbeat_success(self, mock_httpx_cls):
        mock_client = MagicMock()
        mock_httpx_cls.return_value.__enter__.return_value = mock_client
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "maxUsage": 25000,
            "apiToken": "token-xyz-123",
            "status": "active"
        }
        mock_client.post.return_value = mock_resp

        with patch("claude_replica.base44.GUARD_FILE", self.guard_file):
            client = Base44Client()
            res = client.send_heartbeat(usage=1234)

            self.assertTrue(res["success"])
            self.assertEqual(res["max_usage"], 25000)
            self.assertEqual(res["status"], "active")
            self.assertEqual(res["usage"], 1234)
            self.assertEqual(client.api_token, "token-xyz-123")

    @patch("claude_replica.base44.httpx.Client")
    def test_send_heartbeat_failure_handled_gracefully(self, mock_httpx_cls):
        mock_client = MagicMock()
        mock_httpx_cls.return_value.__enter__.return_value = mock_client
        mock_resp = MagicMock()
        mock_resp.status_code = 500
        mock_resp.text = "Internal Server Error"
        mock_client.post.return_value = mock_resp

        # Patch time.sleep to avoid waiting in tests
        with patch("claude_replica.base44.time.sleep"), patch("claude_replica.base44.GUARD_FILE", self.guard_file):
            client = Base44Client()
            res = client.send_heartbeat(usage=50)

            self.assertFalse(res["success"])
            self.assertIn("HTTP 500", res["error"])
            self.assertIsNotNone(client.last_error)

    @patch("claude_replica.base44.httpx.Client")
    def test_lovable_mirroring(self, mock_httpx_cls):
        mock_client = MagicMock()
        mock_httpx_cls.return_value.__enter__.return_value = mock_client
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "maxUsage": 5000,
            "apiToken": "lovable-auth-key",
            "status": "active"
        }
        mock_client.post.return_value = mock_resp

        with patch("claude_replica.base44.GUARD_FILE", self.guard_file):
            client = Base44Client(lovable_url="https://api.lovable.dev")
            client.send_heartbeat(usage=888)

            # Check that post was called twice: once for base44, once for lovable
            self.assertEqual(mock_client.post.call_count, 2)
            lovable_call = mock_client.post.call_args_list[1]
            self.assertEqual(lovable_call[0][0], "https://api.lovable.dev/usage")
            self.assertEqual(lovable_call[1]["headers"]["Authorization"], "Bearer lovable-auth-key")

    def test_background_loop_lifecycle(self):
        with patch("claude_replica.base44.GUARD_FILE", self.guard_file), \
             patch.object(Base44Client, "send_heartbeat") as mock_hb:
            client = Base44Client()
            client.start_background_loop(interval_sec=1)
            self.assertIsNotNone(client._bg_thread)
            self.assertTrue(client._bg_thread.is_alive())
            
            client.stop_background_loop()
            self.assertTrue(client._stop_event.is_set())


if __name__ == "__main__":
    unittest.main()
