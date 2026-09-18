"""
UI and Terminal Rendering for Coder / Claude Code Replica.
Replicates Anthropic Claude Code's visual design, Clawd mascot animations, colored diffs, and formatting.
"""

import os
import sys
import time
import shutil
import difflib
from pathlib import Path
from typing import Optional, Dict, Any, List
from contextlib import contextmanager

from rich.console import Console
from rich.panel import Panel
from rich.text import Text
from rich.markdown import Markdown
from rich.syntax import Syntax
from rich.table import Table
from rich.prompt import Prompt
from rich.columns import Columns
from rich.live import Live
import rich.spinner

# Register retro ASCII spinners from claude-code-spinner
from claude_replica.animations import SPINNER_DEFINITIONS, play_ascii_animation
for _s_name, _s_data in SPINNER_DEFINITIONS.items():
    rich.spinner.SPINNERS[_s_name] = _s_data
rich.spinner.SPINNERS["clawd"] = SPINNER_DEFINITIONS["crt_bar"]

# Initialize main console
console = Console(highlight=True)

# Theme colors matching Anthropic Claude Code
CLAUDE_ORANGE = "#d97757"
CLAUDE_MUTED = "#8c8c8c"
CLAUDE_BLUE = "#4db8ff"
CLAUDE_GREEN = "#2ecc71"
CLAUDE_RED = "#e74c3c"
CLAUDE_YELLOW = "#f1c40f"
CLAUDE_BORDER = "#404040"

# Authentic Clawd ASCII Art and Animation Frames
# Extracted and adapted from Anthropic's Claude Code (WelcomeV2.tsx)
CLAWD_FRAMES = [
    # (ascii_lines, border_color, sleep_duration)
    (
        "   █████████  \n"
        "  ██-█████-██ \n"
        "   █████████  \n"
        "    █     █   ",
        "#7a4231",
        0.08
    ),
    (
        "   █████████  \n"
        "  ██▄▄████ ██ \n"
        "   █████████  \n"
        "   █ █   █ █  ",
        "#9e523b",
        0.08
    ),
    (
        "   █████████  \n"
        "  ██ ████▄▄██ \n"
        "   █████████  \n"
        "   █ █   █ █  ",
        "#bf6244",
        0.08
    ),
    (
        "   █████████  \n"
        "  ██▄█████▄██ \n"
        "   █████████  \n"
        "    ██   ██   ",
        "#d46f4e",
        0.08
    ),
    (
        "   █████████  \n"
        "  ██▄█████▄██ \n"
        "   █████████  \n"
        "   █ █   █ █  ",
        CLAUDE_ORANGE,
        0.04
    ),
]


def clear_terminal():
    """Clear terminal cross-platform (Linux, macOS, Windows) and reset cursor to row 1, col 1."""
    if os.name == 'nt':
        os.system('cls')
    else:
        # Reset terminal state + clear viewport & scrollback + move cursor to (1,1)
        sys.stdout.write("\033c\033[H\033[2J\033[3J")
        sys.stdout.flush()
        try:
            os.system('clear')
        except Exception:
            pass
    console.clear()


OFFICIAL_CLAUDE_CRAB = [
    "   █████████ ",
    "  ██▄█████▄██",
    "   █████████  ",
    "   █ █   █ █  ",
]


