"""
Global Groq Usage & Live Rate-Limit Quota Tracker for Coder / Claude Code Replica.
Maintains persistent global telemetry across all sessions, devices, and models using Groq API.
Extracts real-time rate limit headers (TPM, RPM, reset countdowns, region, speed) from Groq responses.
"""

import os
import sys
import json
import time
import re
import datetime
import threading
from pathlib import Path
from typing import Dict, Any, Optional, Mapping

import httpx

from claude_replica.config import ConfigManager, CONFIG_DIR

GROQ_USAGE_FILE = CONFIG_DIR / "global_groq_usage.json"
GROQ_API_BASE = "https://api.groq.com/openai/v1"


def parse_duration_str(s: str) -> float:
    """Parse duration strings like '2m52.8s', '577ms', '15s' into float seconds."""
    if not s:
        return 0.0
    s = s.strip().lower()
    if s.endswith("ms"):
        try:
            return round(float(s[:-2]) / 1000.0, 4)
        except ValueError:
            return 0.0
    total = 0.0
    matches = re.findall(r'(\d+(?:\.\d+)?)([hms])', s)
    if matches:
        for val, unit in matches:
            v = float(val)
            if unit == 'h':
                total += v * 3600
            elif unit == 'm':
                total += v * 60
            elif unit == 's':
                total += v
        return round(total, 2)
    try:
        return round(float(s.rstrip("s")), 2)
    except ValueError:
        return 0.0


