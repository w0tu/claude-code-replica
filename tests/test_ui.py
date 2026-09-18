import unittest
from pathlib import Path
from claude_replica import ui
from claude_replica.config import ConfigManager
from claude_replica.agent import AgentSession
from claude_replica.commands import handle_command


class TestUIAnimationsAndRendering(unittest.TestCase):
    def setUp(self):
        self.config_mgr = ConfigManager()
        self.session = AgentSession(config_mgr=self.config_mgr)

    def test_render_welcome_panel(self):
        art, col, _ = ui.CLAWD_FRAMES[-1]
        panel = ui.render_welcome_panel(
            clawd_art=art,
            border_color=col,
            version="2.1.0",
            model_display="Claude 3.7 Sonnet",
            workspace="/tmp",
            git_branch="main",
            effort_name="normal",
            prompts_remaining=40,
            prompts_limit=40,
            auto_confirm=False
        )
        self.assertIsNotNone(panel)

    def test_animate_welcome_banner_skip(self):
        ui.animate_welcome_banner(
            version="2.1.0",
            model_display="Claude 3.7 Sonnet",
            workspace="/tmp",
            skip_animation=True
        )

    def test_gif_loading_and_fallback(self):
        frames = ui.load_gif_ascii_frames(ui.JAM_GIF_PATHS, width=18, height=9)
        self.assertIsInstance(frames, list)
        if frames:
            self.assertGreater(len(frames), 0)
            self.assertIsInstance(frames[0], str)

        proc_frames = ui.load_gif_ascii_frames(ui.PROCESSING_GIF_PATHS, width=16, height=8)
        self.assertIsInstance(proc_frames, list)
        if proc_frames:
            self.assertGreater(len(proc_frames), 0)
            self.assertIsInstance(proc_frames[0], str)

    def test_status_spinner_context(self):
        with ui.status_spinner("Testing spinner message") as spinner:
            self.assertIsNotNone(spinner)

    def test_thinking_rendering(self):
        ui.print_thinking("Let us break down the problem into smaller subproblems.")
        ui.print_thinking("")

    def test_streaming_lifecycle(self):
        ui.start_streaming_content()
        ui.stream_token("Hello ")
        ui.stream_token("world!")
        ui.end_streaming_content()

    def test_idle_and_dance_commands(self):
        res_idle = handle_command("/idle", self.session)
        self.assertTrue(res_idle)

        res_dance = handle_command("/dance", self.session)
        self.assertTrue(res_dance)

    def test_stats_and_messages(self):
        ui.print_stats(100, 50, 0.42, cost=0.0, saved_path=None)
        ui.print_info("Testing info message with special chars [brackets]")
        ui.print_success("Testing success message [brackets]")
        ui.print_error("Testing error message with unexpected [tag]")

    def test_alerts_and_deep_research(self):
        ui.print_alert("note", "Important Note", "This is an informational note.")
        ui.print_alert("tip", "Pro Tip", "Here is a tip.")
        ui.print_alert("warning", "Caution", "Proceed with care.")
        ui.print_deep_research_indicator("build a full-stack react and tailwind application")

    def test_font_and_memory_commands(self):
        res_font = handle_command("/font cyber", self.session)
        self.assertTrue(res_font)

        res_mem = handle_command("/memory", self.session)
        self.assertTrue(res_mem)

        res_ufile = handle_command("/usage-file", self.session)
        self.assertTrue(res_ufile)

    def test_retro_spinners_and_animations(self):
        from claude_replica import animations
        spinners = animations.list_spinners()
        self.assertIn("crt_bar", spinners)
        self.assertIn("pipe", spinners)
        self.assertIn("braille", spinners)
        
        frames = animations.get_spinner_frames("crt_bar")
        self.assertGreater(len(frames), 0)

        anims = animations.list_animations()
        self.assertIn("dna", anims)
        self.assertIn("apple", anims)

        anim_frames = animations.load_3a_animation("dna")
        self.assertGreater(len(anim_frames), 0)

    def test_new_commands(self):
        # /spinner
        res_spin = handle_command("/spinner pipe", self.session)
        self.assertTrue(res_spin)
        self.assertEqual(self.config_mgr.get("spinner"), "pipe")

        # /thinking
        res_th = handle_command("/thinking on", self.session)
        self.assertTrue(res_th)
        self.assertTrue(self.config_mgr.get("thinking_enabled"))
        res_th2 = handle_command("/thinking off", self.session)
        self.assertTrue(res_th2)
        self.assertFalse(self.config_mgr.get("thinking_enabled"))

        # /thinking small
        res_th_s1 = handle_command("/thinking small on", self.session)
        self.assertTrue(res_th_s1)
        self.assertTrue(self.config_mgr.get("think_on_small_prompts"))
        res_th_s2 = handle_command("/thinking small off", self.session)
        self.assertTrue(res_th_s2)
        self.assertFalse(self.config_mgr.get("think_on_small_prompts"))

        # /plugins
        res_plug = handle_command("/plugins", self.session)
        self.assertTrue(res_plug)


if __name__ == "__main__":
    unittest.main()