def render_welcome_panel(
    clawd_art: Optional[str] = None,
    border_color: str = CLAUDE_ORANGE,
    version: str = "2.1.265",
    model_display: str = "Sonnet 5",
    workspace: str = "~/Projects/claude-code-replica",
    git_branch: Optional[str] = None,
    effort_name: str = "normal",
    prompts_remaining: int = 40,
    prompts_limit: int = 40,
    auto_confirm: bool = False,
    font_style: str = "modern",
    awaiting: int = 1,
    working: int = 0,
    completed: int = 0
) -> Table:
    """Render the exact borderless Claude Code v2.1 header matching screenshots."""
    home = os.path.expanduser("~")
    display_ws = ("~" + workspace[len(home):]) if workspace.startswith(home) else workspace
    git_text = f" · [dim]git:({git_branch})[/dim]" if git_branch else ""

    crab_str = "\n".join(OFFICIAL_CLAUDE_CRAB)
    colored_crab = f"[{border_color}]{crab_str}[/{border_color}]"

    info_content = (
        f"[bold white]Claude Code[/bold white] [dim]v{version}[/dim]\n"
        f"[dim]{model_display} · {display_ws}{git_text}[/dim]\n"
        f"[dim]{awaiting} awaiting input · {working} working · {completed} completed[/dim]"
    )

    grid = Table.grid(padding=(0, 2))
    grid.add_column(no_wrap=True)
    grid.add_column()
    grid.add_row(colored_crab, info_content)
    return grid


def print_claude_code_header(
    version: str = "2.1.265",
    model_display: str = "Sonnet 5",
    workspace: str = "~/Projects/claude-code-replica",
    git_branch: Optional[str] = None,
    awaiting: int = 1,
    working: int = 0,
    completed: int = 0
):
    """Print the exact Claude Code v2.1 header flush at row 1, col 1."""
    grid = render_welcome_panel(
        version=version,
        model_display=model_display,
        workspace=workspace,
        git_branch=git_branch,
        awaiting=awaiting,
        working=working,
        completed=completed
    )
    console.print(grid)
    console.print()


def print_session_dashboard(
    session_name: str = "claude-code-replica",
    elapsed_time: str = "20m"
):
    """Render the background session management dashboard (Screenshots 1 & 2)."""
    console.print("[dim]Your conversation moved to the background — enter opens it · esc returns to it · ctrl+c twice quits[/dim]\n")
    console.print("[bold white]Needs input[/bold white]")
    console.print("[dim]Sessions that have a question or need your decision land here[/dim]")
    
    width = shutil.get_terminal_size().columns
    pad = max(4, width - 42 - len(session_name) - len(elapsed_time))
    session_line = f"[bold #f5a623]*[/bold #f5a623] [bold white]current session[/bold white]  [white]{session_name}[/white]{' ' * pad}[dim]{elapsed_time}[/dim]"
    console.print(f"[on #1c2128] {session_line} [/on #1c2128]\n")

    console.print("[bold white]Working[/bold white]")
    console.print("[dim]Sessions Claude is actively working on — they keep running even if you close the terminal[/dim]\n")

    console.print("[bold white]Completed[/bold white]")
    console.print("[dim]Finished sessions wait here for you to review[/dim]\n")


def print_task_handoff_notice():
    """Render the task handoff notice from Screenshot 2."""
    console.print("[dim]A different way to work with Claude: hand off a bigger task than you would chat through, and Claude organizes it in the sections above so you know when it needs you.[/dim]\n")


def print_auto_update_notice(message: str = "Auto-update failed: no write permission to npm prefix · Run claude doctor"):
    """Render the subtle error notice from Screenshot 3."""
    console.print(f"[bold red]✖[/bold red] [dim]{message}[/dim]")


_streaming_started = False

DANCING_NOTES = ["♪", "♫", "♬", "♩"]

# GIF Asset Paths
JAM_GIF_PATHS = [
    Path("/home/feds/claude-jam.gif"),
    Path(__file__).parent / "web" / "claude-jam.gif",
    Path(__file__).parent.parent / "claude-jam.gif"
]

PROCESSING_GIF_PATHS = [
    Path("/home/feds/claude-processing.gif"),
    Path(__file__).parent / "web" / "claude-processing.gif",
    Path(__file__).parent.parent / "claude-processing.gif"
]

_GIF_CACHE: Dict[str, List[str]] = {}


