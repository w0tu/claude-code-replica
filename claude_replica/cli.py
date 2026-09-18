"""
Command Line Interface and Interactive REPL for Coder / Claude Code Replica.
Features terminal clear on startup, Clawd animations, Claude model mapping, and Documents logging.
"""

import os
import sys
import time
import argparse
import shutil
from pathlib import Path

from prompt_toolkit import PromptSession
from prompt_toolkit.history import FileHistory
from prompt_toolkit.completion import Completer, Completion
from prompt_toolkit.styles import Style
from prompt_toolkit.formatted_text import HTML
from prompt_toolkit.key_binding import KeyBindings

from claude_replica.config import ConfigManager, CONFIG_DIR
from claude_replica.agent import AgentSession
from claude_replica.commands import handle_command
from claude_replica import ui

VERSION = "2.1.265"

# Categorized Registry matching Claude Code v2.1 (Screenshots 1, 4, 5)
COMMAND_REGISTRY = [
    ("/add-dir", "command", "Add a new working directory"),
    ("/add-language-rules", "skill", "Workflow command scaffold for add-language-rules in everything-claude-code."),
    ("/add-repo", "command", "Clone and attach a GitHub repository to session memory"),
    ("/agents", "command", "(removed) Ask Claude to create/manage subagents, or edit .claude/agents/"),
    ("/artifact-diagramming", "skill", "Diagramming know-how for Artifacts - when a picture earns its place, how to draw one that shows the real mechanism, and the inline-SVG mechanics that keep it legible in both themes."),
    ("/autocompact", "command", "Set how full the context gets before auto-summarizing"),
    ("/background", "command", "Send this session to the background and free the terminal"),
    ("/batch", "skill", "Research and plan a large-scale change, then execute it in parallel across 5-30 isolated worktree agents that each open a PR."),
    ("/branch", "command", "Create a branch of the current conversation at this point"),
    ("/btw", "command", "Ask a quick side question without interrupting the main conversation"),
    ("/bug", "command", "Report a bug or share your conversation"),
    ("/cd", "command", "Move this session to a new working directory"),
    ("/claude-api", "skill", "Reference for the Claude API / Anthropic SDK - model ids, pricing, params, streaming, tool use, MCP, agents, caching, token counting, model migration."),
    ("/clear", "command", "Start a new session with empty context; previous session stays on disk (resumable with /resume)"),
    ("/clone", "command", "Clone remote Git repository into workspace repos directory"),
    ("/code-review", "skill", "Review the current diff, or a PR number/branch/path target, for correctness bugs and reuse/simplification/efficiency cleanups at the given effort level..."),
    ("/color", "command", "Set the prompt bar color for this session"),
    ("/commit-push-pr", "skill", "Commit, push, and open a PR"),
    ("/compact", "command", "Free up context by summarizing the conversation so far"),
    ("/config", "command", "Open settings"),
    ("/cost", "command", "Show token usage and cost metrics for current session"),
    ("/doctor", "command", "Check system health and tool configuration"),
    ("/ecc", "command", "Inspect Everything Claude Code (ECC) framework integration"),
    ("/ecc-pro-security-roadmap", "workflow", "Survey + web-research + triage both ECC and AgentShield, then synthesize a prioritized ECC Pro security roadmap"),
    ("/effort", "command", "Set effort level for model usage"),
    ("/exit", "command", "Exit the CLI"),
    ("/export", "command", "Export the current conversation to a file or clipboard"),
    ("/fast", "command", "Toggle fast mode (Opus 5)"),
    ("/feature-development", "skill", "Workflow command scaffold for feature-development in everything-claude-code."),
    ("/feedback", "command", "Send feedback to Anthropic or report a bug"),
    ("/fewer-permission-prompts", "skill", "Scan your transcripts for common read-only Bash and MCP tool calls, then add a prioritized allowlist to project .claude/settings.json to reduce permission prompts."),
    ("/find-duplicate-issues", "skill", "Find duplicate GitHub issues"),
    ("/focus", "command", "Toggle focus view: just your prompt, summary, and response"),
    ("/fork", "command", "Copy this conversation into a new background session and keep working here"),
    ("/github", "command", "Inspect GitHub remote, branches, PRs, and issues"),
    ("/goal", "command", "Set a goal Claude checks before stopping"),
    ("/groq", "command", "Global Groq token telemetry and live quota"),
    ("/help", "command", "Show help and available commands"),
    ("/hooks", "command", "View hook configurations for tool events"),
    ("/ide", "command", "Manage IDE integrations and show status"),
    ("/init", "skill", "Initialize a new CLAUDE.md file with codebase documentation"),
    ("/insights", "skill", "Generate a report analyzing your Claude Code sessions"),
    ("/install-github-app", "command", "Set up Claude GitHub Actions for a repository"),
    ("/issue", "command", "View or inspect GitHub issues for repository"),
    ("/keybindings", "command", "Open your keyboard shortcuts file"),
    ("/list-agents", "command", "List subagents, teammates, and other Claude sessions you can message"),
    ("/login", "command", "Sign in to your AI provider account"),
    ("/logout", "command", "Sign out and clear stored credentials"),
    ("/mcp", "command", "View and configure MCP servers"),
    ("/memory", "command", "Inspect persistent project memory and directives"),
    ("/model", "command", "Switch Claude/GPT model backend"),
    ("/permissions", "command", "View and modify allowed tool permissions"),
    ("/plugins", "command", "Browse, list, and inspect plugins from marketplace"),
    ("/pr", "command", "View, diff, or inspect branch pull requests"),
    ("/provider", "command", "Switch AI provider backend (groq, anthropic, openai)"),
    ("/repo", "command", "Manage attached repositories (add, list, remove, status)"),
    ("/resume", "command", "Resume a previous session from disk"),
    ("/review", "command", "Review git changes and inspect diffs"),
    ("/rules", "command", "View loaded coding rules and guardrails"),
    ("/sessions", "command", "View background and active sessions"),
    ("/spinner", "command", "Switch retro ASCII spinner animation"),
    ("/status", "command", "Show current session status, model, and git branch"),
    ("/thinking", "command", "Toggle reasoning/thinking mode (on/off)"),
    ("/total", "command", "Display lifetime credits and token telemetry"),
    ("/triage-github-issues", "skill", "Triage GitHub issues by analyzing and applying labels"),
    ("/usage", "command", "Launch real-time usage shower web monitor"),
    ("/version", "command", "Show version information"),
    ("/workflow", "command", "List and run ECC workflow commands"),
]

