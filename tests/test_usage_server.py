import json
import time
import urllib.request
import unittest

from claude_replica.usage_server import (
    parse_device_info,
    DeviceRegistry,
    start_usage_server,
    stop_usage_server,
    get_local_ip
)
from claude_replica.config import ConfigManager


class TestUsageServer(unittest.TestCase):
    def test_parse_device_info(self):
        # iPhone UA
        iphone_ua = "Mozilla/5.0 (iPhone; CPU iPhone OS 17_4 like Mac OS X) AppleWebKit/605.1.15 Mobile Safari/604.1"
        info = parse_device_info(iphone_ua, "192.168.1.50")
        self.assertEqual(info["device_type"], "Mobile")
        self.assertEqual(info["os"], "iOS")
        self.assertEqual(info["browser"], "Safari")
        self.assertEqual(info["icon"], "📱")

        # Android UA
        android_ua = "Mozilla/5.0 (Linux; Android 14; Pixel 8) AppleWebKit/537.36 Chrome/124.0.0.0 Mobile Safari/537.36"
        android_info = parse_device_info(android_ua, "192.168.1.55")
        self.assertEqual(android_info["device_type"], "Mobile")
        self.assertEqual(android_info["os"], "Android")
        self.assertEqual(android_info["browser"], "Chrome")

        # Linux Desktop UA
        linux_ua = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/120.0.0.0 Safari/537.36"
        linux_info = parse_device_info(linux_ua, "127.0.0.1")
        self.assertEqual(linux_info["device_type"], "Desktop")
        self.assertEqual(linux_info["os"], "Linux")
        self.assertEqual(linux_info["icon"], "💻")

    def test_device_registry(self):
        reg = DeviceRegistry()
        dev = reg.register_or_pulse(
            client_id="test_dev_1",
            ip="192.168.1.100",
            user_agent="Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X)",
            extra_meta={"screen": "390x844"}
        )
        self.assertEqual(dev["client_id"], "test_dev_1")
        self.assertTrue(dev["is_online"])
        self.assertEqual(reg.get_active_count(), 1)

        # Disconnect
        reg.disconnect("test_dev_1")
        all_devs = reg.get_all_devices()
        self.assertEqual(len(all_devs), 1)
        self.assertFalse(all_devs[0]["is_online"])

    def test_usage_server_endpoints(self):
        cfg = ConfigManager()
        port, local_url, net_url = start_usage_server(port=54325, in_background=True, config_mgr=cfg)
        self.assertTrue(local_url.startswith("http://localhost:"))

        try:
            # 1. Test HTML endpoint
            with urllib.request.urlopen(f"{local_url}/") as resp:
                self.assertEqual(resp.status, 200)
                html = resp.read().decode("utf-8")
                self.assertIn("Claude Code · Usage Shower", html)

            # 2. Test JS endpoint
            with urllib.request.urlopen(f"{local_url}/usage.js") as resp:
                self.assertEqual(resp.status, 200)
                js = resp.read().decode("utf-8")
                self.assertIn("sendHeartbeat", js)

            # 3. Test CSS endpoint
            with urllib.request.urlopen(f"{local_url}/usage.css") as resp:
                self.assertEqual(resp.status, 200)
                css = resp.read().decode("utf-8")
                self.assertIn("--bg-main", css)

            # 3b. Test GIF endpoints
            for gif_name in ["claude-jam.gif", "claude-processing.gif"]:
                with urllib.request.urlopen(f"{local_url}/{gif_name}") as resp:
                    self.assertEqual(resp.status, 200)
                    self.assertEqual(resp.headers["Content-Type"], "image/gif")
                    self.assertTrue(len(resp.read()) > 1000)

            # 4. Test API usage endpoint
            with urllib.request.urlopen(f"{local_url}/api/usage") as resp:
                self.assertEqual(resp.status, 200)
                data = json.loads(resp.read().decode("utf-8"))
                self.assertIn("session", data)
                self.assertIn("lifetime", data)
                self.assertIn("quota", data)
                self.assertIn("base44", data)
                self.assertIn("groq_global", data)

            # 5. Test Heartbeat POST
            heartbeat_body = json.dumps({
                "client_id": "test_phone_client",
                "device_type": "Mobile",
                "os": "Android",
                "browser": "Chrome",
                "action": "connect"
            }).encode("utf-8")
            req = urllib.request.Request(
                f"{local_url}/api/heartbeat",
                data=heartbeat_body,
                headers={"Content-Type": "application/json"}
            )
            with urllib.request.urlopen(req) as resp:
                self.assertEqual(resp.status, 200)
                hb_res = json.loads(resp.read().decode("utf-8"))
                self.assertEqual(hb_res["status"], "ok")
                self.assertGreaterEqual(hb_res["active_count"], 1)

            # 6. Test Devices GET
            with urllib.request.urlopen(f"{local_url}/api/devices") as resp:
                self.assertEqual(resp.status, 200)
                dev_data = json.loads(resp.read().decode("utf-8"))
                self.assertIn("devices", dev_data)
                found = any(d["client_id"] == "test_phone_client" for d in dev_data["devices"])
                self.assertTrue(found)

        finally:
            stop_usage_server()


if __name__ == "__main__":
    unittest.main()