def load_gif_ascii_frames(paths: List[Path], width: int = 18, height: int = 9) -> List[str]:
    """Extract and convert GIF frames into terminal ASCII art strings."""
    cache_key = f"{paths[0].name}_{width}x{height}"
    if cache_key in _GIF_CACHE:
        return _GIF_CACHE[cache_key]

    target_path = None
    for p in paths:
        if p.exists():
            target_path = p
            break

    frames = []
    if target_path is not None:
        try:
            from PIL import Image
            with Image.open(target_path) as im:
                for i in range(im.n_frames):
                    im.seek(i)
                    fr = im.convert("RGBA").resize((width, height), Image.Resampling.BILINEAR)
                    lines = []
                    for y in range(height):
                        row = ""
                        for x in range(width):
                            r, g, b, a = fr.getpixel((x, y))
                            if a < 40 or (r < 60 and g < 60 and b < 60):
                                row += " "
                            elif r > 160 and g > 70 and b < 130:
                                row += "█"
                            elif r > 110:
                                row += "▓"
                            else:
                                row += "░"
                        lines.append(row)
                    frames.append("\n".join(lines))
        except Exception:
            frames = []

    if frames:
        _GIF_CACHE[cache_key] = frames
        return frames

    return []


def play_clawd_processing_animation(cycles: int = 1):
    """Play animated ASCII processing sequence from claude-processing.gif."""
    if not sys.stdout.isatty():
        return
    frames = load_gif_ascii_frames(PROCESSING_GIF_PATHS, width=16, height=8)
    if not frames:
        return

    from rich.live import Live
    initial_panel = Panel(
        f"[{CLAUDE_ORANGE}]{frames[0]}[/]",
        border_style=CLAUDE_ORANGE,
        title=f"[bold {CLAUDE_ORANGE}]◈ Clawd is Thinking & Processing...[/]"
    )
    with Live(initial_panel, console=console, refresh_per_second=20, transient=True) as live:
        for _ in range(cycles):
            for fr in frames:
                p = Panel(
                    f"[{CLAUDE_ORANGE}]{fr}[/]\n\n[bold {CLAUDE_ORANGE}]◈ Thinking & synthesizing solution...[/]",
                    border_style=CLAUDE_ORANGE,
                    title=f"[bold {CLAUDE_ORANGE}]◈ Clawd Processing...[/]",
                    padding=(0, 2)
                )
                live.update(p)
                time.sleep(0.04)


def play_clawd_answering_animation():
    """Brief animated retro sequence when starting to answer."""
    if not sys.stdout.isatty():
        return
    frames = load_gif_ascii_frames(JAM_GIF_PATHS, width=18, height=8)
    if not frames:
        return

    from rich.live import Live
    initial_panel = Panel(
        f"[{CLAUDE_ORANGE}]{frames[0]}[/]",
        border_style=CLAUDE_ORANGE,
        title=f"[bold {CLAUDE_ORANGE}]◈ Processing Task...[/]"
    )
    with Live(initial_panel, console=console, refresh_per_second=15, transient=True) as live:
        for idx in range(min(5, len(frames))):
            p = Panel(
                f"[{CLAUDE_ORANGE}]{frames[idx]}[/]\n\n[bold {CLAUDE_ORANGE}]Executing prompt...[/]",
                border_style=CLAUDE_ORANGE,
                title=f"[bold {CLAUDE_ORANGE}]◈ Processing Task[/]",
                padding=(0, 2)
            )
            live.update(p)
            time.sleep(0.06)


