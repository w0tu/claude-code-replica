"""
Claude Code / Clawd Real-Time Usage Shower & Device Connection Server.
Serves the live updating usage dashboard (usage.html, usage.js, usage.css)
and tracks connected hardware devices (mobile phones, tablets, laptops) in real-time.
"""

import os
import sys
import json
import time
import socket
import datetime
import threading
from pathlib import Path
from http.server import HTTPServer, BaseHTTPRequestHandler
from socketserver import ThreadingMixIn
from typing import Dict, Any, List, Optional, Tuple

from claude_replica.config import ConfigManager

WEB_DIR = Path(__file__).parent / "web"


def get_local_ip() -> str:
    """Discover the local Wi-Fi / Ethernet LAN IP address."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(('10.254.254.254', 1))
        ip = s.getsockname()[0]
    except Exception:
        ip = '127.0.0.1'
    finally:
        s.close()
    return ip


def parse_device_info(user_agent: str, ip: str, extra_meta: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Parse User-Agent and device metadata to identify hardware category, OS, and browser."""
    ua = (user_agent or "").lower()
    meta = extra_meta or {}

    # Category & Icon
    if meta.get("device_type"):
        dev_type = meta["device_type"]
    elif any(k in ua for k in ["iphone", "android", "mobile", "ipod"]):
        dev_type = "Mobile"
    elif any(k in ua for k in ["ipad", "tablet"]):
        dev_type = "Tablet"
    else:
        dev_type = "Desktop"

    icon_map = {"Mobile": "📱", "Tablet": "📟", "Desktop": "💻"}
    icon = icon_map.get(dev_type, "💻")

    # Operating System
    if meta.get("os"):
        os_name = meta["os"]
    elif "iphone" in ua or "ipod" in ua or "ios" in ua:
        os_name = "iOS"
    elif "ipad" in ua:
        os_name = "iPadOS"
    elif "android" in ua:
        os_name = "Android"
    elif "macintosh" in ua or "mac os" in ua:
        os_name = "macOS"
    elif "windows" in ua:
        os_name = "Windows"
    elif "linux" in ua:
        os_name = "Linux"
    else:
        os_name = "Unknown OS"

    # Browser
    if meta.get("browser"):
        browser = meta["browser"]
    elif "edg" in ua:
        browser = "Edge"
    elif "chrome" in ua and "safari" in ua:
        browser = "Chrome"
    elif "safari" in ua and "chrome" not in ua:
        browser = "Safari"
    elif "firefox" in ua:
        browser = "Firefox"
    else:
        browser = "Browser"

    name = f"{os_name} {dev_type} ({browser})"
    return {
        "device_type": dev_type,
        "icon": icon,
        "os": os_name,
        "browser": browser,
        "name": name,
        "ip": ip,
        "screen": meta.get("screen", "")
    }