SLASH_COMMANDS = [cmd for cmd, _, _ in COMMAND_REGISTRY]


class ClaudeCodeCompleter(Completer):
    """Two-column autocompleter matching Claude Code CLI dropdown UI (Screenshots 1, 4, 5)."""
    def __init__(self, registry):
        self.registry = registry
        self._file_cache = []
        self._last_cache_time = 0

    def _get_files(self):
        if time.time() - self._last_cache_time > 5:
            try:
                import subprocess
                res = subprocess.run(["git", "ls-files"], capture_output=True, text=True, timeout=2)
                if res.returncode == 0:
                    self._file_cache = [f for f in res.stdout.splitlines() if f]
                else:
                    self._file_cache = [os.path.relpath(os.path.join(r, f), '.') for r, d, fs in os.walk('.') if '.git' not in r for f in fs]
            except Exception:
                pass
            self._last_cache_time = time.time()
        return self._file_cache

    def get_completions(self, document, complete_event):
        text = document.text_before_cursor
        if text.startswith('/'):
            query = text.lower()
            # Calculate alignment width
            col_width = 28
            for cmd, category, desc in self.registry:
                if cmd.lower().startswith(query) or (len(query) > 1 and query[1:] in cmd.lower()):
                    display_text = f"{cmd:<{col_width}}"
                    meta_text = f"{category:<8} · {desc}"
                    yield Completion(
                        text=cmd,
                        start_position=-len(text),
                        display=display_text,
                        display_meta=meta_text
                    )
        elif '@' in text:
            idx = text.rfind('@')
            prefix = text[idx+1:]
            for f in self._get_files():
                if f.startswith(prefix):
                    yield Completion(f, start_position=-len(prefix), display=f"[file] {f}")


