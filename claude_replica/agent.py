"""
Autonomous Agent Loop for Coder / Claude Code Replica.
Orchestrates tool execution, Claude model resolution, safety permissions, and automatic Document saving.
"""

import os
import time
import datetime
from typing import Dict, Any, Optional, List

from claude_replica.config import ConfigManager
from claude_replica.memory import ProjectContext, ConversationManager
from claude_replica.providers import get_provider, LLMResponse
from claude_replica.tools import ToolManager, OPENAI_TOOLS
from claude_replica import ui


def is_small_prompt(prompt: str) -> bool:
    """
    Determine whether a user prompt is a 'small prompt' (e.g., greetings, short questions,
    quick lookups, status queries, brief clarifications) that should NOT trigger thinking models,
    chain-of-thought, reasoning analysis boxes, or deep research spinners.
    """
    if not prompt:
        return True

    text = prompt.strip()
    if not text:
        return True

    # Multi-line inputs or code fences indicate non-trivial instructions/code
    if "```" in text or text.count("\n") >= 3:
        return False

    # Stack traces or error dumps require full analysis
    lower = text.lower().strip()
    error_markers = (
        "traceback (most recent call last):",
        "fatal error:",
        "exception:",
        "syntaxerror:",
        "typeerror:",
        "referenceerror:",
        "segmentation fault",
    )
    if any(marker in lower for marker in error_markers):
        return False

    # Standard greetings and conversational pleasantries (always zero-thinking fast path)
    greetings = {
        "hi", "hello", "hey", "sup", "yo", "good morning", "good evening", "good afternoon",
        "howdy", "greetings", "salut", "hola", "how are you", "how are you doing", "hows it going",
        "how's it going", "what's up", "whats up", "who are you", "what are you", "what can you do",
        "introduce yourself", "tell me about yourself", "thanks", "thank you", "thx",
        "thank you very much", "bye", "goodbye", "see ya", "cya", "cool", "nice", "awesome",
        "great", "ok", "okay", "yes", "no", "y", "n", "sure", "got it", "understood",
        "test", "testing", "ping", "pong", "help", "who made you", "what is your name",
        "who created you", "are you there", "are you ready", "can you help", "can you help me"
    }

    # Clean punctuation to match greetings like "hi!", "hello.", "how are you?"
    cleaned_lower = "".join(c for c in lower if c.isalnum() or c.isspace()).strip()
    if cleaned_lower in greetings:
        return True

    words = text.split()
    word_count = len(words)
    char_count = len(text)

    # Complex engineering task keywords that warrant reasoning when enabled
    complex_triggers = {
        "architect", "refactor", "debug", "solve", "prove", "analyze",
        "redesign", "optimize", "benchmark", "diagnose", "investigate",
        "step by step", "deep dive", "implement a", "build a", "create a",
        "write a full", "full stack", "scaffold", "migration", "derive",
        "generate a full", "make an app", "make a website", "write code for"
    }

    for trigger in complex_triggers:
        if trigger in lower:
            return False

    # Math or arithmetic queries
    math_prefixes = ("what is 2", "what's 2", "2+", "2-", "2*", "2/", "calculate ", "math ", "say ")
    if any(lower.startswith(p) for p in math_prefixes):
        return True

    # Short queries with <= 18 words and <= 120 characters without complex triggers
    if word_count <= 18 and char_count <= 120:
        return True

    return False