class DeviceRegistry:
    """Thread-safe registry for connected devices with active heartbeat tracking."""

    def __init__(self):
        self._lock = threading.Lock()
        self.devices: Dict[str, Dict[str, Any]] = {}

    def register_or_pulse(
        self,
        client_id: str,
        ip: str,
        user_agent: str,
        extra_meta: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        with self._lock:
            now = time.time()
            now_str = datetime.datetime.fromtimestamp(now).strftime("%H:%M:%S")

            if client_id not in self.devices:
                parsed = parse_device_info(user_agent, ip, extra_meta)
                self.devices[client_id] = {
                    "client_id": client_id,
                    "ip": ip,
                    "device_type": parsed["device_type"],
                    "icon": parsed["icon"],
                    "os": parsed["os"],
                    "browser": parsed["browser"],
                    "name": parsed["name"],
                    "screen": parsed["screen"],
                    "connected_at": now,
                    "connected_at_str": now_str,
                    "last_seen": now,
                    "is_online": True
                }
            else:
                dev = self.devices[client_id]
                dev["last_seen"] = now
                dev["is_online"] = True
                if extra_meta and extra_meta.get("screen"):
                    dev["screen"] = extra_meta["screen"]

            return self.devices[client_id]

    def disconnect(self, client_id: str):
        with self._lock:
            if client_id in self.devices:
                self.devices[client_id]["is_online"] = False
                self.devices[client_id]["last_seen"] = 0.0

    def get_all_devices(self, timeout_sec: float = 10.0) -> List[Dict[str, Any]]:
        with self._lock:
            now = time.time()
            result = []
            for dev_id, data in self.devices.items():
                copy_d = dict(data)
                is_active = copy_d.get("is_online", True) and ((now - copy_d.get("last_seen", 0)) < timeout_sec)
                copy_d["is_online"] = is_active
                result.append(copy_d)

            # Sort: online first, then by connected_at desc
            result.sort(key=lambda x: (1 if x["is_online"] else 0, x["connected_at"]), reverse=True)
            return result

    def get_active_count(self, timeout_sec: float = 10.0) -> int:
        devices = self.get_all_devices(timeout_sec)
        return sum(1 for d in devices if d["is_online"])


class ThreadedHTTPServer(ThreadingMixIn, HTTPServer):
    """Multi-threaded HTTP Server for concurrent web requests."""
    daemon_threads = True
    allow_reuse_address = True


_GLOBAL_REGISTRY = DeviceRegistry()
_RUNNING_SERVER: Optional[ThreadedHTTPServer] = None
_RUNNING_PORT: Optional[int] = None
_CONFIG_MGR: Optional[ConfigManager] = None


class UsageServerHandler(BaseHTTPRequestHandler):
    """HTTP Request Handler serving static web files and REST endpoints."""

    def log_message(self, format, *args):
        # Suppress noisy standard HTTP access logs to keep terminal clean
        pass

    def _send_json(self, data: Any, status: int = 200):
        body = json.dumps(data).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()
        self.wfile.write(body)

    def _send_file(self, file_path: Path, content_type: str):
        if not file_path.exists():
            self.send_error(404, f"File Not Found: {file_path.name}")
            return
        content = file_path.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(content)))
        self.end_headers()
        self.wfile.write(content)

    def do_OPTIONS(self):
        self.send_response(200)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def do_GET(self):
        parsed_path = self.path.split("?")[0]
        client_ip = self.client_address[0]
        ua = self.headers.get("User-Agent", "")

        # Auto-register device on initial page load if not already tracked
        if parsed_path in ["/", "/usage.html"]:
            dev_id = f"auto_{client_ip.replace('.', '_')}"
            _GLOBAL_REGISTRY.register_or_pulse(dev_id, client_ip, ua)

        if parsed_path in ["/", "/usage.html", "/index.html"]:
            self._send_file(WEB_DIR / "usage.html", "text/html; charset=utf-8")

        elif parsed_path == "/usage.js":
            self._send_file(WEB_DIR / "usage.js", "application/javascript; charset=utf-8")

        elif parsed_path == "/usage.css":
            self._send_file(WEB_DIR / "usage.css", "text/css; charset=utf-8")

        elif parsed_path.endswith(".gif") or parsed_path.endswith(".png"):
            fname = parsed_path.split("/")[-1]
            p = WEB_DIR / fname
            if not p.exists():
                p = Path("/home/feds") / fname
            ctype = "image/gif" if fname.endswith(".gif") else "image/png"
            self._send_file(p, ctype)

        elif parsed_path == "/api/usage":
            cfg = _CONFIG_MGR or ConfigManager()
            session_data = cfg.get_session_usage()
            lifetime_data = cfg.get_total_telemetry()
            
            allowed, remaining, limit = cfg.check_rate_limit()
            effort_tier = cfg.get("effort", "normal")
            
            local_ip = get_local_ip()
            port = _RUNNING_PORT or 54321

            devices = _GLOBAL_REGISTRY.get_all_devices()
            active_count = sum(1 for d in devices if d["is_online"])

            # Base44 UsageGuard integration
            try:
                from claude_replica.base44 import get_base44_client
                base44_status = get_base44_client(cfg).get_status()
            except Exception:
                base44_status = None

            # Global Groq Usage & Live Quota integration
            try:
                from claude_replica.groq_usage import get_groq_tracker
                groq_status = get_groq_tracker(cfg).get_status()
            except Exception:
                groq_status = None

            payload = {
                "session": session_data,
                "lifetime": lifetime_data,
                "quota": {
                    "hourly_limit": limit,
                    "hourly_remaining": remaining,
                    "hourly_used": limit - remaining,
                    "effort_tier": effort_tier
                },
                "base44": base44_status,
                "groq_global": groq_status,
                "connected_devices": devices,
                "active_device_count": max(1, active_count),
                "local_url": f"http://localhost:{port}",
                "network_url": f"http://{local_ip}:{port}",
                "server_time": time.time()
            }
            self._send_json(payload)

        elif parsed_path == "/api/devices":
            devices = _GLOBAL_REGISTRY.get_all_devices()
            self._send_json({"devices": devices, "active_count": sum(1 for d in devices if d["is_online"])})

        else:
            self.send_error(404, f"Path not found: {self.path}")

    def do_POST(self):
        parsed_path = self.path.split("?")[0]
        client_ip = self.client_address[0]
        ua = self.headers.get("User-Agent", "")

        content_len = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(content_len) if content_len > 0 else b"{}"
        
        try:
            body_json = json.loads(body.decode("utf-8"))
        except Exception:
            body_json = {}

        client_id = body_json.get("client_id") or f"client_{client_ip.replace('.', '_')}"

        if parsed_path == "/api/heartbeat":
            dev = _GLOBAL_REGISTRY.register_or_pulse(
                client_id=client_id,
                ip=client_ip,
                user_agent=body_json.get("user_agent", ua),
                extra_meta=body_json
            )
            active_count = _GLOBAL_REGISTRY.get_active_count()
            self._send_json({
                "status": "ok",
                "device": dev,
                "active_count": active_count
            })

        elif parsed_path == "/api/disconnect":
            _GLOBAL_REGISTRY.disconnect(client_id)
            self._send_json({"status": "disconnected"})

        else:
            self.send_error(404, "Endpoint not found")


