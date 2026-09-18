"""
Base44 UsageGuard Client for Claude Code / Coder Replica.
Integrates with https://claudecode.base44.app to automatically register devices,
stream live usage heartbeats every 2 minutes and after every turn, and respect per-device limits.
"""

import os
import sys
import json
import time
import socket
import datetime
import threading
from pathlib import Path
from typing import Dict, Any, Optional, Tuple

import httpx

from claude_replica.config import ConfigManager, CONFIG_DIR

DEFAULT_BASE44_URL = "https://claudecode.base44.app"
DEFAULT_APP_ID = "6aa01ac3955aac8f2331761f"
GUARD_FILE = CONFIG_DIR / "base44_guard.json"


class Base44Client:
    """Client for central usage tracking and rate limit enforcement on Base44."""

    def __init__(
        self,
        config_mgr: Optional[ConfigManager] = None,
        base_url: str = DEFAULT_BASE44_URL,
        app_id: str = DEFAULT_APP_ID,
        lovable_url: Optional[str] = None
    ):
        self.config_mgr = config_mgr or ConfigManager()
        self.base_url = (self.config_mgr.get("base44_url") or base_url).rstrip("/")
        self.app_id = self.config_mgr.get("base44_app_id") or app_id
        self.lovable_url = self.config_mgr.get("lovable_url") or lovable_url
        
        self.device_name = self._get_device_name()
        self.heartbeat_url = f"{self.base_url}/api/apps/{self.app_id}/functions/heartbeat"

        self._lock = threading.Lock()
        self._bg_thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        
        # State
        self.max_usage = 1000
        self.api_token = ""
        self.status = "active"
        self.last_sync_time: Optional[float] = None
        self.last_sync_str = "Never"
        self.last_error: Optional[str] = None
        self.current_usage = 0
        
        self._load_local_state()

    def _get_device_name(self) -> str:
        """Resolve stable OS-level device name."""
        try:
            return socket.gethostname() or os.uname().nodename
        except Exception:
            return "osint"

    def _load_local_state(self):
        """Load cached guard state from disk."""
        if GUARD_FILE.exists():
            try:
                with open(GUARD_FILE, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    self.max_usage = data.get("max_usage", 1000)
                    self.api_token = data.get("api_token", "")
                    self.status = data.get("status", "active")
                    self.current_usage = data.get("current_usage", 0)
                    self.last_sync_time = data.get("last_sync_time")
                    if self.last_sync_time:
                        self.last_sync_str = datetime.datetime.fromtimestamp(self.last_sync_time).strftime("%H:%M:%S")
            except Exception:
                pass

    def _save_local_state(self):
        """Persist guard state to disk so it survives restarts."""
        try:
            GUARD_FILE.parent.mkdir(parents=True, exist_ok=True)
            data = {
                "device_name": self.device_name,
                "current_usage": self.current_usage,
                "max_usage": self.max_usage,
                "api_token": "***" if self.api_token else "",
                "status": self.status,
                "last_sync_time": self.last_sync_time,
                "last_sync_str": self.last_sync_str,
                "base_url": self.base_url,
                "app_id": self.app_id
            }
            with open(GUARD_FILE, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)
        except Exception:
            pass

    def send_heartbeat(self, usage: Optional[int] = None) -> Dict[str, Any]:
        """
        Send usage heartbeat to Base44 with exponential backoff.
        POST {base_url}/api/apps/{app_id}/functions/heartbeat
        body: { "deviceName": "<hostname>", "usage": <counter> }
        """
        if usage is not None:
            self.current_usage = usage
        else:
            # Pull lifetime or session tokens, plus global Groq tokens
            telemetry = self.config_mgr.get_total_telemetry()
            tot = telemetry.get("total_tokens", 0)
            try:
                from claude_replica.groq_usage import get_groq_tracker
                groq_tot = get_groq_tracker(self.config_mgr).total_tokens
                tot = max(tot, groq_tot)
            except Exception:
                pass
            self.current_usage = tot

        payload = {
            "deviceName": self.device_name,
            "usage": self.current_usage
        }

        headers = {
            "Content-Type": "application/json",
            "X-App-Id": self.app_id,
            "Base44-Functions-Version": "prod",
            "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
        }

        delays = [1.0, 2.0, 4.0]
        resp_data = None
        last_exc = None

        for attempt in range(len(delays) + 1):
            try:
                with httpx.Client(timeout=10.0) as client:
                    resp = client.post(self.heartbeat_url, json=payload, headers=headers)
                    if resp.status_code == 200:
                        resp_data = resp.json()
                        break
                    else:
                        last_exc = f"HTTP {resp.status_code}: {resp.text[:100]}"
            except Exception as e:
                last_exc = str(e)

            if attempt < len(delays):
                time.sleep(delays[attempt])

        with self._lock:
            now = time.time()
            if resp_data and isinstance(resp_data, dict):
                self.max_usage = resp_data.get("maxUsage", self.max_usage)
                self.api_token = resp_data.get("apiToken", "")
                self.status = resp_data.get("status", "active")
                self.last_sync_time = now
                self.last_sync_str = datetime.datetime.fromtimestamp(now).strftime("%H:%M:%S")
                self.last_error = None
                self._save_local_state()

                # Step 5: If apiToken is present and lovable_url is set, mirror to lovable.dev
                if self.api_token and self.lovable_url:
                    self._forward_to_lovable()

                return {
                    "success": True,
                    "max_usage": self.max_usage,
                    "status": self.status,
                    "usage": self.current_usage,
                    "device_name": self.device_name,
                    "synced_at": self.last_sync_str
                }
            else:
                self.last_error = last_exc
                return {
                    "success": False,
                    "error": str(last_exc),
                    "status": self.status,
                    "max_usage": self.max_usage,
                    "usage": self.current_usage,
                    "device_name": self.device_name
                }

    def _forward_to_lovable(self):
        """Mirror telemetry to lovable.dev endpoint if apiToken delivered by heartbeat."""
        if not self.lovable_url or not self.api_token:
            return

        payload = {
            "deviceId": self.device_name,
            "usage": self.current_usage,
            "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat()
        }
        headers = {
            "Authorization": f"Bearer {self.api_token}",
            "Content-Type": "application/json"
        }
        try:
            with httpx.Client(timeout=8.0) as client:
                client.post(f"{self.lovable_url.rstrip('/')}/usage", json=payload, headers=headers)
        except Exception:
            pass

    def start_background_loop(self, interval_sec: int = 120):
        """Start daemon thread reporting heartbeats every 2 minutes (120s)."""
        if self._bg_thread is not None and self._bg_thread.is_alive():
            return

        self._stop_event.clear()

        def _worker():
            # Initial heartbeat immediately on start
            self.send_heartbeat()

            while not self._stop_event.is_set():
                # Sleep interval in small chunks for responsive shutdown
                for _ in range(int(interval_sec)):
                    if self._stop_event.is_set():
                        break
                    time.sleep(1)

                if not self._stop_event.is_set():
                    self.send_heartbeat()

        self._bg_thread = threading.Thread(target=_worker, daemon=True, name="Base44GuardHeartbeat")
        self._bg_thread.start()

    def stop_background_loop(self):
        """Stop daemon heartbeat thread."""
        self._stop_event.set()
        if self._bg_thread:
            self._bg_thread = None

    def get_status(self) -> Dict[str, Any]:
        """Return real-time Base44 guard status."""
        with self._lock:
            is_over = (self.status == "over_limit") or (self.max_usage > 0 and self.current_usage >= self.max_usage)
            return {
                "base_url": self.base_url,
                "app_id": self.app_id,
                "device_name": self.device_name,
                "current_usage": self.current_usage,
                "max_usage": self.max_usage,
                "status": "over_limit" if is_over else self.status,
                "is_over_limit": is_over,
                "last_sync_time": self.last_sync_time,
                "last_sync_str": self.last_sync_str,
                "last_error": self.last_error
            }


_GLOBAL_BASE44: Optional[Base44Client] = None


def get_base44_client(config_mgr: Optional[ConfigManager] = None) -> Base44Client:
    """Singleton getter for Base44 UsageGuard client."""
    global _GLOBAL_BASE44
    if _GLOBAL_BASE44 is None:
        _GLOBAL_BASE44 = Base44Client(config_mgr=config_mgr)
    return _GLOBAL_BASE44