def play_idle_animation(idle_seconds: int = 10):
    """Play retro terminal standby animation for idle sessions."""
    if not sys.stdout.isatty():
        console.print(f"[dim {CLAUDE_ORANGE}]◈ System Standby ({idle_seconds}s)[/dim {CLAUDE_ORANGE}]")
        return

    frames = load_gif_ascii_frames(JAM_GIF_PATHS, width=18, height=9)
    from rich.live import Live
    initial_frame = frames[0] if frames else "  █████████  \n  ██▄█████▄██ \n   █████████  \n   █ █   █ █  "
    p_init = Panel(
        f"[{CLAUDE_ORANGE}]{initial_frame}[/]",
        border_style=CLAUDE_ORANGE,
        title=f"[{CLAUDE_ORANGE}]◈ SYSTEM STANDBY[/]"
    )

    colors = ["#d97757", "#e67e22", "#f39c12", "#2ecc71", "#3498db", "#9b59b6"]

    with Live(p_init, console=console, refresh_per_second=10, transient=True) as live:
        for cycle in range(2):
            seq = frames if frames else [
                "  █████████  \n ▄██-█████-██ \n  █████████   \n   █     █    ",
                "  █████████  \n  ██-█████-██▄\n  █████████   \n    █     █   ",
                "   █████████  \n ▄██▄█████▄██ \n  █████████   \n   █ █   █ █  ",
                "  █████████  \n  ██▄█████▄██▄\n  █████████   \n   █ █   █ █  "
            ]
            for i, fr in enumerate(seq):
                col = colors[i % len(colors)]
                p = Panel(
                    f"[{col}]{fr}[/{col}]\n\n[dim]Standby for {idle_seconds}s...[/dim] [bold {col}]Ready[/bold {col}]",
                    border_style=col,
                    title=f"[{col}]◈ SYSTEM STANDBY ({idle_seconds}s idle)[/]",
                    padding=(0, 2)
                )
                live.update(p)
                time.sleep(0.10)

    console.print(f"[dim {CLAUDE_ORANGE}]◈ System Standby ({idle_seconds}s) · Type a prompt or /help to resume[/dim {CLAUDE_ORANGE}]")


def play_clawd_dance(repeats: int = 2):
    """Play OpenASCII animation in the terminal."""
    played = play_ascii_animation("dna", duration_s=2.0)
    if not played:
        console.print("[dim]Terminal animation completed.[/dim]")


def start_streaming_content():
    """Mark beginning of live streamed token answer."""
    global _streaming_started
    _streaming_started = True
    console.print()
    console.print(f"[bold {CLAUDE_ORANGE}]╭─ ◈ Response[/]")


def stream_token(token: str):
    """Write live token to terminal in real time for dynamic answering animation."""
    global _streaming_started
    _streaming_started = True
    sys.stdout.write(token)
    sys.stdout.flush()


def end_streaming_content():
    """Finalize live streaming display."""
    global _streaming_started
    if _streaming_started:
        sys.stdout.write("\n")
        sys.stdout.flush()
        console.print(f"[{CLAUDE_BORDER}]╰─ ◈ End of Response[/{CLAUDE_BORDER}]")
        console.print()
        _streaming_started = False


def animate_welcome_banner(
    version: str,
    model_display: str,
    workspace: str,
    git_branch: Optional[str] = None,
    effort_name: str = "normal",
    prompts_remaining: int = 40,
    prompts_limit: int = 40,
    auto_confirm: bool = False,
    skip_animation: bool = False,
    font_style: str = "modern"
):
    """Render the signature Claude Code v2.1 header matching official CLI."""
    print_claude_code_header(
        version=version,
        model_display=model_display,
        workspace=workspace,
        git_branch=git_branch
    )


def print_welcome_banner(
    version: str,
    model_display: str,
    workspace: str,
    git_branch: Optional[str] = None,
    effort_name: str = "normal",
    prompts_remaining: int = 40,
    prompts_limit: int = 40,
    auto_confirm: bool = False,
    skip_animation: bool = False,
    font_style: str = "modern"
):
    """Render the signature Claude Code / Clawd startup banner with animation."""
    animate_welcome_banner(
        version=version,
        model_display=model_display,
        workspace=workspace,
        git_branch=git_branch,
        effort_name=effort_name,
        prompts_remaining=prompts_remaining,
        prompts_limit=prompts_limit,
        auto_confirm=auto_confirm,
        skip_animation=skip_animation,
        font_style=font_style
    )


@contextmanager
def status_spinner(message: str, spinner: str = "crt_bar"):
    """Show an animated retro ASCII spinner while an action or LLM call is running."""
    s = spinner if spinner in rich.spinner.SPINNERS else "crt_bar"
    with console.status(f"[{CLAUDE_ORANGE}]{message}[/]", spinner=s) as status:
        yield status