def start_usage_server(
    port: int = 54321,
    host: str = "0.0.0.0",
    in_background: bool = True,
    config_mgr: Optional[ConfigManager] = None
) -> Tuple[int, str, str]:
    """
    Launch the Claude Code Usage Shower HTTP server.
    Binds to host and port with automatic fallback if port is in use.
    Returns (actual_port, local_url, network_url).
    """
    global _RUNNING_SERVER, _RUNNING_PORT, _CONFIG_MGR

    if _RUNNING_SERVER is not None and _RUNNING_PORT is not None:
        local_ip = get_local_ip()
        return (_RUNNING_PORT, f"http://localhost:{_RUNNING_PORT}", f"http://{local_ip}:{_RUNNING_PORT}")

    _CONFIG_MGR = config_mgr or ConfigManager()
    actual_port = port

    # Scan for available port
    for p in range(port, port + 50):
        try:
            server = ThreadedHTTPServer((host, p), UsageServerHandler)
            actual_port = p
            _RUNNING_SERVER = server
            _RUNNING_PORT = p
            break
        except OSError:
            continue

    if _RUNNING_SERVER is None:
        raise RuntimeError(f"Could not bind Usage Shower server on ports {port}-{port+50}")

    local_ip = get_local_ip()
    local_url = f"http://localhost:{actual_port}"
    network_url = f"http://{local_ip}:{actual_port}"

    if in_background:
        t = threading.Thread(target=_RUNNING_SERVER.serve_forever, daemon=True, name="UsageShowerServer")
        t.start()
    else:
        _RUNNING_SERVER.serve_forever()

    return (actual_port, local_url, network_url)


def get_usage_server_info() -> Optional[Dict[str, Any]]:
    """Return status and URLs of currently running Usage Shower server."""
    if _RUNNING_SERVER is None or _RUNNING_PORT is None:
        return None
    local_ip = get_local_ip()
    return {
        "port": _RUNNING_PORT,
        "local_url": f"http://localhost:{_RUNNING_PORT}",
        "network_url": f"http://{local_ip}:{_RUNNING_PORT}",
        "active_devices": _GLOBAL_REGISTRY.get_active_count()
    }


def stop_usage_server():
    """Cleanly shut down Usage Shower server."""
    global _RUNNING_SERVER, _RUNNING_PORT
    if _RUNNING_SERVER is not None:
        try:
            _RUNNING_SERVER.shutdown()
            _RUNNING_SERVER.server_close()
        except Exception:
            pass
        _RUNNING_SERVER = None
        _RUNNING_PORT = None
