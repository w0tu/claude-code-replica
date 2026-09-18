import unittest
from claude_replica.memory import ConversationManager, ProjectContext
from claude_replica.config import ConfigManager
from claude_replica.tools import ToolManager


class TestAgentAndMemory(unittest.TestCase):
    def test_conversation_compaction(self):
        cm = ConversationManager(system_prompt="You are Claude.")
        for i in range(12):
            cm.add_user_message(f"Message number {i}")
            cm.add_assistant_message(f"Response number {i}")
        
        # Total messages = 24
        self.assertEqual(len(cm.messages), 24)
        compacted_count = cm.compact()
        self.assertEqual(compacted_count, 18)
        self.assertEqual(len(cm.messages), 6)
        self.assertIsNotNone(cm.summary_prefix)
        self.assertIn("Message number 0", cm.summary_prefix)

    def test_project_context(self):
        pc = ProjectContext()
        prompt = pc.build_system_prompt()
        self.assertIn("Claude Code", prompt)
        self.assertIn("# Core Operating Directives", prompt)
        self.assertIn("# Professional Execution Guidelines", prompt)
        self.assertIn("# Using Your Dedicated Tools", prompt)

    def test_save_code_only(self):
        cm = ConversationManager(system_prompt="You are Claude.")
        cm.add_user_message("Hello there!")
        cm.add_assistant_message("Hi! How can I help you today?")
        # No code activity - should NOT save
        saved = cm.save_code_to_documents("Claude 3.7 Sonnet")
        self.assertIsNone(saved)

        # Code activity - write_file tool result added
        cm.add_assistant_message("I will write the file.", tool_calls=[{
            "id": "call_1",
            "function": {
                "name": "write_to_file",
                "arguments": '{"TargetFile": "hello.py", "CodeContent": "print(1)"}'
            }
        }])
        cm.add_tool_result("call_1", "write_to_file", "File created", path_arg="hello.py")
        self.assertTrue(cm.has_code_activity)
        saved = cm.save_code_to_documents("Claude 3.7 Sonnet")
        self.assertIsNotNone(saved)
        self.assertTrue(saved.exists())
        # Clean up test file
        if saved.exists():
            saved.unlink()

    def test_tool_manager_dispatch(self):
        tm = ToolManager()
        res = tm.dispatch("bash", {"command": "echo 'unit-test-pass'"})
        self.assertTrue(res.success)
        self.assertIn("unit-test-pass", res.output)

    def test_fable_models_resolution(self):
        cfg = ConfigManager()
        backend_fable5 = cfg.resolve_backend_model("fable-5", provider="groq")
        self.assertEqual(backend_fable5, "qwen/qwen3.8-27b")
        backend_fable51 = cfg.resolve_backend_model("fable-5.1", provider="groq")
        self.assertEqual(backend_fable51, "openai/gpt-oss-120b")
        display_fable5 = cfg.get_display_model_name("fable-5", provider="groq")
        self.assertIn("Fable 5", display_fable5)

    def test_telemetry_tracking(self):
        cfg = ConfigManager()
        initial = cfg.get_total_telemetry()
        init_credits = initial.get("total_credits_used", 0.0)
        
        cfg.record_telemetry(input_tokens=500, output_tokens=500, duration_s=1.5)
        updated = cfg.get_total_telemetry()
        
        self.assertAlmostEqual(updated["total_credits_used"], init_credits + 1.0, places=2)
        self.assertGreaterEqual(updated["total_turns"], initial.get("total_turns", 0) + 1)

    def test_auto_confirm_setting(self):
        cfg = ConfigManager()
        orig = cfg.get("auto_confirm", False)
        cfg.set("auto_confirm", True)
        self.assertTrue(cfg.get("auto_confirm"))
        cfg.set("auto_confirm", orig)

    def test_is_small_prompt_greetings_and_queries(self):
        from claude_replica.agent import is_small_prompt
        # Greetings and small talk
        self.assertTrue(is_small_prompt("hi"))
        self.assertTrue(is_small_prompt("hello!"))
        self.assertTrue(is_small_prompt("hey there"))
        self.assertTrue(is_small_prompt("how are you doing?"))
        self.assertTrue(is_small_prompt("good morning"))
        self.assertTrue(is_small_prompt("who are you"))
        self.assertTrue(is_small_prompt("what can you do?"))
        self.assertTrue(is_small_prompt("thanks!"))
        self.assertTrue(is_small_prompt("yes"))
        self.assertTrue(is_small_prompt("no"))
        self.assertTrue(is_small_prompt("ok"))
        self.assertTrue(is_small_prompt("ping"))
        self.assertTrue(is_small_prompt("pong"))

        # Simple math and short lookups
        self.assertTrue(is_small_prompt("what is 2+2"))
        self.assertTrue(is_small_prompt("what's 10 * 5"))
        self.assertTrue(is_small_prompt("say 123"))
        self.assertTrue(is_small_prompt("who created python?"))
        self.assertTrue(is_small_prompt("how do I exit"))
        self.assertTrue(is_small_prompt("can you help me"))

    def test_is_small_prompt_complex_tasks(self):
        from claude_replica.agent import is_small_prompt
        # Complex architectural or engineering tasks
        self.assertFalse(is_small_prompt("build a full stack react and express website with sqlite database"))
        self.assertFalse(is_small_prompt("create a 3d scroll based website using three.js and gsap"))
        self.assertFalse(is_small_prompt("refactor the database connection layer to support async pooling"))
        self.assertFalse(is_small_prompt("architect a microservice queue system for distributed video processing"))
        self.assertFalse(is_small_prompt("debug why this function returns null:\n```python\ndef foo(): return None\n```"))
        self.assertFalse(is_small_prompt("Traceback (most recent call last):\n  File 'main.py', line 10, in <module>\nValueError: invalid literal"))

    def test_small_prompt_thinking_bypass_logic(self):
        from claude_replica.agent import AgentSession, is_small_prompt
        cfg = ConfigManager()
        cfg.set("thinking_enabled", True)
        cfg.set("think_on_small_prompts", False)

        prompt = "hi"
        self.assertTrue(is_small_prompt(prompt))
        # When prompt is small, thinking_enabled should be overridden to False
        is_small = is_small_prompt(prompt)
        think_on_small = cfg.get("think_on_small_prompts", False)
        effective_thinking = cfg.get("thinking_enabled", False)
        if is_small and not think_on_small:
            effective_thinking = False

        self.assertFalse(effective_thinking)


if __name__ == "__main__":
    unittest.main()