def print_tool_call(tool_name: str, args: Dict[str, Any]):
    """Render the tool invocation card in clean professional block style."""
    tool_header = f"[{CLAUDE_ORANGE}]╭─ [•] {tool_name}[/{CLAUDE_ORANGE}]"
    console.print(tool_header)
    
    for key, val in args.items():
        val_str = str(val)
        if len(val_str) > 120:
            val_str = val_str[:117] + "..."
        val_str = val_str.replace("\n", " ⏎ ")
        console.print(f"[{CLAUDE_BORDER}]│[/]  [dim]{key}:[/dim] [white]{val_str}[/white]")


def print_tool_result(tool_name: str, success: bool, output: str, duration: float = 0.0):
    """Render the tool output card matching Claude Code."""
    status_icon = f"[{CLAUDE_GREEN}][OK][/{CLAUDE_GREEN}]" if success else f"[{CLAUDE_RED}][FAIL][/{CLAUDE_RED}]"
    dur_str = f"[dim]({duration:.2f}s)[/dim]" if duration > 0 else ""
    
    lines = output.strip().split("\n")
    max_lines = 16
    if len(lines) > max_lines:
        displayed = lines[:max_lines]
        omitted = len(lines) - max_lines
        for line in displayed:
            console.print(f"[{CLAUDE_BORDER}]│[/]  [dim]{line}[/dim]")
        console.print(f"[{CLAUDE_BORDER}]│[/]  [yellow]... ({omitted} lines hidden) ...[/yellow]")
    elif lines and lines[0]:
        for line in lines:
            console.print(f"[{CLAUDE_BORDER}]│[/]  [dim]{line}[/dim]")
            
    footer = f"[{CLAUDE_BORDER}]╰─ {status_icon} [dim]Finished {tool_name}[/dim] {dur_str}[/{CLAUDE_BORDER}]"
    console.print(footer)
    console.print()


def print_diff(file_path: str, old_text: str, new_text: str):
    """Render a git-style colorized diff for file modifications."""
    diff = list(difflib.unified_diff(
        old_text.splitlines(keepends=True),
        new_text.splitlines(keepends=True),
        fromfile=f"a/{file_path}",
        tofile=f"b/{file_path}",
        n=3
    ))
    
    if not diff:
        console.print(f"[{CLAUDE_MUTED}]No changes detected in {file_path}[/]")
        return

    console.print(f"[{CLAUDE_ORANGE}]╭─ [Diff] [bold]{file_path}[/bold][/{CLAUDE_ORANGE}]")
    for line in diff[2:]:
        line_clean = line.rstrip("\r\n")
        if line_clean.startswith("+"):
            console.print(f"[{CLAUDE_BORDER}]│[/]  [{CLAUDE_GREEN}]{line_clean}[/{CLAUDE_GREEN}]")
        elif line_clean.startswith("-"):
            console.print(f"[{CLAUDE_BORDER}]│[/]  [{CLAUDE_RED}]{line_clean}[/{CLAUDE_RED}]")
        elif line_clean.startswith("@@"):
            console.print(f"[{CLAUDE_BORDER}]│[/]  [{CLAUDE_BLUE}]{line_clean}[/{CLAUDE_BLUE}]")
        else:
            console.print(f"[{CLAUDE_BORDER}]│[/]  [dim]{line_clean}[/dim]")
    console.print(f"[{CLAUDE_BORDER}]╰─ [OK] Changes applied[/{CLAUDE_BORDER}]")
    console.print()


def print_assistant_message(content: str):
    """Render assistant markdown response."""
    if not content:
        return
    console.print()
    console.print(Markdown(content.strip(), code_theme="monokai"))
    console.print()