def create_prompt_session(session: Optional[AgentSession] = None, session_mode_ref: Optional[List[bool]] = None) -> PromptSession:
    """Create prompt_toolkit session with Claude Code v2.1 styling, shortcuts, and completion menu."""
    if session_mode_ref is None:
        session_mode_ref = [False]

    history_file = CONFIG_DIR / "history.txt"
    completer = ClaudeCodeCompleter(COMMAND_REGISTRY)

    style = Style.from_dict({
        "prompt": "#ffffff bold",
        "bottom-toolbar": "#8c8c8c bg:default",
        "completion-menu": "bg:default",
        "completion-menu.completion": "fg:#c9d1d9 bg:default",
        "completion-menu.completion.current": "fg:#58a6ff bg:#1f293d bold",
        "completion-menu.meta.completion": "fg:#6e7681 bg:default",
        "completion-menu.meta.completion.current": "fg:#7aa2f7 bg:#1f293d",
        "scrollbar.background": "bg:default",
        "scrollbar.button": "bg:#58a6ff",
    })

    last_active = [time.time()]

    def get_bottom_toolbar():
        width = shutil.get_terminal_size().columns
        sep = "─" * max(20, width - 1)
        if session_mode_ref[0]:
            line1 = f'<style fg="#333333">{sep}</style>'
            line2 = '<style fg="#8c8c8c">ctrl+r to rename       ctrl+j for newline     ctrl+t to pin to top     ctrl+x to stop     ? to close\nctrl+s to switch views  @ to mention          alt+1 to open            esc to quit</style>'
            return HTML(f"{line1}\n{line2}")
        else:
            line1 = f'<style fg="#333333">{sep}</style>'
            line2 = '<style fg="#8c8c8c">" manual mode on · ← for agents</style>'
            return HTML(f"{line1}\n{line2}")

    kb = KeyBindings()

    @kb.add('escape', 'enter')
    @kb.add('c-j')
    def _(event):
        """Insert a newline on Esc+Enter or Ctrl+J for multiline editing."""
        last_active[0] = time.time()
        event.current_buffer.insert_text('\n')

    @kb.add('c-s')
    def _(event):
        """Toggle session handoff view on Ctrl+S (Screenshot 1 & 2)."""
        session_mode_ref[0] = not session_mode_ref[0]
        ui.clear_terminal()
        if session:
            ui.print_claude_code_header(
                version=VERSION,
                model_display=session.config_mgr.get_display_model_name(),
                workspace=session.workspace_dir
            )
        if session_mode_ref[0]:
            ui.print_session_dashboard()
            ui.print_task_handoff_notice()
        event.app.invalidate()

    @kb.add('escape')
    def _(event):
        """Exit session mode and return to chat on Escape."""
        if session_mode_ref[0]:
            session_mode_ref[0] = False
            ui.clear_terminal()
            if session:
                ui.print_claude_code_header(
                    version=VERSION,
                    model_display=session.config_mgr.get_display_model_name(),
                    workspace=session.workspace_dir
                )
            event.app.invalidate()

    prompt_session = PromptSession(
        history=FileHistory(str(history_file)),
        completer=completer,
        complete_while_typing=True,
        key_bindings=kb,
        style=style,
        bottom_toolbar=get_bottom_toolbar,
        refresh_interval=1.0
    )

    setattr(prompt_session, "reset_idle_timer", lambda: last_active.__setitem__(0, time.time()))

    def _on_text_changed(buf):
        last_active[0] = time.time()

    prompt_session.default_buffer.on_text_changed += _on_text_changed

    return prompt_session