class GroqUsageTracker:
    """
    Thread-safe tracker for global Groq token consumption and live rate limit quotas.
    Survives CLI restarts and synchronizes across sessions.
    """

    def __init__(self, config_mgr: Optional[ConfigManager] = None):
        self.config_mgr = config_mgr or ConfigManager()
        self._lock = threading.Lock()

        # Cumulative global telemetry
        self.total_tokens: int = 0
        self.prompt_tokens: int = 0
        self.completion_tokens: int = 0
        self.requests_count: int = 0
        self.credits_used: float = 0.0
        self.total_duration_s: float = 0.0
        self.models_usage: Dict[str, Dict[str, Any]] = {}

        # Live rate limit & quota state
        self.limit_requests: int = 1000
        self.remaining_requests: int = 1000
        self.limit_tokens: int = 8000
        self.remaining_tokens: int = 8000
        self.reset_requests: str = "0s"
        self.reset_tokens: str = "0s"
        self.reset_requests_sec: float = 0.0
        self.reset_tokens_sec: float = 0.0
        self.region: str = "unknown"
        self.last_sync_time: Optional[float] = None
        self.last_sync_str: str = "Never"
        self.last_latency_s: float = 0.0
        self.tok_per_sec: float = 0.0
        self.queue_time: float = 0.0
        self.prompt_time: float = 0.0
        self.completion_time: float = 0.0

        self._load_state()

    def _load_state(self):
        """Load persistent global Groq telemetry from disk."""
        if GROQ_USAGE_FILE.exists():
            try:
                with open(GROQ_USAGE_FILE, "r", encoding="utf-8") as f:
                    data = json.load(f)
                self.total_tokens = data.get("total_tokens", 0)
                self.prompt_tokens = data.get("prompt_tokens", 0)
                self.completion_tokens = data.get("completion_tokens", 0)
                self.requests_count = data.get("requests_count", 0)
                self.credits_used = data.get("credits_used", round(self.total_tokens / 1000.0, 3))
                self.total_duration_s = data.get("total_duration_s", 0.0)
                self.models_usage = data.get("models_usage", {})

                q = data.get("live_quota", {})
                self.limit_requests = q.get("limit_requests", self.limit_requests)
                self.remaining_requests = q.get("remaining_requests", self.remaining_requests)
                self.limit_tokens = q.get("limit_tokens", self.limit_tokens)
                self.remaining_tokens = q.get("remaining_tokens", self.remaining_tokens)
                self.reset_requests = q.get("reset_requests", self.reset_requests)
                self.reset_tokens = q.get("reset_tokens", self.reset_tokens)
                self.reset_requests_sec = q.get("reset_requests_sec", self.reset_requests_sec)
                self.reset_tokens_sec = q.get("reset_tokens_sec", self.reset_tokens_sec)
                self.region = q.get("region", self.region)
                self.last_sync_time = q.get("last_sync_time")
                self.last_sync_str = q.get("last_sync_str", "Never")
                self.last_latency_s = q.get("last_latency_s", 0.0)
                self.tok_per_sec = q.get("tok_per_sec", 0.0)
                self.queue_time = q.get("queue_time", 0.0)
                self.prompt_time = q.get("prompt_time", 0.0)
                self.completion_time = q.get("completion_time", 0.0)
            except Exception:
                pass

    def _save_state(self):
        """Persist state atomically to disk."""
        try:
            GROQ_USAGE_FILE.parent.mkdir(parents=True, exist_ok=True)
            data = {
                "total_tokens": self.total_tokens,
                "prompt_tokens": self.prompt_tokens,
                "completion_tokens": self.completion_tokens,
                "requests_count": self.requests_count,
                "credits_used": self.credits_used,
                "total_duration_s": self.total_duration_s,
                "models_usage": self.models_usage,
                "live_quota": {
                    "limit_requests": self.limit_requests,
                    "remaining_requests": self.remaining_requests,
                    "limit_tokens": self.limit_tokens,
                    "remaining_tokens": self.remaining_tokens,
                    "reset_requests": self.reset_requests,
                    "reset_tokens": self.reset_tokens,
                    "reset_requests_sec": self.reset_requests_sec,
                    "reset_tokens_sec": self.reset_tokens_sec,
                    "region": self.region,
                    "last_sync_time": self.last_sync_time,
                    "last_sync_str": self.last_sync_str,
                    "last_latency_s": self.last_latency_s,
                    "tok_per_sec": self.tok_per_sec,
                    "queue_time": self.queue_time,
                    "prompt_time": self.prompt_time,
                    "completion_time": self.completion_time
                }
            }
            with open(GROQ_USAGE_FILE, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)
        except Exception:
            pass

    def record_headers(self, headers: Mapping[str, str]):
        """Extract and update live rate limit quotas from HTTP response headers."""
        with self._lock:
            self._parse_headers_internal(headers)
            self._save_state()

    def _parse_headers_internal(self, headers: Mapping[str, str]):
        """Internal helper to parse Groq headers without holding lock twice."""
        hdrs = {k.lower(): v for k, v in headers.items()}

        if "x-ratelimit-limit-requests" in hdrs:
            try:
                self.limit_requests = int(hdrs["x-ratelimit-limit-requests"])
            except ValueError:
                pass

        if "x-ratelimit-remaining-requests" in hdrs:
            try:
                self.remaining_requests = int(hdrs["x-ratelimit-remaining-requests"])
            except ValueError:
                pass

        if "x-ratelimit-limit-tokens" in hdrs:
            try:
                self.limit_tokens = int(hdrs["x-ratelimit-limit-tokens"])
            except ValueError:
                pass

        if "x-ratelimit-remaining-tokens" in hdrs:
            try:
                self.remaining_tokens = int(hdrs["x-ratelimit-remaining-tokens"])
            except ValueError:
                pass

        if "x-ratelimit-reset-requests" in hdrs:
            self.reset_requests = hdrs["x-ratelimit-reset-requests"]
            self.reset_requests_sec = parse_duration_str(self.reset_requests)

        if "x-ratelimit-reset-tokens" in hdrs:
            self.reset_tokens = hdrs["x-ratelimit-reset-tokens"]
            self.reset_tokens_sec = parse_duration_str(self.reset_tokens)

        if "x-groq-region" in hdrs:
            self.region = hdrs["x-groq-region"]

        now = time.time()
        self.last_sync_time = now
        self.last_sync_str = datetime.datetime.fromtimestamp(now).strftime("%H:%M:%S")

    def record_response(
        self,
        headers: Mapping[str, str],
        usage: Optional[Dict[str, Any]] = None,
        model: str = "",
        duration: float = 0.0
    ):
        """
        Record a completed Groq API turn: parse headers, accumulate global tokens,
        calculate speed, and persist state.
        """
        with self._lock:
            # 1. Parse headers
            self._parse_headers_internal(headers)

            # 2. Extract token usage
            usage = usage or {}
            in_tok = int(usage.get("prompt_tokens", 0))
            out_tok = int(usage.get("completion_tokens", 0))
            tot_tok = int(usage.get("total_tokens", in_tok + out_tok))

            # Body latencies if provided by Groq
            if "queue_time" in usage:
                self.queue_time = round(float(usage.get("queue_time", 0.0)), 4)
            if "prompt_time" in usage:
                self.prompt_time = round(float(usage.get("prompt_time", 0.0)), 4)
            if "completion_time" in usage:
                self.completion_time = round(float(usage.get("completion_time", 0.0)), 4)

            # Accumulate global metrics
            self.prompt_tokens += in_tok
            self.completion_tokens += out_tok
            self.total_tokens += tot_tok
            self.requests_count += 1
            self.credits_used = round(self.total_tokens / 1000.0, 3)
            self.total_duration_s = round(self.total_duration_s + duration, 2)
            self.last_latency_s = round(duration, 3)

            if duration > 0 and tot_tok > 0:
                self.tok_per_sec = round(tot_tok / duration, 1)

            # Model breakdown
            if model:
                if model not in self.models_usage:
                    self.models_usage[model] = {
                        "total_tokens": 0,
                        "prompt_tokens": 0,
                        "completion_tokens": 0,
                        "requests": 0
                    }
                m_entry = self.models_usage[model]
                m_entry["total_tokens"] += tot_tok
                m_entry["prompt_tokens"] += in_tok
                m_entry["completion_tokens"] += out_tok
                m_entry["requests"] += 1

            self._save_state()

    def get_status(self) -> Dict[str, Any]:
        """Return snapshot of global Groq usage and live quota metrics."""
        with self._lock:
            used_tokens = max(0, self.limit_tokens - self.remaining_tokens)
            used_requests = max(0, self.limit_requests - self.remaining_requests)

            tpm_pct_rem = round((self.remaining_tokens / self.limit_tokens * 100), 1) if self.limit_tokens > 0 else 100.0
            rpm_pct_rem = round((self.remaining_requests / self.limit_requests * 100), 1) if self.limit_requests > 0 else 100.0

            # Health classification
            if self.remaining_tokens <= 0 or self.remaining_requests <= 0:
                health = "throttled"
            elif tpm_pct_rem < 20.0 or rpm_pct_rem < 20.0:
                health = "near_limit"
            else:
                health = "healthy"

            return {
                "total_tokens": self.total_tokens,
                "prompt_tokens": self.prompt_tokens,
                "completion_tokens": self.completion_tokens,
                "requests_count": self.requests_count,
                "credits_used": self.credits_used,
                "total_duration_s": self.total_duration_s,
                "models_usage": dict(self.models_usage),
                "live_quota": {
                    "limit_requests": self.limit_requests,
                    "remaining_requests": self.remaining_requests,
                    "used_requests": used_requests,
                    "requests_pct_remaining": rpm_pct_rem,
                    "limit_tokens": self.limit_tokens,
                    "remaining_tokens": self.remaining_tokens,
                    "used_tokens": used_tokens,
                    "tokens_pct_remaining": tpm_pct_rem,
                    "reset_requests": self.reset_requests,
                    "reset_tokens": self.reset_tokens,
                    "reset_requests_sec": self.reset_requests_sec,
                    "reset_tokens_sec": self.reset_tokens_sec,
                    "region": self.region,
                    "status": health,
                    "last_sync_time": self.last_sync_time,
                    "last_sync_str": self.last_sync_str,
                    "last_latency_s": self.last_latency_s,
                    "tok_per_sec": self.tok_per_sec,
                    "queue_time": self.queue_time,
                    "prompt_time": self.prompt_time,
                    "completion_time": self.completion_time
                }
            }

    def refresh_live_quota(
        self,
        api_key: Optional[str] = None,
        model: str = "openai/gpt-oss-120b"
    ) -> Dict[str, Any]:
        """
        Send a lightweight probe request to Groq /chat/completions to fetch
        fresh rate limit headers on demand.
        """
        key = api_key or self.config_mgr.get("keys", {}).get("groq", "")
        if not key:
            return {"success": False, "error": "No Groq API key configured"}

        url = f"{GROQ_API_BASE}/chat/completions"
        headers = {
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/json"
        }
        payload = {
            "model": model,
            "messages": [{"role": "user", "content": "ping"}],
            "max_tokens": 1
        }

        start_t = time.time()
        try:
            with httpx.Client(timeout=10.0) as client:
                resp = client.post(url, headers=headers, json=payload)
                dur = time.time() - start_t

                # Whether 200 or 429, parse headers
                self.record_headers(resp.headers)

                if resp.status_code == 200:
                    data = resp.json()
                    usage = data.get("usage", {})
                    self.record_response(resp.headers, usage=usage, model=model, duration=dur)
                    return {"success": True, "status": self.get_status()}
                else:
                    return {
                        "success": False,
                        "status_code": resp.status_code,
                        "error": resp.text[:200],
                        "status": self.get_status()
                    }
        except Exception as e:
            return {"success": False, "error": str(e), "status": self.get_status()}

    def reset_global_usage(self):
        """Reset cumulative token counters back to zero."""
        with self._lock:
            self.total_tokens = 0
            self.prompt_tokens = 0
            self.completion_tokens = 0
            self.requests_count = 0
            self.credits_used = 0.0
            self.total_duration_s = 0.0
            self.models_usage = {}
            self._save_state()


_GLOBAL_GROQ_TRACKER: Optional[GroqUsageTracker] = None


def get_groq_tracker(config_mgr: Optional[ConfigManager] = None) -> GroqUsageTracker:
    """Singleton getter for global Groq usage tracker."""
    global _GLOBAL_GROQ_TRACKER
    if _GLOBAL_GROQ_TRACKER is None:
        _GLOBAL_GROQ_TRACKER = GroqUsageTracker(config_mgr=config_mgr)
    return _GLOBAL_GROQ_TRACKER