def print_thinking(thought: str, super_thinking: bool = False):
    """Display the internal reasoning process when thinking mode is active."""
    if not thought or not thought.strip():
        return
    text = thought.strip()
    words = len(text.split())
    
    console.print()
    if super_thinking:
        header = f"[bold {CLAUDE_ORANGE}]╭─ ◈ Extended Analysis ({words} words)[/bold {CLAUDE_ORANGE}]"
    else:
        header = f"[bold {CLAUDE_ORANGE}]╭─ ◈ Reasoning Analysis ({words} words)[/bold {CLAUDE_ORANGE}]"
    console.print(header)
    for line in text.split("\n"):
        if super_thinking:
            console.print(f"[{CLAUDE_BORDER}]│[/]  [bold italic #e67e22]{line}[/bold italic #e67e22]")
        else:
            console.print(f"[{CLAUDE_BORDER}]│[/]  [dim italic #b4b4b4]{line}[/dim italic #b4b4b4]")
    
    footer = f"[bold {CLAUDE_BORDER}]╰─ ◈ End of Extended Analysis[/bold {CLAUDE_BORDER}]" if super_thinking else f"[{CLAUDE_BORDER}]╰─ ◈ End of Analysis[/{CLAUDE_BORDER}]"
    console.print(footer)
    console.print()


def prompt_permission(action_type: str, detail: str) -> str:
    """Prompt user for confirmation when permission mode is 'ask'."""
    console.print()
    console.print(Panel(
        f"[bold yellow]Tool Permission Required[/bold yellow]\n\n"
        f"[dim]Action:[/] [bold white]{action_type}[/bold white]\n"
        f"[dim]Detail:[/] [cyan]{detail}[/cyan]\n\n"
        f"[white]Do you want to run this tool?[/white]\n"
        f"[dim]y = Yes, once | n = No, abort | a = Always allow for session[/dim]",
        border_style="yellow",
        title="[yellow]Permission Check[/yellow]"
    ))
    choice = Prompt.ask(
        f"[{CLAUDE_ORANGE}]Allow?[/]",
        choices=["y", "n", "a"],
        default="y"
    )
    return choice


def print_stats(tokens_in: int, tokens_out: int, duration_s: float, cost: float = 0.0, saved_path: Optional[str] = None):
    """Display session token, latency, and saved document statistics."""
    t_rate = (tokens_in + tokens_out) / duration_s if duration_s > 0 else 0
    saved_str = f" · [cyan]Saved to: {saved_path}[/cyan]" if saved_path else ""
    console.print(
        f"[dim]Tokens:[/] [white]{tokens_in:,}[/] [dim]in[/dim] / "
        f"[white]{tokens_out:,}[/] [dim]out[/dim] · "
        f"[dim]{duration_s:.2f}s[/dim] · "
        f"[dim]{t_rate:.1f} tok/s[/dim]{saved_str}"
    )