def main():
    parser = argparse.ArgumentParser(
        prog="coder",
        description="Coder / Claude Code Replica — Interactive Agentic Coding CLI"
    )
    parser.add_argument("prompt", nargs="*", help="Direct prompt to execute")
    parser.add_argument("-p", "--print", action="store_true", help="Non-interactive mode (print response and exit)")
    parser.add_argument("-m", "--model", help="Set model (claude-3-7-sonnet, fable-5, fable-5.1, etc.)")
    parser.add_argument("--provider", help="Set provider (groq, anthropic, openai)")
    parser.add_argument("--effort", help="Set effort tier (extra high, high, medium, normal, low)")
    parser.add_argument("--total", action="store_true", help="Display lifetime usage and credits telemetry")
    parser.add_argument("--groq", "--global", dest="groq_usage", action="store_true", help="Display global Groq token usage and live rate-limit quota")
    parser.add_argument("--usage", "--monitor", action="store_true", help="Launch real-time Usage Shower web monitor in browser")
    parser.add_argument("-y", "--auto", "--auto-confirm", dest="auto_confirm", action="store_true", help="Auto-confirm all file edits and commands")
    parser.add_argument("--dangerously-skip-permissions", action="store_true", help="Auto-approve all tool actions")
    parser.add_argument("--thinking", choices=["on", "off"], help="Enable or disable thinking/reasoning mode (default: off)")
    parser.add_argument("--spinner", help="Set ASCII spinner (crt_bar, pipe, braille, dots, variation_1, etc.)")
    parser.add_argument("--insane", "--focus", action="store_true", dest="insane", help="Enable Maximum Focus mode: Autonomous auto-fixing and max reasoning depth")
    parser.add_argument("--boost", action="store_true", help="Enable Boost mode (Alias for Maximum Focus)")
    parser.add_argument("--no-animation", action="store_true", help="Skip startup animation")
    parser.add_argument("-d", "--dir", default=os.getcwd(), help="Target workspace directory")
    parser.add_argument("-v", "--version", action="version", version=f"Coder / Claude Code Replica v{VERSION}")

    args = parser.parse_args()

    # Load configuration
    cfg = ConfigManager()
    if args.model:
        cfg.set("model", args.model)
    if args.provider:
        cfg.set("provider", args.provider)
    if args.effort:
        cfg.set_effort(args.effort)
    if args.auto_confirm:
        cfg.set("auto_confirm", True)
    if args.dangerously_skip_permissions:
        cfg.set("dangerously_skip_permissions", True)
    if args.thinking:
        cfg.set("thinking_enabled", args.thinking.lower() == "on")
    if args.spinner:
        cfg.set("spinner", args.spinner)
    if getattr(args, "insane", False) or getattr(args, "boost", False):
        import time
        print("\n\033[1;36m[SYSTEM] Maximum Focus Mode Activated: Autonomous Error Correction & Max Effort Enabled\033[0m\n")
        cfg.set("insane_mode", True)
        cfg.set("auto_confirm", True)
        cfg.set("dangerously_skip_permissions", True)
        cfg.set_effort("extra high")
        time.sleep(1)

    target_dir = os.path.abspath(args.dir)
    if "higgsfield" in target_dir.lower():
        target_dir = "/home/feds/Projects/claude-code-replica"
    session = AgentSession(config_mgr=cfg, workspace_dir=target_dir)

    # 0. Total telemetry check
    if args.total:
        handle_command("/total", session)
        sys.exit(0)

    # 0a. Global Groq usage check
    if args.groq_usage:
        handle_command("/groq", session)
        sys.exit(0)

    # 0b. Real-time Usage Shower web monitor launch
    if args.usage:
        handle_command("/usage web", session)
        sys.exit(0)

    # 1. Non-interactive direct prompt mode
    if args.print or args.prompt:
        prompt_text = " ".join(args.prompt) if args.prompt else sys.stdin.read().strip()
        if not prompt_text:
            ui.print_error("No prompt provided.")
            sys.exit(1)
        session.run_turn(prompt_text)
        sys.exit(0)

    # 2. Interactive REPL mode:
    # Auto-start Usage Shower server in background for real-time remote monitoring
    try:
        from claude_replica.usage_server import start_usage_server
        start_usage_server(config_mgr=cfg, in_background=True)
    except Exception:
        pass

    # Clear screen on start and play Clawd welcome animation
    ui.clear_terminal()

    git_info = session.project_context.get_git_info()
    disp_model = cfg.get_display_model_name()
    effort_name = cfg.get("effort", "normal")
    auto_conf = cfg.get("auto_confirm", False)
    _, remaining, limit = cfg.check_rate_limit()
    
    ui.animate_welcome_banner(
        version=VERSION,
        model_display=disp_model,
        workspace=target_dir,
        git_branch=git_info.get("branch"),
        effort_name=effort_name,
        prompts_remaining=remaining,
        prompts_limit=limit,
        auto_confirm=auto_conf,
        skip_animation=args.no_animation,
        font_style=cfg.get("font_style", "modern")
    )

    session_mode = [False]
    prompt_session = create_prompt_session(session=session, session_mode_ref=session_mode)

    while True:
        try:
            if hasattr(prompt_session, "reset_idle_timer"):
                prompt_session.reset_idle_timer()

            prompt_html = HTML('<b>❯</b> ')
            if session_mode[0]:
                placeholder = HTML('<style fg="#555555">describe a task for a new session</style>')
            else:
                placeholder = HTML('<style fg="#555555">Ask anything, @ to mention, / for actions</style>')

            user_input = prompt_session.prompt(
                prompt_html,
                placeholder=placeholder
            ).strip()

            if hasattr(prompt_session, "reset_idle_timer"):
                prompt_session.reset_idle_timer()

            if not user_input:
                continue

            if session_mode[0]:
                session_mode[0] = False
                ui.clear_terminal()
                ui.print_claude_code_header(
                    version=VERSION,
                    model_display=disp_model,
                    workspace=target_dir,
                    git_branch=git_info.get("branch")
                )

            if user_input.startswith("/"):
                cmd_lower = user_input.lower().strip()
                if cmd_lower in ["/sessions", "/background"]:
                    session_mode[0] = not session_mode[0]
                    ui.clear_terminal()
                    ui.print_claude_code_header(
                        version=VERSION,
                        model_display=disp_model,
                        workspace=target_dir,
                        git_branch=git_info.get("branch")
                    )
                    if session_mode[0]:
                        ui.print_session_dashboard()
                        ui.print_task_handoff_notice()
                    continue

                keep_running = handle_command(user_input, session)
                if not keep_running:
                    break
            else:
                session.run_turn(user_input)

        except KeyboardInterrupt:
            ui.console.print("\n[dim]Prompt cancelled. (Press Ctrl+D or type /exit to quit)[/dim]")
            continue
        except EOFError:
            ui.console.print("\n[dim]Goodbye![/dim]")
            break
        except Exception as e:
            ui.print_error(f"Unexpected error: {e}")


if __name__ == "__main__":
    main()
