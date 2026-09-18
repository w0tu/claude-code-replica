"""
Retro Terminal Animations & Spinners for Claude Code Replica.
Integrates:
- claude-code-spinner (https://github.com/coding-pelican/claude-code-spinner)
- OpenASCII 3a format player (https://github.com/asciimoth/openascii)
"""

import os
import re
import sys
import time
from pathlib import Path
from typing import List, Dict, Any, Optional

import rich.spinner
from rich.console import Console
from rich.panel import Panel
from rich.live import Live

console = Console()

ANIMATIONS_DATA_DIR = Path(__file__).parent / "animations" / "data"

# ============================================================
# Retro ASCII Spinners (from claude-code-spinner)
# ============================================================

SPINNER_DEFINITIONS: Dict[str, Dict[str, Any]] = {
    "crt_bar": {
        "interval": 90,
        "frames": [
            "|>    |",
            "|=>   |",
            "|==>  |",
            "|===> |",
            "|====>|",
            "|    <|",
            "|   <=|",
            "|  <==|",
            "| <===|",
            "|<====|",
        ]
    },
    "pipe": {
        "interval": 120,
        "frames": ["|", "/", "-", "\\"]
    },
    "braille": {
        "interval": 100,
        "frames": [".", "o", "O", "0", "O", "o"]
    },
    "variation_1": {
        "interval": 140,
        "frames": [
            "//",
            "||",
            "\\\\",
            "=="
        ]
    },
    "variation_2": {
        "interval": 140,
        "frames": [
            "  ||  ",
            " \\\\   ",
            "====== ",
            "   // "
        ]
    },
    "variation_3": {
        "interval": 120,
        "frames": [
            "◈ · ·",
            "· ◈ ·",
            "· · ◈",
            "· · ·",
            "· · ◈",
            "· ◈ ·"
        ]
    },
    "variation_4": {
        "interval": 120,
        "frames": [
            "0 o .",
            "o . 0",
            ". 0 o"
        ]
    }
}

# Register all spinners into rich.spinner.SPINNERS
for name, data in SPINNER_DEFINITIONS.items():
    rich.spinner.SPINNERS[name] = data

# Default fallback spinner if clawd requested
rich.spinner.SPINNERS["clawd"] = SPINNER_DEFINITIONS["crt_bar"]


# ============================================================
# OpenASCII 3a Animation Parser & Player
# ============================================================

class OpenAsciiAnimation:
    """Parser and player for .3a ASCII animation format."""
    def __init__(self, file_path: Path):
        self.file_path = Path(file_path)
        self.title = self.file_path.stem
        self.frames: List[List[str]] = []
        self.fps = 10
        self._load()

    def _load(self):
        if not self.file_path.exists():
            return
        try:
            with open(self.file_path, "r", encoding="utf-8", errors="replace") as f:
                content = f.read()

            in_body = False
            current_frame: List[str] = []
            for line in content.splitlines():
                line_str = line.rstrip("\r\n")
                if line_str.startswith("title "):
                    self.title = line_str.replace("title ", "").strip()
                elif line_str.startswith("@body"):
                    in_body = True
                    continue
                elif not in_body:
                    continue

                # In 3a format, empty lines separate animation frames
                if not line_str.strip():
                    if current_frame:
                        self.frames.append(current_frame)
                        current_frame = []
                else:
                    clean_line = re.sub(r'[0-9a-fA-F]{6,}$', '', line_str).rstrip()
                    current_frame.append(clean_line or line_str)

            if current_frame:
                self.frames.append(current_frame)
        except Exception:
            pass


def list_spinners() -> List[str]:
    """List names of all retro spinners."""
    return list(SPINNER_DEFINITIONS.keys())


def get_spinner_frames(name: str) -> List[str]:
    """Get frame list for a specific spinner."""
    return SPINNER_DEFINITIONS.get(name, {}).get("frames", [])


def list_animations() -> List[str]:
    """List available OpenASCII animation names."""
    return list_available_animations()


def load_3a_animation(name: str) -> List[List[str]]:
    """Load and return frames for an OpenASCII animation."""
    anim = get_animation(name)
    return anim.frames if anim else []


def list_available_animations() -> List[str]:
    """List available OpenASCII animation names."""
    if not ANIMATIONS_DATA_DIR.exists():
        return []
    return sorted([f.stem for f in ANIMATIONS_DATA_DIR.glob("*.3a")])


def get_animation(name: str) -> Optional[OpenAsciiAnimation]:
    """Retrieve an OpenASCII animation by name."""
    clean_name = name.lower().strip()
    if not clean_name.endswith(".3a"):
        clean_name += ".3a"
    target = ANIMATIONS_DATA_DIR / clean_name
    if target.exists():
        anim = OpenAsciiAnimation(target)
        if anim.frames:
            return anim
    return None


def play_ascii_animation(
    name: str = "dna",
    duration_s: float = 2.0,
    border_color: str = "#d97757",
    title_suffix: str = "Animation"
) -> bool:
    """Play an OpenASCII retro animation in the terminal using Rich Live."""
    if not sys.stdout.isatty():
        return False

    anim = get_animation(name)
    if not anim or not anim.frames:
        return False

    title_text = f"[{border_color}]◈ OpenASCII: {anim.title} ({title_suffix})[/{border_color}]"
    first_frame_text = "\n".join(anim.frames[0])
    panel = Panel(
        first_frame_text,
        border_style=border_color,
        title=title_text,
        padding=(0, 2)
    )

    frame_delay = 1.0 / max(1, anim.fps)
    start_time = time.time()

    with Live(panel, console=console, refresh_per_second=anim.fps, transient=True) as live:
        frame_idx = 0
        total_frames = len(anim.frames)
        while time.time() - start_time < duration_s:
            curr_lines = anim.frames[frame_idx % total_frames]
            live.update(
                Panel(
                    "\n".join(curr_lines),
                    border_style=border_color,
                    title=title_text,
                    padding=(0, 2)
                )
            )
            frame_idx += 1
            time.sleep(frame_delay)

    return True