def explain_error(err_input: Any) -> Dict[str, Any]:
    """Parse raw error strings or exceptions and produce human-understandable diagnostics."""
    import re
    import json

    msg_str = str(err_input).strip()
    error_data = {}
    json_match = re.search(r'\{.*"error".*\}', msg_str, re.DOTALL)
    if json_match:
        try:
            error_data = json.loads(json_match.group(0)).get('error', {})
        except Exception:
            pass

    raw_message = error_data.get('message') or msg_str
    err_code = str(error_data.get('code') or '').lower()
    lower_msg = msg_str.lower()

    # 1. Rate Limit (429 / TPM / RPM)
    if '429' in msg_str or 'rate_limit' in err_code or 'rate limit' in lower_msg or 'tokens per minute' in lower_msg:
        wait_m = re.search(r'try again in ([\d\.]+\s*(?:s|ms|seconds|minutes))', raw_message, re.IGNORECASE)
        wait_str = wait_m.group(1) if wait_m else 'a few seconds'
        return {
            'title': 'Rate Limit Exceeded (HTTP 429)',
            'what_happened': 'You reached the provider request or token quota (TPM/RPM) for the active model.',
            'details': f'Cooldown required: please wait {wait_str} before making another request.',
            'solutions': [
                f'Wait {wait_str} for your token quota window to reset, then retry your prompt.',
                'Run /compact to compress conversation history and use fewer tokens per turn.',
                'Switch to a lighter model with higher limits: /model llama-3.3-70b-versatile or /model 8b.',
                'Add more API keys with /key groq <new_key> to expand your key rotation pool.'
            ]
        }

    # 2. Authentication (401 / 403 / Invalid Key)
    if '401' in msg_str or '403' in msg_str or 'invalid_api_key' in err_code or 'invalid api key' in lower_msg or 'unauthorized' in lower_msg:
        return {
            'title': 'Authentication Failed (HTTP 401/403)',
            'what_happened': 'The API key provided was rejected or has been revoked by the provider.',
            'details': 'Your active credentials could not authenticate with the provider API.',
            'solutions': [
                'Set a valid API key with: /key groq <your_key>.',
                'Verify your active keys at https://console.groq.com/keys.',
                'Run /doctor to check your provider credentials and environment status.'
            ]
        }

    # 3. Context Length / Prompt Too Large (413 / 400)
    if '413' in msg_str or 'context_length_exceeded' in err_code or 'too large' in lower_msg or 'maximum context' in lower_msg:
        return {
            'title': 'Context Length Exceeded (HTTP 413)',
            'what_happened': 'The total token count of your prompt, conversation history, and system instructions exceeds the model context window.',
            'details': 'The context window has filled up with accumulated conversation turns.',
            'solutions': [
                'Run /compact to summarize conversation history and reduce token usage.',
                'Run /clear to start a fresh conversation with an empty context window.',
                'Avoid pasting huge file dumps or build logs directly into the prompt.'
            ]
        }

    # 4. Connection / Network Error
    if any(k in lower_msg for k in ['connection refused', 'connecterror', 'timeout', 'nodename nor servname', 'network is unreachable', 'max retries']):
        return {
            'title': 'Network Connection Error',
            'what_happened': 'Unable to establish a connection to the AI provider endpoint.',
            'details': 'Network timeout or unreachable host.',
            'solutions': [
                'Check your internet connection.',
                'Verify whether a VPN, firewall, or proxy is blocking outbound HTTPS requests.',
                'Run /doctor to test connectivity.'
            ]
        }

    # 5. Model Not Found / Deprecated (404)
    if '404' in msg_str or 'model_not_found' in err_code or 'does not exist' in lower_msg:
        return {
            'title': 'Model Not Found (HTTP 404)',
            'what_happened': 'The requested model is either unavailable, misspelled, or deprecated on this provider.',
            'details': raw_message[:150],
            'solutions': [
                'Run /model to see the list of active supported models.',
                'Switch to an available model: /model openai/gpt-oss-120b or /model llama-3.3-70b-versatile.'
            ]
        }

    # Fallback / Generic
    clean_msg = raw_message
    if '{' in clean_msg and '}' in clean_msg:
        clean_msg = re.sub(r'\{.*?\}', '', clean_msg).strip() or raw_message

    return {
        'title': 'Execution Error',
        'what_happened': clean_msg[:200],
        'details': '',
        'solutions': [
            'Run /doctor to check your environment, git status, and API health.',
            'Try running /clear or /compact if the conversation is stuck.'
        ]
    }


def print_error(msg: Any):
    """Render a human-readable diagnostic error panel explaining what went wrong and how to fix it."""
    from rich.markup import escape
    from rich.panel import Panel

    msg_str = str(msg).strip()
    if not msg_str:
        return

    # For short simple validation notices, keep clean single line
    if len(msg_str) < 55 and not any(k in msg_str.lower() for k in ["429", "401", "403", "413", "api error", "failed", "rate limit", "connection"]):
        console.print(f"[{CLAUDE_RED}]Error:[/{CLAUDE_RED}] {escape(msg_str)}")
        return

    diag = explain_error(msg_str)
    
    body = f"[bold white]What happened:[/bold white]\n{diag['what_happened']}\n"
    if diag.get('details'):
        body += f"\n[dim]{diag['details']}[/dim]\n"
    
    if diag.get('solutions'):
        body += "\n[bold white]How to fix:[/bold white]\n"
        for s in diag['solutions']:
            body += f"  • {s}\n"

    panel = Panel(
        body.strip(),
        title=f"[bold {CLAUDE_RED}]✖ {diag['title']}[/bold {CLAUDE_RED}]",
        border_style=CLAUDE_RED,
        padding=(1, 2)
    )
    console.print()
    console.print(panel)
    console.print()


