import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch, MagicMock

from claude_replica.groq_usage import (
    GroqUsageTracker,
    parse_duration_str,
    get_groq_tracker
)
from claude_replica.config import ConfigManager


class TestGroqUsage(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.usage_file = Path(self.temp_dir.name) / "global_groq_usage.json"

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_parse_duration_str(self):
        self.assertEqual(parse_duration_str("577ms"), 0.577)
        self.assertEqual(parse_duration_str("10s"), 10.0)
        self.assertEqual(parse_duration_str("2m52.8s"), 172.8)
        self.assertEqual(parse_duration_str("1h"), 3600.0)
        self.assertEqual(parse_duration_str(""), 0.0)
        self.assertEqual(parse_duration_str("invalid"), 0.0)

    def test_record_headers(self):
        with patch("claude_replica.groq_usage.GROQ_USAGE_FILE", self.usage_file):
            tracker = GroqUsageTracker()
            headers = {
                "x-ratelimit-limit-requests": "1000",
                "x-ratelimit-remaining-requests": "995",
                "x-ratelimit-limit-tokens": "8000",
                "x-ratelimit-remaining-tokens": "7500",
                "x-ratelimit-reset-requests": "2m30s",
                "x-ratelimit-reset-tokens": "400ms",
                "x-groq-region": "fra"
            }
            tracker.record_headers(headers)

            self.assertEqual(tracker.limit_requests, 1000)
            self.assertEqual(tracker.remaining_requests, 995)
            self.assertEqual(tracker.limit_tokens, 8000)
            self.assertEqual(tracker.remaining_tokens, 7500)
            self.assertEqual(tracker.region, "fra")
            self.assertEqual(tracker.reset_tokens_sec, 0.4)
            self.assertEqual(tracker.reset_requests_sec, 150.0)

    def test_record_response_and_accumulation(self):
        with patch("claude_replica.groq_usage.GROQ_USAGE_FILE", self.usage_file):
            tracker = GroqUsageTracker()
            headers = {
                "x-ratelimit-limit-tokens": "8000",
                "x-ratelimit-remaining-tokens": "7000",
                "x-groq-region": "lhr"
            }
            usage_1 = {
                "prompt_tokens": 100,
                "completion_tokens": 50,
                "total_tokens": 150,
                "queue_time": 0.05
            }
            tracker.record_response(headers, usage=usage_1, model="openai/gpt-oss-120b", duration=1.5)

            self.assertEqual(tracker.total_tokens, 150)
            self.assertEqual(tracker.prompt_tokens, 100)
            self.assertEqual(tracker.completion_tokens, 50)
            self.assertEqual(tracker.requests_count, 1)
            self.assertEqual(tracker.credits_used, 0.15)
            self.assertEqual(tracker.queue_time, 0.05)
            self.assertIn("openai/gpt-oss-120b", tracker.models_usage)
            self.assertEqual(tracker.models_usage["openai/gpt-oss-120b"]["total_tokens"], 150)

            # Record second response
            usage_2 = {
                "prompt_tokens": 200,
                "completion_tokens": 100,
                "total_tokens": 300
            }
            tracker.record_response(headers, usage=usage_2, model="openai/gpt-oss-120b", duration=2.0)

            self.assertEqual(tracker.total_tokens, 450)
            self.assertEqual(tracker.prompt_tokens, 300)
            self.assertEqual(tracker.completion_tokens, 150)
            self.assertEqual(tracker.requests_count, 2)
            self.assertEqual(tracker.credits_used, 0.45)

            # Test persistence
            tracker2 = GroqUsageTracker()
            self.assertEqual(tracker2.total_tokens, 450)
            self.assertEqual(tracker2.requests_count, 2)

    def test_status_health_calculation(self):
        with patch("claude_replica.groq_usage.GROQ_USAGE_FILE", self.usage_file):
            tracker = GroqUsageTracker()
            
            # Healthy
            tracker.remaining_tokens = 7000
            tracker.limit_tokens = 8000
            tracker.remaining_requests = 900
            tracker.limit_requests = 1000
            status = tracker.get_status()
            self.assertEqual(status["live_quota"]["status"], "healthy")

            # Near limit
            tracker.remaining_tokens = 500
            status = tracker.get_status()
            self.assertEqual(status["live_quota"]["status"], "near_limit")

            # Throttled
            tracker.remaining_tokens = 0
            status = tracker.get_status()
            self.assertEqual(status["live_quota"]["status"], "throttled")

    def test_reset_global_usage(self):
        with patch("claude_replica.groq_usage.GROQ_USAGE_FILE", self.usage_file):
            tracker = GroqUsageTracker()
            tracker.total_tokens = 5000
            tracker.requests_count = 10
            tracker.reset_global_usage()

            self.assertEqual(tracker.total_tokens, 0)
            self.assertEqual(tracker.requests_count, 0)
            self.assertEqual(tracker.credits_used, 0.0)

    @patch("claude_replica.groq_usage.httpx.Client")
    def test_refresh_live_quota(self, mock_httpx):
        mock_client = MagicMock()
        mock_httpx.return_value.__enter__.return_value = mock_client
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.headers = {
            "x-ratelimit-limit-tokens": "8000",
            "x-ratelimit-remaining-tokens": "7900",
            "x-ratelimit-limit-requests": "1000",
            "x-ratelimit-remaining-requests": "990",
            "x-ratelimit-reset-tokens": "300ms",
            "x-ratelimit-reset-requests": "1m",
            "x-groq-region": "fra"
        }
        mock_resp.json.return_value = {
            "usage": {"prompt_tokens": 10, "completion_tokens": 1, "total_tokens": 11}
        }
        mock_client.post.return_value = mock_resp

        with patch("claude_replica.groq_usage.GROQ_USAGE_FILE", self.usage_file):
            tracker = GroqUsageTracker()
            res = tracker.refresh_live_quota(api_key="test-groq-key")

            self.assertTrue(res["success"])
            self.assertEqual(tracker.limit_tokens, 8000)
            self.assertEqual(tracker.remaining_tokens, 7900)
            self.assertEqual(tracker.region, "fra")


if __name__ == "__main__":
    unittest.main()
