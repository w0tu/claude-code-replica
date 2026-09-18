import unittest
import time
from claude_replica.providers import (
    GeminiRateTracker,
    HybridSmartProvider,
    BaseProvider,
    LLMResponse,
    ToolCallItem
)


class MockProvider(BaseProvider):
    def __init__(self, name: str, should_fail: bool = False, fail_code: int = 429):
        self.name = name
        self.should_fail = should_fail
        self.fail_code = fail_code
        self.calls = []

    def complete(self, system_prompt, messages, tools, model, max_tokens=4096, temperature=0.2, stream=True, stream_callback=None, thinking_callback=None):
        self.calls.append({"model": model, "messages": messages})
        if self.should_fail:
            raise RuntimeError(f"API Error ({self.fail_code}): Rate limit or quota exceeded")
        if stream_callback:
            stream_callback(f"response from {self.name}")
        return LLMResponse(content=f"response from {self.name}", active_model=model)


class TestHybridSmartProvider(unittest.TestCase):
    def setUp(self):
        self.tracker = GeminiRateTracker.get_instance()
        self.tracker.request_timestamps.clear()
        self.tracker.cooldown_until = 0.0

    def test_tracker_rate_limiting(self):
        self.assertFalse(self.tracker.is_near_limit())
        # Record 12 requests within window
        for _ in range(12):
            self.tracker.record_request()
        self.assertTrue(self.tracker.is_near_limit())
        self.assertEqual(self.tracker.get_recent_rpm(), 12)

    def test_tracker_cooldown(self):
        self.assertFalse(self.tracker.is_in_cooldown())
        self.tracker.trigger_cooldown(10.0)
        self.assertTrue(self.tracker.is_in_cooldown())
        self.assertTrue(self.tracker.is_near_limit())

    def test_hybrid_normal_flow_uses_primary(self):
        gemini_mock = MockProvider("gemini")
        groq_mock = MockProvider("groq")
        hybrid = HybridSmartProvider("gemini", gemini_mock, "groq", groq_mock, {})

        resp = hybrid.complete("sys", [{"role": "user", "content": "hi"}], [], "gemini-3.5-flash-lite")
        self.assertEqual(resp.content, "response from gemini")
        self.assertEqual(len(gemini_mock.calls), 1)
        self.assertEqual(len(groq_mock.calls), 0)

    def test_hybrid_near_limit_routes_to_groq(self):
        gemini_mock = MockProvider("gemini")
        groq_mock = MockProvider("groq")
        hybrid = HybridSmartProvider("gemini", gemini_mock, "groq", groq_mock, {})

        # Simulate near limit condition
        self.tracker.trigger_cooldown(20.0)
        self.assertTrue(self.tracker.is_near_limit())

        resp = hybrid.complete("sys", [{"role": "user", "content": "hi"}], [], "gemini-3.5-flash-lite")
        self.assertEqual(resp.content, "response from groq")
        self.assertEqual(len(gemini_mock.calls), 0)
        self.assertEqual(len(groq_mock.calls), 1)
        # Verify model was adapted to groq
        self.assertEqual(groq_mock.calls[0]["model"], "openai/gpt-oss-120b")

    def test_hybrid_runtime_429_failover(self):
        gemini_mock = MockProvider("gemini", should_fail=True, fail_code=429)
        groq_mock = MockProvider("groq")
        hybrid = HybridSmartProvider("gemini", gemini_mock, "groq", groq_mock, {})

        resp = hybrid.complete("sys", [{"role": "user", "content": "hi"}], [], "gemini-3.5-flash-lite")
        self.assertEqual(resp.content, "response from groq")
        self.assertEqual(len(gemini_mock.calls), 1)
        self.assertEqual(len(groq_mock.calls), 1)
        self.assertTrue(self.tracker.is_in_cooldown())


if __name__ == "__main__":
    unittest.main()