class AgentSession:
    def __init__(self, config_mgr: ConfigManager, workspace_dir: Optional[str] = None):
        self.config_mgr = config_mgr
        self.workspace_dir = workspace_dir
        self.project_context = ProjectContext(workspace_dir)
        self.tool_manager = ToolManager(workspace_dir)
        
        self.system_prompt = self.project_context.build_system_prompt()
        self.conversation = ConversationManager(self.system_prompt, workspace_dir=workspace_dir)
        
        # Session metrics
        self.session_id = f"sess_{int(time.time())}_{os.getpid()}"
        self.session_start_time = time.time()
        self.session_input_tokens = 0
        self.session_output_tokens = 0
        self.session_cost = 0.0
        self.session_duration = 0.0
        self.turns_history: List[Dict[str, Any]] = []

        # Initialize session tracking for the live Usage Shower JS app
        prov = self.config_mgr.get("provider", "groq")
        m_name = self.config_mgr.get_display_model_name(provider=prov)
        self.config_mgr.init_session_usage(self.session_id, m_name, prov, self.workspace_dir)
        
        # Base44 UsageGuard integration (https://claudecode.base44.app)
        from claude_replica.base44 import get_base44_client
        self.base44_client = get_base44_client(self.config_mgr)
        if self.config_mgr.get("base44_enabled", True):
            self.base44_client.start_background_loop(interval_sec=120)
        
        # Permissions
        self.always_allow_session = self.config_mgr.get("dangerously_skip_permissions", False)

        # Pre-warm Antigravity provider in background for sub-second first turns
        if self.config_mgr.get("provider", "antigravity") in ("antigravity", "agy"):
            try:
                from claude_replica.providers import AntigravityCLIProvider
                m = self.config_mgr.resolve_backend_model(provider="antigravity")
                AntigravityCLIProvider.prewarm(m)
            except Exception:
                pass

    def refresh_context(self):
        """Update system prompt with latest git status or CLAUDE.md changes."""
        self.system_prompt = self.project_context.build_system_prompt()
        self.conversation.system_prompt = self.system_prompt

    def permission_checker(self, action_type: str, detail: str) -> bool:
        """Evaluate if tool execution is authorized."""
        if self.always_allow_session or self.config_mgr.get("auto_confirm", False):
            return True
        
        if self.config_mgr.get("dangerously_skip_permissions", False):
            return True

        ask_before_editing = self.config_mgr.get("ask_before_editing", True)
        # Require user confirmation before writing or editing files
        if action_type in ["edit_file", "write_file", "create_file"] and ask_before_editing:
            choice = ui.prompt_permission(action_type, detail)
            if choice == "a":
                self.always_allow_session = True
                return True
            return choice == "y"

        mode = self.config_mgr.get("permission_mode", "auto")
        if mode == "auto" and action_type not in ["bash"]:
            return True
            
        choice = ui.prompt_permission(action_type, detail)
        if choice == "a":
            self.always_allow_session = True
            return True
        return choice == "y"

    def run_turn(self, user_prompt: str, max_steps: int = 25):
        """Execute a full autonomous agent turn with tool calling."""
        if self.config_mgr.get("insane_mode", False):
            max_steps = 100
        
        provider_name = self.config_mgr.get("provider", "antigravity")
        user_model_choice = self.config_mgr.get("model", "gemini-3.8-flash-low")

        allowed, remaining, limit = self.config_mgr.check_rate_limit()
        if not allowed and provider_name != "antigravity":
            wait_s = max(0, (self.config_mgr.tier_start_time + 3600) - time.time())
            if wait_s > 0:
                time.sleep(min(wait_s, 1.0))

        self.config_mgr.record_prompt_usage()

        # Check Base44 UsageGuard limit from https://claudecode.base44.app
        if self.config_mgr.get("base44_enabled", True):
            guard_info = self.base44_client.get_status()
            if guard_info.get("is_over_limit"):
                if self.config_mgr.get("base44_enforce", False):
                    return
                else:
                    pass

        self.conversation.add_user_message(user_prompt)
        self.refresh_context()

        # Resolve to backend model and display name
        backend_model = self.config_mgr.resolve_backend_model(user_model_choice, provider=provider_name)
        display_model = self.config_mgr.get_display_model_name(user_model_choice, provider=provider_name)
        
        max_tokens = self.config_mgr.get("max_tokens", 4096)
        temp = self.config_mgr.get("temperature", 0.2)

        # Check if the prompt qualifies for the zero-thinking instant path for small prompts
        is_small = is_small_prompt(user_prompt)
        think_on_small = self.config_mgr.get("think_on_small_prompts", False)

        # Thinking is OFF by default. Only enabled if explicitly turned on in config or insane/boost mode.
        thinking_enabled = self.config_mgr.get("thinking_enabled", False)

        # For small prompts (greetings, simple questions, short lookups), NEVER think unless explicitly requested
        if is_small and not think_on_small:
            thinking_enabled = False
            # Route heavy/thinking models to high-speed non-thinking counterparts for instant sub-second reply
            if provider_name in ("antigravity", "agy"):
                if any(k in backend_model for k in ("high", "medium", "thinking", "opus", "sonnet")):
                    backend_model = "gemini-3.8-flash-low"
            elif provider_name in ("omniroute", "omni"):
                if backend_model not in ("auto/fast", "groq/openai/gpt-oss-20b"):
                    backend_model = "auto/fast"
            elif provider_name in ("gemini", "google"):
                if "pro" in backend_model:
                    backend_model = "gemini-3.5-flash-lite"
            elif provider_name == "groq":
                if "120b" in backend_model:
                    backend_model = "qwen/qwen3.8-27b"

        if thinking_enabled or (self.config_mgr.get("insane_mode", False) and not is_small):
            if self.config_mgr.get("insane_mode", False):
                ui.print_deep_research_indicator("MAX FOCUS MODE: Deep Analysis & Verification Active")
                max_tokens = max(max_tokens, 16384)
                temp = min(temp, 0.05)
            else:
                ui.print_deep_research_indicator(user_prompt)
                max_tokens = max(max_tokens, 8192)
                temp = min(temp, 0.15)

        try:
            provider = get_provider(provider_name, self.config_mgr.config)
        except Exception as e:
            ui.print_error(f"Failed to initialize provider '{provider_name}': {e}")
            return

        self.config_mgr.set_agent_state("loading")
        turn_start = time.time()
        turn_in_tokens = 0
        turn_out_tokens = 0
        turn_tools_called: List[str] = []

        for step in range(1, max_steps + 1):
            messages = self.conversation.get_messages_for_llm()
            
            streamed_tokens = []
            thinking_tokens = []
            thinking_printed = [False]
            in_think_tag = [False]
            is_super_thinking = [False]
            think_tag_buffer = []

            def on_thinking(chunk: str):
                if thinking_enabled:
                    thinking_tokens.append(chunk)

            spinner_label = f"Responding... ({display_model})" if (is_small and not think_on_small) else f"Processing request... ({display_model})"
            with ui.status_spinner(spinner_label) as spinner:
                def on_token(token: str):
                    # Handle tag-based thinking <think>...</think>
                    if "<super_think>" in token or "<super_thinking>" in token:
                        in_think_tag[0] = True
                        is_super_thinking[0] = True
                        token = token.replace("<super_think>", "").replace("<super_thinking>", "")
                    elif "<think>" in token or "<thinking>" in token:
                        in_think_tag[0] = True
                        is_super_thinking[0] = False
                        token = token.replace("<think>", "").replace("<thinking>", "")

                    if in_think_tag[0]:
                        if "</think>" in token or "</thinking>" in token or "</super_think>" in token or "</super_thinking>" in token:
                            sep = next((t for t in ["</think>", "</thinking>", "</super_think>", "</super_thinking>"] if t in token), "</think>")
                            before, after = token.split(sep, 1)
                            think_tag_buffer.append(before)
                            t_str = "".join(think_tag_buffer).strip()
                            think_tag_buffer.clear()
                            in_think_tag[0] = False
                            if thinking_enabled and t_str and not thinking_printed[0]:
                                try:
                                    spinner.stop()
                                except Exception:
                                    pass
                                ui.print_thinking(t_str, super_thinking=is_super_thinking[0])
                                thinking_printed[0] = True
                            token = after
                        else:
                            think_tag_buffer.append(token)
                            return

                    if not token:
                        return

                    # If thinking is enabled and there is separate thinking/reasoning accumulated, flush it
                    if thinking_enabled and thinking_tokens and not thinking_printed[0]:
                        t_str = "".join(thinking_tokens).strip()
                        thinking_tokens.clear()
                        if t_str:
                            try:
                                spinner.stop()
                            except Exception:
                                pass
                            ui.print_thinking(t_str, super_thinking=is_super_thinking[0])
                            thinking_printed[0] = True

                    if not streamed_tokens:
                        try:
                            spinner.stop()
                        except Exception:
                            pass
                        self.config_mgr.set_agent_state("answering")
                        ui.start_streaming_content()

                    streamed_tokens.append(token)
                    ui.stream_token(token)

                system_prompt_to_use = self.system_prompt
                if is_small and not think_on_small:
                    system_prompt_to_use += (
                        "\n\n[DIRECTIVE: The user prompt is a small/simple query or greeting. "
                        "Respond directly, concisely, and immediately. Do NOT include any thinking, reasoning, "
                        "chain-of-thought, or <think> tags.]"
                    )

                try:
                    response: LLMResponse = provider.complete(
                        system_prompt=system_prompt_to_use,
                        messages=messages,
                        tools=OPENAI_TOOLS,
                        model=backend_model,
                        max_tokens=max_tokens,
                        temperature=temp,
                        stream=True,
                        stream_callback=on_token,
                        thinking_callback=on_thinking if thinking_enabled else None
                    )
                except Exception as e:
                    ui.print_error(f"Inference error on step {step}: {e}")
                    break

            turn_in_tokens += response.input_tokens
            turn_out_tokens += response.output_tokens

            # Finalize streaming if content was streamed live
            if streamed_tokens:
                ui.end_streaming_content()

            # Show thinking only if thinking_enabled is True
            if thinking_enabled and response.thinking and not thinking_printed[0]:
                ui.print_thinking(response.thinking)
                thinking_printed[0] = True
            elif thinking_enabled and response.tool_calls and response.content and response.content.strip() and not thinking_printed[0] and not streamed_tokens:
                ui.print_thinking(response.content.strip())
                thinking_printed[0] = True

            # Check for tool calls
            if response.tool_calls:
                tc_dicts = [tc.to_dict() for tc in response.tool_calls]
                self.conversation.add_assistant_message(response.content, tool_calls=tc_dicts)

                if response.thinking and response.content and response.content.strip() and not streamed_tokens:
                    ui.print_assistant_message(response.content)

                # Execute all requested tools
                for tc in response.tool_calls:
                    turn_tools_called.append(tc.name)
                    ui.print_tool_call(tc.name, tc.arguments)
                    t_start = time.time()
                    
                    tool_res = self.tool_manager.dispatch(
                        tc.name,
                        tc.arguments,
                        permission_checker=self.permission_checker
                    )
                    t_dur = time.time() - t_start
                    
                    ui.print_tool_result(
                        tc.name,
                        success=tool_res.success,
                        output=tool_res.output,
                        duration=t_dur
                    )
                    
                    path_arg = tc.arguments.get("file_path") or tc.arguments.get("path")
                    self.conversation.add_tool_result(
                        tool_call_id=tc.id,
                        tool_name=tc.name,
                        output=tool_res.output,
                        path_arg=path_arg
                    )
                    
                    if not tool_res.success and self.config_mgr.get("insane_mode", False):
                        # Inject self-correction guidance
                        guidance = f"[INSANE MODE AUTONOMOUS GUIDANCE] The tool '{tc.name}' failed. You must immediately formulate a new plan to debug or fix the issue. Use bash to inspect the environment if necessary."
                        self.conversation.messages.append({"role": "user", "content": guidance})

                continue

            else:
                # Final response reached
                if response.content:
                    if not streamed_tokens:
                        ui.print_assistant_message(response.content)
                self.conversation.add_assistant_message(response.content)
                break

        turn_duration = time.time() - turn_start
        self.session_input_tokens += turn_in_tokens
        self.session_output_tokens += turn_out_tokens
        self.session_duration += turn_duration
        
        # Save complete session transcript directly to project sessions/ folder
        saved_file = None
        saved_path = self.conversation.save_session(display_model)
        if saved_path:
            saved_file = str(saved_path)

        # Learn and store insights from this turn in persistent memory
        try:
            resp_str = response.content if "response" in locals() and response else ""
            self.project_context.memory.learn_from_turn(user_prompt, resp_str or "", turn_tools_called)
        except Exception:
            pass

        # Record lifetime telemetry for /total
        self.config_mgr.record_telemetry(turn_in_tokens, turn_out_tokens, turn_duration)

        # Record turn for the real-time Usage Shower JS app
        turn_data = {
            "turn_index": len(self.turns_history) + 1,
            "timestamp": time.time(),
            "time_str": datetime.datetime.now().strftime("%H:%M:%S"),
            "prompt": user_prompt,
            "model": display_model,
            "backend_model": backend_model,
            "input_tokens": turn_in_tokens,
            "output_tokens": turn_out_tokens,
            "total_tokens": turn_in_tokens + turn_out_tokens,
            "credits_used": round((turn_in_tokens + turn_out_tokens) / 1000.0, 3),
            "duration": round(turn_duration, 2),
            "tools_called": list(set(turn_tools_called)),
            "saved_file": saved_file
        }
        self.turns_history.append(turn_data)
        self.config_mgr.record_turn_to_session(turn_data)

        # Real-time auto-sync to Base44 UsageGuard (https://claudecode.base44.app)
        if self.config_mgr.get("base44_enabled", True):
            import threading
            tot = self.config_mgr.get_total_telemetry()
            threading.Thread(target=self.base44_client.send_heartbeat, args=(tot.get("total_tokens", 0),), daemon=True).start()

        ui.print_stats(turn_in_tokens, turn_out_tokens, turn_duration, cost=0.0, saved_path=saved_file)
        self.config_mgr.set_agent_state("idle")

        # Auto-push to Github if enabled
        if self.conversation.has_code_activity and self.config_mgr.get("auto_push", False):
            try:
                import subprocess
                subprocess.run(["git", "add", "."], cwd=self.workspace_dir, capture_output=True)
                msg = f"Auto-commit: Clawd task completed ({datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')})"
                res = subprocess.run(["git", "commit", "-m", msg], cwd=self.workspace_dir, capture_output=True)
                if res.returncode == 0:
                    ui.print_info("Auto-committed changes to Git.")
                    push_res = subprocess.run(["git", "push"], cwd=self.workspace_dir, capture_output=True)
                    if push_res.returncode == 0:
                        ui.print_success("Auto-pushed updates to GitHub!")
                    else:
                        ui.print_error("Failed to auto-push to GitHub. Ensure remote is configured.")
            except Exception as e:
                ui.print_error(f"Auto-push failed: {e}")