def print_info(msg: str):
    from rich.markup import escape
    console.print(f"[{CLAUDE_BLUE}]Info:[/{CLAUDE_BLUE}] {escape(str(msg))}")


def print_success(msg: str):
    from rich.markup import escape
    console.print(f"[{CLAUDE_GREEN}]Success:[/{CLAUDE_GREEN}] {escape(str(msg))}")


def print_warning(msg: str):
    from rich.markup import escape
    console.print(f"[{CLAUDE_YELLOW}]Warning:[/{CLAUDE_YELLOW}] {escape(str(msg))}")


ALERT_CONFIGS = {
    "note": {"color": "#4db8ff", "label": "NOTE"},
    "tip": {"color": "#2ecc71", "label": "TIP"},
    "important": {"color": "#9b59b6", "label": "IMPORTANT"},
    "warning": {"color": "#f1c40f", "label": "WARNING"},
    "caution": {"color": "#e74c3c", "label": "CAUTION"},
}


def print_alert(alert_type: str, title: str, message: str):
    """Render Antigravity-style clean alert panels."""
    key = alert_type.lower().replace("[!", "").replace("]", "").strip()
    cfg = ALERT_CONFIGS.get(key, ALERT_CONFIGS["note"])
    col = cfg["color"]
    lbl = cfg["label"]

    panel = Panel(
        f"[white]{message}[/white]",
        title=f"[bold {col}]{lbl}: {title}[/bold {col}]",
        border_style=col,
        padding=(0, 2)
    )
    console.print()
    console.print(panel)
    console.print()


def print_deep_research_indicator(prompt: str):
    """Render a clean prompt box at the bottom."""
    clean_p = prompt.strip()
    panel = Panel(
        f"[bold white]{clean_p}[/bold white]",
        border_style=CLAUDE_BORDER,
        title="[dim]Prompt[/dim]",
        title_align="left",
        padding=(1, 2)
    )
    console.print()
    console.print(panel)


def print_usage_heatmap():
    """Display a 32-square heatmap for monthly usage."""
    import json
    import datetime
    from pathlib import Path
    
    console.print()
    console.print("[bold white]Usage Monitor[/bold white] [dim](Days of Month)[/dim]")
    
    daily_file = Path.home() / ".claude-replica" / "daily_usage.json"
    daily_data = {}
    if daily_file.exists():
        try:
            with open(daily_file, "r") as f:
                daily_data = json.load(f)
        except Exception:
            pass
            
    now = datetime.datetime.now()
    year = now.year
    month = now.month
    
    # Generate 32 squares (4 rows of 8)
    colors = ["#1a1a1a", "#1e3a29", "#276b3f", "#39a859", "#4cd964"]
    
    grid = Table.grid(padding=(0, 1))
    for _ in range(8):
        grid.add_column()
        
    for row in range(4):
        cells = []
        for col in range(8):
            day = row * 8 + col + 1
            if day > 31:
                color = colors[0]
            else:
                date_str = f"{year}-{month:02d}-{day:02d}"
                usage = daily_data.get(date_str, 0)
                if usage == 0:
                    color = colors[0]
                elif usage < 1000:
                    color = colors[1]
                elif usage < 5000:
                    color = colors[2]
                elif usage < 20000:
                    color = colors[3]
                else:
                    color = colors[4]
            cells.append(f"[{color}]██[/{color}]")
        grid.add_row(*cells)
    
    panel = Panel(
        grid,
        title="[bold cyan]Monthly Activity[/bold cyan]",
        border_style="cyan",
        padding=(1, 2),
        expand=False
    )
    console.print(panel)
    console.print("[dim]Less Color (No Usage) -> More Color (High Usage)[/dim]")
    console.print()


