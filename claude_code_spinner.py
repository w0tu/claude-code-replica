#!/usr/bin/env python3
"""
Claude Code Spinner - Cross-platform retro ASCII spinner overlay for Claude Code.

Renders NxM ASCII art spinners directly onto the terminal via /dev/tty writes,
independent of Claude Code's Ink rendering pipeline. Ink redraws its own region;
the spinner occupies space outside that region (or gets naturally overwritten).

Modes:
  enable      - Register hooks in ~/.claude/settings.json (activates spinner)
  disable     - Remove hooks from ~/.claude/settings.json (deactivates spinner)
  status      - Show hook registration state and current configuration
  hook        - Handle Claude Code lifecycle events (invoked by hooks)
  statusline  - Render inline status line for Claude Code's status bar
  overlay     - Run spinner overlay daemon (launched automatically by hook)
  kill        - Kill running overlay daemon for a session
  list        - List available spinner variants
  preview     - Preview a spinner live in the terminal
  init-config - Write default config.json
"""

import argparse
import json
import math
import os
import shutil
import signal
import struct
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

# ============================================================
# Spinner Library
# ============================================================

def _fmulti(frames, ms=200):
    h = max(len(f) for f in frames) if frames else 1
    w = max(len(line) for f in frames for line in f) if frames else 1
    normalized = []
    for frame in frames:
        lines = [line.ljust(w) for line in frame]
        if len(lines) < h:
            lines += [" " * w] * (h - len(lines))
        normalized.append(lines)
    return {"w": w, "h": h, "ms": ms, "frames": normalized}


def _f1(frames, ms=120):
    """Helper for single-line spinner frames."""
    w = max(len(f) for f in frames) if frames else 1
    return {"w": w, "h": 1, "ms": ms, "frames": [[f.ljust(w)] for f in frames]}


SPINNERS = {
    # --- 1-line spinners (for statusline) ---

    # CRT scanning bar: left-to-right ping-pong sweep
    "crt_bar": _f1([
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
    ], 90),

    # Classic pipe spinner
    "pipe": _f1(["|", "/", "-", "\\"], 120),

    # Braille pulse
    "braille": _f1([".", "o", "O", "0", "O", "o"], 100),

    # sprite.txt variation #1
    "variation_1": _fmulti([
        ["    ||", "    ||", "    ||", "    ||", "    ||"],
        ["        //", "      //", "    //", "  //", "//"],
        ["", "", "==========", "", ""],
        ["\\\\", "  \\\\", "    \\\\", "      \\\\", "        \\\\"],
    ], 140),
    # sprite.txt variation #2
    "variation_2": _fmulti([
        ["  ||", "  ||", "  ||"],
        ["\\\\", "  \\\\", "    \\\\"],
        ["", "======", ""],
        ["    //", "  //", "//"],
    ], 140),
    # sprite.txt variation #3
    "variation_3": _fmulti([
        ["   o * .", " 0       .", "@         .", " .       .", "   . . ."],
        ["   * . .", " o       .", "0         .", " @       .", "   . . ."],
        ["   . . .", " *       .", "o         .", " 0       .", "   @ . ."],
        ["   . . .", " .       .", "*         .", " o       .", "   0 @ ."],
        ["   . . .", " .       .", ".         .", " *       .", "   o 0 @"],
        ["   . . .", " .       .", ".         .", " .       @", "   * o 0"],
        ["   . . .", " .       .", ".         @", " .       0", "   . * o"],
        ["   . . .", " .       @", ".         0", " .       o", "   . . *"],
        ["   . . @", " .       0", ".         o", " .       *", "   . . ."],
        ["   . @ 0", " .       o", ".         *", " .       .", "   . . ."],
        ["   @ 0 o", " .       *", ".         .", " .       .", "   . . ."],
        ["   0 o *", " @       .", ".         .", " .       .", "   . . ."],
    ], 120),
    # sprite.txt variation #4
    "variation_4": _fmulti([
        ["  0 o", "@     .", "  . ."],
        ["  o .", "0     .", "  @ ."],
        ["  . .", "o     .", "  0 @"],
        ["  . .", ".     @", "  o 0"],
        ["  . @", ".     0", "  . o"],
        ["  @ 0", ".     o", "  . ."],
    ], 120),
}


def get_spinner(name):
    return SPINNERS.get(name) or SPINNERS["variation_1"]


def get_spinner_for_statusline(name):
    """Return a spinner suitable for 1-line statusline display.

    If the named spinner is multi-line, falls back to crt_bar and logs a
    warning so the user knows their config needs to be updated.
    """
    sp = SPINNERS.get(name)
    if sp is None:
        return SPINNERS["crt_bar"], f"unknown spinner '{name}', using crt_bar"
    if sp["h"] > 1:
        return SPINNERS["crt_bar"], f"spinner '{name}' is {sp['h']} lines tall; statusline needs h=1, using crt_bar"
    return sp, None


def get_frame_at(spinner, timestamp_ms=None):
    """Time-based frame selection — used by the overlay daemon (continuous loop)."""
    if timestamp_ms is None:
        timestamp_ms = int(time.time() * 1000)
    n = len(spinner["frames"])
    idx = int((timestamp_ms / spinner["ms"]) % n)
    return spinner["frames"][idx]


def get_next_statusline_frame(spinner, session_id):
    """Call-count-based frame selection — used by the statusline handler.

    The statusline is invoked at a fixed refresh interval (e.g. every 1 second)
    that rarely aligns with the spinner's ms-per-frame cycle. Using wall-clock
    time causes the visible frame to advance by gcd(interval, cycle) steps,
    making some frames never appear. Advancing by exactly +1 per call ensures
    every frame is shown in order, regardless of the refresh interval.
    """
    state = load_state(session_id) or {}
    n = len(spinner["frames"])
    idx = (state.get("sl_frame", -1) + 1) % n
    state["sl_frame"] = idx
    save_state(session_id, state)
    return spinner["frames"][idx]


def auto_select_spinner(theme_name, avail_w, avail_h):
    """Pick the largest sprite variation that fits avail_w x avail_h."""
    fitting = []
    for name, sp in SPINNERS.items():
        if sp["w"] <= avail_w and sp["h"] <= avail_h:
            fitting.append((sp["w"] * sp["h"], sp["h"], sp["w"], name))
    if fitting:
        _, _, _, chosen = sorted(fitting)[-1]
        return get_spinner(chosen), chosen
    return SPINNERS["variation_1"], "variation_1"


# ============================================================
# Terminal I/O
# ============================================================

def _is_truthy(value):
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def _run_capture(args, timeout=2, stdin_path=None):
    try:
        kwargs = {"capture_output": True, "text": True, "timeout": timeout}
        if stdin_path:
            with open(stdin_path, "r", encoding="utf-8", errors="ignore") as f:
                result = subprocess.run(args, stdin=f, **kwargs)
        else:
            result = subprocess.run(args, **kwargs)
        if result.returncode == 0:
            return (result.stdout or "").strip()
    except Exception:
        pass
    return ""


def _resolve_tmux_target():
    manual_target = os.environ.get("CLAUDE_SPINNER_TMUX_TARGET", "").strip()
    if manual_target:
        return manual_target

    pane = os.environ.get("TMUX_PANE", "").strip()
    if pane:
        return pane

    probe = _run_capture(["tmux", "display-message", "-p", "#{pane_id}"], timeout=1)
    if probe:
        return probe

    listing = _run_capture(
        ["tmux", "list-panes", "-a", "-F", "#{session_attached} #{pane_active} #{pane_id}"],
        timeout=2,
    )
    if not listing:
        return ""

    for line in listing.splitlines():
        parts = line.strip().split()
        if len(parts) >= 3 and parts[0] == "1" and parts[1] == "1":
            return parts[2]
    for line in listing.splitlines():
        parts = line.strip().split()
        if len(parts) >= 3 and parts[0] == "1":
            return parts[2]
    return ""


def _resolve_tmux_tty():
    target = _resolve_tmux_target()
    if not target:
        return ""

    tty = _run_capture(["tmux", "display-message", "-p", "-t", target, "#{pane_tty}"], timeout=1)
    if tty.startswith("/"):
        return tty
    return ""


def _walk_process_tree_tty():
    """Walk PPID chain to find the terminal TTY path (Unix only).

    When CLAUDE_CODE_SPAWN_BACKEND=tmux, hooks run in subprocesses with no
    controlling terminal. Walking up the process tree finds the PTY of the
    ancestor terminal session. Mirrors peon-ping's _peon_walk_tty approach.

    Uses two separate `ps` calls per iteration (-o tty= and -o ppid=) to
    avoid relying on comma-separated field syntax that differs across
    GNU ps (Linux), BSD ps (macOS), and BusyBox ps.
    """
    if sys.platform == "win32":
        return ""
    try:
        pid = os.getppid()
        last_tty = ""
        seen = set()
        while pid > 1 and pid not in seen:
            seen.add(pid)
            tty_name = _run_capture(["ps", "-p", str(pid), "-o", "tty="], timeout=1)
            ppid_str = _run_capture(["ps", "-p", str(pid), "-o", "ppid="], timeout=1)
            if tty_name and tty_name not in ("??", ""):
                # ps outputs "pts/0" (Linux) or "s004" (macOS) - prepend /dev/
                last_tty = "/dev/" + tty_name
            if not ppid_str or not ppid_str.isdigit():
                break
            pid = int(ppid_str)
        return last_tty
    except Exception:
        return ""


class _Win32ConOut:
    """Direct WriteFile-to-CONOUT$ writer — the ctypes equivalent of C's fwrite to CONOUT$.

    Python's text-mode open("CONOUT$") goes through TextIOWrapper -> BufferedWriter
    -> FileIO -> WriteFile, adding codec and buffering layers. Using CreateFileW +
    WriteFile directly (as a C io_Writer implementation would) removes those layers:
    every write() call is a single synchronous WriteFile to the console handle.

    VT processing is enabled on the handle at construction time so ANSI cursor-
    positioning sequences are honoured even in a daemon spawned with redirected stdio.
    """

    GENERIC_READ  = 0x80000000
    GENERIC_WRITE = 0x40000000
    FILE_SHARE_RW = 0x00000003
    OPEN_EXISTING = 3
    ENABLE_VT     = 0x0004

    def __init__(self, name="CONOUT$"):
        import ctypes
        self._ctypes = ctypes
        k32 = ctypes.windll.kernel32
        k32.CreateFileW.restype = ctypes.c_void_p
        handle = k32.CreateFileW(
            name,
            self.GENERIC_READ | self.GENERIC_WRITE,
            self.FILE_SHARE_RW,
            None, self.OPEN_EXISTING, 0, None,
        )
        # CreateFileW returns INVALID_HANDLE_VALUE (cast to signed: -1) on failure
        if ctypes.c_void_p(handle).value == ctypes.c_void_p(-1).value:
            raise OSError(f"CreateFileW({name!r}) failed")
        self._handle = handle
        self._k32 = k32
        # Enable VT processing so ANSI sequences are honoured
        mode = ctypes.c_ulong()
        if k32.GetConsoleMode(ctypes.c_void_p(handle), ctypes.byref(mode)):
            k32.SetConsoleMode(
                ctypes.c_void_p(handle),
                mode.value | self.ENABLE_VT,
            )

    def write(self, text):
        data = text.encode("utf-8") if isinstance(text, str) else bytes(text)
        if not data:
            return
        written = self._ctypes.c_ulong()
        self._k32.WriteFile(
            self._ctypes.c_void_p(self._handle),
            data, len(data),
            self._ctypes.byref(written),
            None,
        )

    def flush(self):
        pass  # WriteFile is synchronous; nothing to flush

    def close(self):
        if self._handle is not None:
            self._k32.CloseHandle(self._ctypes.c_void_p(self._handle))
            self._handle = None


def _open_tty_path(path):
    if not path:
        return None
    try:
        return open(path, "w", encoding="utf-8", buffering=1)
    except Exception:
        return None


def open_tty_write():
    override = os.environ.get("CLAUDE_SPINNER_TTY", "").strip()
    if override:
        tty = _open_tty_path(override)
        if tty:
            return tty, override, "env"

    if sys.platform == "win32":
        # Use _Win32ConOut: CreateFileW("CONOUT$") + WriteFile, with VT processing
        # enabled on the handle.  This is the direct ctypes equivalent of C's
        # fopen("CONOUT$","w") + fwrite — the io_Writer-to-file pattern — and avoids
        # Python's TextIOWrapper/BufferedWriter codec layers that sit between our
        # ANSI sequences and the actual WriteFile syscall.
        for con_name in ("CONOUT$", "CON"):
            try:
                return _Win32ConOut(con_name), con_name, "native"
            except Exception:
                pass
        return None, "", "none"

    tty = _open_tty_path("/dev/tty")
    if tty:
        return tty, "/dev/tty", "native"

    tmux_tty = _resolve_tmux_tty()
    if tmux_tty:
        tmux_handle = _open_tty_path(tmux_tty)
        if tmux_handle:
            return tmux_handle, tmux_tty, "tmux"

    proc_tty = _walk_process_tree_tty()
    if proc_tty:
        proc_handle = _open_tty_path(proc_tty)
        if proc_handle:
            return proc_handle, proc_tty, "proc"

    return None, "", "none"


def query_terminal_size_win32():
    """Windows Console API: GetConsoleScreenBufferInfo on CONOUT$.

    Works even when the process was spawned with redirected stdio (stdout →
    DEVNULL), because we open CONOUT$ explicitly via CreateFileW rather than
    relying on the inherited stdout handle.  Returns the *visible window* size
    (srWindow), not the scroll-back buffer size (dwSize).
    """
    if sys.platform != "win32":
        return None
    try:
        import ctypes
        import ctypes.wintypes

        kernel32 = ctypes.windll.kernel32

        GENERIC_READ      = 0x80000000
        FILE_SHARE_RW     = 0x00000003
        OPEN_EXISTING     = 3
        INVALID_HANDLE    = ctypes.c_void_p(-1).value

        kernel32.CreateFileW.restype = ctypes.c_void_p
        handle = kernel32.CreateFileW(
            "CONOUT$", GENERIC_READ, FILE_SHARE_RW,
            None, OPEN_EXISTING, 0, None,
        )
        if handle == INVALID_HANDLE:
            return None

        class _COORD(ctypes.Structure):
            _fields_ = [("X", ctypes.c_short), ("Y", ctypes.c_short)]

        class _SMALL_RECT(ctypes.Structure):
            _fields_ = [
                ("Left",   ctypes.c_short), ("Top",    ctypes.c_short),
                ("Right",  ctypes.c_short), ("Bottom", ctypes.c_short),
            ]

        class _CSBI(ctypes.Structure):
            _fields_ = [
                ("dwSize",              _COORD),
                ("dwCursorPosition",    _COORD),
                ("wAttributes",         ctypes.c_ushort),
                ("srWindow",            _SMALL_RECT),
                ("dwMaximumWindowSize", _COORD),
            ]

        info = _CSBI()
        ok = kernel32.GetConsoleScreenBufferInfo(handle, ctypes.byref(info))
        kernel32.CloseHandle(handle)
        if ok:
            cols = info.srWindow.Right  - info.srWindow.Left + 1
            rows = info.srWindow.Bottom - info.srWindow.Top  + 1
            if rows > 0 and cols > 0:
                return rows, cols
    except Exception:
        pass
    return None


def query_terminal_size_ioctl(tty_path):
    """ioctl TIOCGWINSZ → (rows, cols) or None."""
    if sys.platform == "win32":
        return None

    candidate = tty_path or "/dev/tty"
    try:
        import fcntl
        import termios
        with open(candidate, "r", encoding="utf-8", errors="ignore") as f:
            result = fcntl.ioctl(f.fileno(), termios.TIOCGWINSZ, b'\x00' * 8)
            rows, cols = struct.unpack('HHHH', result)[:2]
            if rows > 0 and cols > 0:
                return rows, cols
    except Exception:
        pass
    return None


def query_terminal_size_tmux():
    target = _resolve_tmux_target()
    if not target:
        return None
    out = _run_capture(
        ["tmux", "display-message", "-p", "-t", target, "#{pane_height} #{pane_width}"],
        timeout=1,
    )
    if not out:
        return None
    parts = out.split()
    if len(parts) != 2:
        return None
    try:
        rows = int(parts[0])
        cols = int(parts[1])
        if rows > 0 and cols > 0:
            return rows, cols
    except Exception:
        pass
    return None


def get_terminal_size(tty_path=""):
    """(rows, cols) via win32 API → ioctl → os → stty → tmux → env → fallback."""
    r = query_terminal_size_win32()
    if r:
        return r

    r = query_terminal_size_ioctl(tty_path)
    if r:
        return r

    try:
        cols, rows = os.get_terminal_size()
        if rows > 0 and cols > 0:
            return rows, cols
    except Exception:
        pass

    stty_stdin = tty_path if tty_path else "/dev/tty"
    out = _run_capture(["stty", "size"], stdin_path=stty_stdin, timeout=1)
    if out:
        parts = out.split()
        if len(parts) == 2:
            try:
                return int(parts[0]), int(parts[1])
            except Exception:
                pass

    tmux_size = query_terminal_size_tmux()
    if tmux_size:
        return tmux_size

    try:
        rows = int(os.environ.get("LINES", 0))
        cols = int(os.environ.get("COLUMNS", 0))
        if rows > 0 and cols > 0:
            return rows, cols
    except Exception:
        pass

    return 24, 80


# ============================================================
# Config & State
# ============================================================

DEFAULT_CONFIG = {
    "spinner": "auto",
    "theme": "sprite",
    "position": "top-right",
    "margin_row": 1,
    "margin_col": 2,
    "max_width_ratio": 0.25,
    "max_height_ratio": 0.33,
    "color": "1;36",
    "idle_clear": True,
    # Screen re-render rate (Hz). Higher = recovers faster after Ink's logUpdate
    # erases the overlay with \033[K.  Ink redraws at ~20-60Hz during streaming;
    # at 100Hz our recovery gap is at most 10ms — imperceptible to the user.
    "redraw_hz": 100,
    "statusline": {
        "enabled": False,
        "spinner": "crt_bar",
        "label": "CRT",
        "show_model": True,
        "show_git": True,
        "show_cost": True,
        "show_elapsed": True,
    },
    "debug": False,
}


def get_config_dir():
    env = os.environ.get("CLAUDE_SPINNER_DIR")
    if env:
        p = Path(env)
    else:
        p = Path.home() / ".claude" / "claude-code-spinner"
    p.mkdir(parents=True, exist_ok=True)
    return p


def load_config():
    cfg_path = get_config_dir() / "config.json"
    cfg = json.loads(json.dumps(DEFAULT_CONFIG))
    if cfg_path.exists():
        try:
            with open(cfg_path, "r", encoding="utf-8") as f:
                user = json.load(f)
            for k, v in user.items():
                if k == "statusline" and isinstance(v, dict):
                    cfg["statusline"] = {**DEFAULT_CONFIG["statusline"], **v}
                else:
                    cfg[k] = v
        except Exception:
            pass
    return cfg


def get_state_path(session_id):
    if not session_id:
        return None
    return get_config_dir() / f"{session_id}.state.json"


def save_state(session_id, state):
    p = get_state_path(session_id)
    if not p:
        return
    try:
        p.write_text(json.dumps(state), encoding="utf-8")
    except Exception:
        pass


def load_state(session_id):
    p = get_state_path(session_id)
    if not p or not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return None


def debug_log(cfg, phase, **fields):
    enabled = _is_truthy(os.environ.get("CLAUDE_SPINNER_DEBUG", "0")) or bool(cfg.get("debug", False))
    if not enabled:
        return

    try:
        log_path = get_config_dir() / "debug.log"
        ts = datetime.now().isoformat(timespec="milliseconds")
        pairs = []
        for k, v in fields.items():
            sval = str(v)
            if " " in sval or "=" in sval:
                sval = '"' + sval.replace("\\", "\\\\").replace('"', '\\"') + '"'
            pairs.append(f"{k}={sval}")
        line = f"{ts} [{phase}] " + " ".join(pairs) + "\n"
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(line)
    except Exception:
        pass


def get_pid_path(session_id):
    return get_config_dir() / f"{session_id}.overlay.pid"


def get_stop_path(session_id):
    return get_config_dir() / f"{session_id}.overlay.stop"


def read_stdin_json():
    try:
        raw = sys.stdin.read()
        if not raw or not raw.strip():
            return None
        return json.loads(raw)
    except Exception:
        return None


def deep_get(obj, *keys, default=None):
    cur = obj
    for k in keys:
        if isinstance(cur, dict) and k in cur:
            cur = cur[k]
        else:
            return default
    return cur


# ============================================================
# Position & Sizing
# ============================================================

def compute_position(cfg, spinner_w, spinner_h, term_rows, term_cols):
    """→ (row, col), 1-indexed."""
    position = cfg.get("position", "top-right")
    margin_r = cfg.get("margin_row", 1)
    margin_c = cfg.get("margin_col", 2)

    if isinstance(position, dict):
        return position.get("row", 1), position.get("col", 1)

    pos = str(position).lower().replace("-", "").replace("_", "")

    r = (1 + margin_r) if "top" in pos else max(1, term_rows - spinner_h - margin_r + 1)
    c = (1 + margin_c) if "left" in pos else max(1, term_cols - spinner_w - margin_c + 1)
    return r, c


def compute_available_space(cfg, term_rows, term_cols):
    """Max spinner dimensions at the configured position."""
    margin_r = cfg.get("margin_row", 1)
    margin_c = cfg.get("margin_col", 2)
    max_w = min(term_cols - margin_c * 2, int(term_cols * cfg.get("max_width_ratio", 0.25)))
    max_h = min(term_rows - margin_r * 2, int(term_rows * cfg.get("max_height_ratio", 0.33)))
    return max(1, max_w), max(1, max_h)


# ============================================================
# Overlay Daemon
# ============================================================

def render_frame(tty, frame, row, col, color=None, drift_rows=2):
    # \033[s / \033[u: save and restore cursor around the overlay write so the
    # foreground renderer (Claude Code / Ink) can continue from where it left off.
    # This requires sharing the same console (ensured on Windows by the
    # FreeConsole + AttachConsole dance in run_overlay).
    #
    # drift_rows: number of extra blank rows written immediately BELOW the
    # spinner after each frame.  When the terminal scrolls, the previous
    # frame shifts down by one row and becomes a "ghost".  Blanking those
    # rows on every render erases scroll-drift artifacts without needing a
    # separate scroll-region VT state change (which interferes with Ink).
    buf = ["\033[s"]
    c_on = f"\033[{color}m" if color else ""
    c_off = "\033[0m" if color else ""
    # Erase drift zone below spinner
    blank = " " * (max(len(line) for line in frame) + 2)
    for i in range(drift_rows):
        buf.append(f"\033[{row + len(frame) + i};{col}H{blank}")
    # Draw spinner frame
    for i, line in enumerate(frame):
        buf.append(f"\033[{row + i};{col}H{c_on}{line}{c_off}")
    buf.append("\033[u")
    tty.write("".join(buf))
    tty.flush()


def clear_area(tty, h, w, row, col):
    buf = ["\033[s"]
    blank = " " * (w + 2)
    for i in range(h):
        buf.append(f"\033[{row + i};{col}H{blank}")
    buf.append("\033[u")
    tty.write("".join(buf))
    tty.flush()


def _win_attach_parent_console(cfg, session_id):
    """FreeConsole() then find a console-owning process to attach to.

    Strategy (in order):
    1. Walk the parent chain (daemon → hook → node → bash → ...).
       The hook may have already exited, so the chain can be short.
    2. If the chain is exhausted without success, search the full process
       list for well-known interactive shell/terminal executables and try
       each one.  This handles the case where the hook subprocess has exited
       before the snapshot is taken, breaking the ancestor walk.

    Mirrors the core pattern from process_b.py:
        kernel32.FreeConsole()
        kernel32.AttachConsole(pid_that_owns_console)
    """
    if sys.platform != "win32":
        return False
    try:
        import ctypes
        k32 = ctypes.windll.kernel32

        k32.FreeConsole()

        # Snapshot all running processes → {pid: ppid}, {pid: exe_name}
        TH32CS_SNAPPROCESS = 0x00000002
        k32.CreateToolhelp32Snapshot.restype = ctypes.c_void_p
        snap = k32.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
        inv = ctypes.c_void_p(-1).value
        if snap == inv or snap is None:
            debug_log(cfg, "attach", session=session_id, error="snapshot_failed")
            return False

        class PROCESSENTRY32W(ctypes.Structure):
            _fields_ = [
                ("dwSize",              ctypes.c_ulong),
                ("cntUsage",            ctypes.c_ulong),
                ("th32ProcessID",       ctypes.c_ulong),
                ("th32DefaultHeapID",   ctypes.c_size_t),
                ("th32ModuleID",        ctypes.c_ulong),
                ("cntThreads",          ctypes.c_ulong),
                ("th32ParentProcessID", ctypes.c_ulong),
                ("pcPriClassBase",      ctypes.c_long),
                ("dwFlags",             ctypes.c_ulong),
                ("szExeFile",           ctypes.c_wchar * 260),
            ]

        entry = PROCESSENTRY32W()
        entry.dwSize = ctypes.sizeof(PROCESSENTRY32W)
        parent_of = {}
        name_of   = {}
        if k32.Process32FirstW(snap, ctypes.byref(entry)):
            while True:
                parent_of[entry.th32ProcessID] = entry.th32ParentProcessID
                name_of[entry.th32ProcessID]   = entry.szExeFile
                if not k32.Process32NextW(snap, ctypes.byref(entry)):
                    break
        k32.CloseHandle(ctypes.c_void_p(snap))

        seen = set()

        # --- Pass 0: try PIDs passed from the hook via env var ---
        # start_overlay_daemon() records hook_pid and claude_pid BEFORE the hook
        # exits, so we can try them even though hook is gone from the process list.
        pids_env = os.environ.get("CLAUDE_SPINNER_ATTACH_PIDS", "")
        if pids_env:
            for epid_str in pids_env.split(","):
                try:
                    epid = int(epid_str.strip())
                except ValueError:
                    continue
                if not epid or epid in seen:
                    continue
                seen.add(epid)
                ok = bool(k32.AttachConsole(epid))
                debug_log(cfg, "attach_try_env", session=session_id,
                          pid=epid, exe=name_of.get(epid, "?"), ok=ok)
                if ok:
                    return True

        # --- Pass 1: walk ancestor chain from daemon's recorded parent ---
        # hook may already be gone; chain truncates after first missing pid.
        pid = os.getpid()
        while True:
            ppid = parent_of.get(pid, 0)
            if not ppid or ppid == pid or ppid in seen:
                break
            seen.add(ppid)
            ok = bool(k32.AttachConsole(ppid))
            debug_log(cfg, "attach_try", session=session_id,
                      pid=ppid, exe=name_of.get(ppid, "?"), ok=ok)
            if ok:
                return True
            pid = ppid

        # --- Pass 2: search known shell/console hosts, ANCESTORS first ---
        # The correct bash is the one that is a process-tree ancestor of this
        # daemon (it is the shell the user typed 'claude' in).  Subshells
        # spawned by Claude Code for tool execution are DESCENDANTS, not
        # ancestors.  Build the daemon's full ancestor set from the snapshot
        # and try ancestor candidates before non-ancestor ones.
        CONSOLE_HOSTS = {
            "bash.exe", "sh.exe", "zsh.exe",
            "mintty.exe",
            "powershell.exe", "pwsh.exe",
            "cmd.exe",
            "conhost.exe",
        }
        daemon_ancestors: set = set()
        _pid = os.getpid()
        while True:
            _ppid = parent_of.get(_pid, 0)
            if not _ppid or _ppid in daemon_ancestors:
                break
            daemon_ancestors.add(_ppid)
            _pid = _ppid

        # Sort: ancestors first (key=False sorts before True)
        candidates = sorted(
            ((cpid, exe) for cpid, exe in name_of.items()
             if exe.lower() in CONSOLE_HOSTS and cpid not in seen),
            key=lambda ce: ce[0] not in daemon_ancestors,
        )
        for cpid, exe in candidates:
            seen.add(cpid)
            is_ancestor = cpid in daemon_ancestors
            ok = bool(k32.AttachConsole(cpid))
            debug_log(cfg, "attach_try_host", session=session_id,
                      pid=cpid, exe=exe, ancestor=is_ancestor, ok=ok)
            if ok:
                return True

        return False
    except Exception as exc:
        debug_log(cfg, "attach_error", session=session_id, error=str(exc)[:120])
        return False


def run_overlay(session_id, cfg):
    # On Windows: the daemon inherits a console from the hook subprocess, but
    # that console is a background/invisible console created by Claude Code
    # (node.js/Electron) — NOT the visible terminal.  Opening CONOUT$ on the
    # inherited console succeeds mechanically but writes go to an invisible
    # buffer.  We MUST FreeConsole + AttachConsole to the shell's visible
    # console (bash.exe, conhost.exe, etc.) before opening CONOUT$.
    # This mirrors process_b.py: FreeConsole() + AttachConsole(process_a_pid).
    if sys.platform == "win32":
        ok = _win_attach_parent_console(cfg, session_id)
        if not ok:
            debug_log(cfg, "overlay", session=session_id,
                      status="attach_failed_no_console")
    tty, tty_path, tty_source = open_tty_write()
    if tty is None:
        debug_log(cfg, "overlay", session=session_id, status="no_tty")
        sys.exit(1)
    debug_log(cfg, "overlay", session=session_id, tty=tty_path or "-", source=tty_source)

    spinner_name = cfg.get("spinner", "auto")
    theme = cfg.get("theme", "sprite")
    color = cfg.get("color", "1;36")

    term_rows, term_cols = get_terminal_size(tty_path)
    avail_w, avail_h = compute_available_space(cfg, term_rows, term_cols)
    debug_log(cfg, "overlay", session=session_id,
              term=f"{term_cols}x{term_rows}", avail=f"{avail_w}x{avail_h}")

    if spinner_name == "auto":
        sp, resolved = auto_select_spinner(theme, avail_w, avail_h)
    else:
        sp = get_spinner(spinner_name)
        resolved = spinner_name

    row, col = compute_position(cfg, sp["w"], sp["h"], term_rows, term_cols)
    debug_log(cfg, "overlay", session=session_id,
              spinner=resolved, size=f"{sp['w']}x{sp['h']}", pos=f"{row},{col}")

    # Animation speed (ms per frame) is controlled by the spinner definition.
    # Screen re-render rate is independent: re-draw at redraw_hz even when the
    # frame hasn't changed, so Ink/other renderers can overwrite us at most for
    # 1/redraw_hz seconds before we re-appear.
    redraw_hz = max(1, cfg.get("redraw_hz", 20))
    redraw_interval = 1.0 / redraw_hz

    stop_path = get_stop_path(session_id)
    # Remove any stale stop file left from a previous crashed session
    try:
        stop_path.unlink()
    except Exception:
        pass

    last_size = (term_rows, term_cols)
    poll_counter = 0
    # Resize check every ~2 s; stop-file check every ~100 ms (every 10 frames at 100 hz)
    stop_check_frames = max(1, int(0.1 / max(redraw_interval, 0.001)))

    _cleanup_done = False

    def cleanup(signum=None, _frame=None):
        nonlocal _cleanup_done
        if _cleanup_done:
            sys.exit(0)
        _cleanup_done = True
        try:
            clear_area(tty, sp["h"], sp["w"], row, col)
        except Exception:
            pass
        try:
            tty.close()
        except Exception:
            pass
        sys.exit(0)

    signal.signal(signal.SIGTERM, cleanup)
    signal.signal(signal.SIGINT, cleanup)

    def handle_resize(signum=None, _frame=None):
        nonlocal term_rows, term_cols, avail_w, avail_h, sp, resolved, row, col
        try:
            clear_area(tty, sp["h"], sp["w"], row, col)
        except Exception:
            pass
        term_rows, term_cols = get_terminal_size(tty_path)
        avail_w, avail_h = compute_available_space(cfg, term_rows, term_cols)
        if spinner_name == "auto":
            sp, resolved = auto_select_spinner(theme, avail_w, avail_h)
        row, col = compute_position(cfg, sp["w"], sp["h"], term_rows, term_cols)

    if hasattr(signal, "SIGWINCH"):
        signal.signal(signal.SIGWINCH, handle_resize)

    render_count = 0
    try:
        while True:
            # get_frame_at is time-based: animation advances at sp["ms"] pace
            # regardless of how often we call render_frame.
            frame_data = get_frame_at(sp)
            render_frame(tty, frame_data, row, col, color)
            render_count += 1
            if render_count == 1:
                debug_log(cfg, "overlay_render1", session=session_id,
                          pos=f"{row},{col}", frame0=repr(frame_data[0][:20]))
            time.sleep(redraw_interval)

            poll_counter += 1

            # Stop-file check: stop_overlay_daemon() writes this before force-killing
            # so the daemon has a chance to clear the screen before termination.
            if poll_counter % stop_check_frames == 0:
                if stop_path.exists():
                    try:
                        stop_path.unlink()
                    except Exception:
                        pass
                    cleanup()

            # Poll resize on platforms without SIGWINCH (~every 2 seconds)
            if poll_counter >= int(2.0 / max(redraw_interval, 0.01)):
                poll_counter = 0
                new_size = get_terminal_size(tty_path)
                if new_size != last_size:
                    last_size = new_size
                    handle_resize()
    except (KeyboardInterrupt, SystemExit):
        cleanup()
    except Exception as exc:
        debug_log(cfg, "overlay_error", session=session_id,
                  error=type(exc).__name__, detail=str(exc)[:120])
        cleanup()


def start_overlay_daemon(session_id, cfg):
    stop_overlay_daemon(session_id)

    script = str(Path(__file__).resolve())
    python = sys.executable
    pid_path = get_pid_path(session_id)
    cmd = [python, script, "overlay", "--session-id", session_id]

    env = os.environ.copy()

    if sys.platform == "win32":
        # Capture the FULL ancestor chain while the hook is alive.
        # Walking up from the hook reaches: hook → Claude Code → bash → ...
        # Storing every ancestor PID lets the daemon find the terminal bash even
        # after the hook and Claude Code PIDs have exited or lack a console.
        try:
            import ctypes as _ct
            _k32 = _ct.windll.kernel32
            _k32.CreateToolhelp32Snapshot.restype = _ct.c_void_p
            _snap = _k32.CreateToolhelp32Snapshot(0x00000002, 0)
            class _PE32W(_ct.Structure):
                _fields_ = [("dwSize", _ct.c_ulong), ("cntUsage", _ct.c_ulong),
                             ("th32ProcessID", _ct.c_ulong),
                             ("th32DefaultHeapID", _ct.c_size_t),
                             ("th32ModuleID", _ct.c_ulong),
                             ("cntThreads", _ct.c_ulong),
                             ("th32ParentProcessID", _ct.c_ulong),
                             ("pcPriClassBase", _ct.c_long),
                             ("dwFlags", _ct.c_ulong),
                             ("szExeFile", _ct.c_wchar * 260)]
            _entry = _PE32W(); _entry.dwSize = _ct.sizeof(_PE32W)
            _par = {}
            if _k32.Process32FirstW(_snap, _ct.byref(_entry)):
                while True:
                    _par[_entry.th32ProcessID] = _entry.th32ParentProcessID
                    if not _k32.Process32NextW(_snap, _ct.byref(_entry)):
                        break
            _k32.CloseHandle(_ct.c_void_p(_snap))
            _pids, _pid, _seen = [], os.getpid(), {os.getpid()}
            while True:
                _ppid = _par.get(_pid, 0)
                if not _ppid or _ppid in _seen:
                    break
                _seen.add(_ppid); _pids.append(_ppid); _pid = _ppid
            env["CLAUDE_SPINNER_ATTACH_PIDS"] = ",".join(str(p) for p in _pids)
        except Exception:
            env["CLAUDE_SPINNER_ATTACH_PIDS"] = f"{os.getpid()},{os.getppid()}"
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            stdin=subprocess.DEVNULL, env=env,
        )
        pid_path.write_text(str(proc.pid), encoding="utf-8")
    else:
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            stdin=subprocess.DEVNULL,
            env=env,
            start_new_session=False,
            close_fds=True,
        )
        pid_path.write_text(str(proc.pid), encoding="utf-8")


def stop_overlay_daemon(session_id):
    pid_path = get_pid_path(session_id)
    if not pid_path.exists():
        return
    try:
        pid = int(pid_path.read_text(encoding="utf-8").strip())
        if sys.platform == "win32":
            # Write a stop file so the daemon can clear the screen (scroll region
            # reset + clear_area) before we force-kill it with taskkill /F.
            # The daemon polls for this file every ~100 ms; we wait 400 ms to
            # give it enough time to clean up, then force-terminate.
            stop_path = get_stop_path(session_id)
            try:
                stop_path.write_text("stop", encoding="utf-8")
            except Exception:
                pass
            time.sleep(0.4)
            subprocess.run(["taskkill", "/F", "/PID", str(pid)],
                           capture_output=True, timeout=5)
        else:
            os.kill(pid, signal.SIGTERM)
    except (ProcessLookupError, ValueError, OSError):
        pass
    finally:
        try:
            pid_path.unlink()
        except Exception:
            pass


# ============================================================
# Settings Manipulation (enable / disable)
# ============================================================

def _settings_path():
    return Path.home() / ".claude" / "settings.json"


def _read_settings(path):
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _write_settings(path, settings):
    if path.exists():
        ts = datetime.now().strftime("%Y%m%d-%H%M%S")
        shutil.copy2(path, path.with_suffix(f".{ts}.bak"))
    path.write_text(json.dumps(settings, indent=2, ensure_ascii=False), encoding="utf-8")


def _remove_spinner_hooks(blocks):
    if not blocks or not isinstance(blocks, list):
        return []
    return [
        b for b in blocks
        if not (isinstance(b, dict) and any(
            isinstance(h, dict) and "claude_code_spinner" in h.get("command", "")
            for h in b.get("hooks", [])
        ))
    ]


def _spinner_hooks_present(settings):
    hooks = settings.get("hooks", {})
    if not isinstance(hooks, dict):
        return False
    for blocks in hooks.values():
        if isinstance(blocks, list):
            for b in blocks:
                if isinstance(b, dict):
                    for h in b.get("hooks", []):
                        if isinstance(h, dict) and "claude_code_spinner" in h.get("command", ""):
                            return True
    return False


def handle_enable():
    script = str(Path(__file__).resolve())
    python = sys.executable
    hook_cmd = f'"{python}" "{script}" hook'
    sl_cmd = f'"{python}" "{script}" statusline'

    sp = _settings_path()
    settings = _read_settings(sp)

    if "hooks" not in settings or not isinstance(settings["hooks"], dict):
        settings["hooks"] = {}

    hook_block = {"hooks": [{"type": "command", "command": hook_cmd}]}
    for event in ("SessionStart", "UserPromptSubmit", "Stop", "StopFailure", "SessionEnd"):
        existing = settings["hooks"].get(event, [])
        if not isinstance(existing, list):
            existing = []
        settings["hooks"][event] = _remove_spinner_hooks(existing) + [hook_block]

    if "statusLine" not in settings:
        settings["statusLine"] = {
            "type": "command",
            "command": sl_cmd,
            "padding": 1,
            "refreshInterval": 1,
        }

    _write_settings(sp, settings)
    print(f"Spinner enabled. Hooks registered in: {sp}")
    print("Restart Claude Code to activate.")


def handle_disable():
    sp = _settings_path()
    settings = _read_settings(sp)

    hooks = settings.get("hooks", {})
    changed = False
    for event in list(hooks.keys()):
        cleaned = _remove_spinner_hooks(hooks[event])
        if cleaned != hooks[event]:
            changed = True
        if cleaned:
            hooks[event] = cleaned
        else:
            del hooks[event]
    settings["hooks"] = hooks

    if changed:
        _write_settings(sp, settings)
        print(f"Spinner disabled. Hooks removed from: {sp}")
        print("Restart Claude Code to deactivate.")
    else:
        print("Spinner hooks were not registered - nothing to remove.")


def handle_status():
    sp = _settings_path()
    settings = _read_settings(sp)
    active = _spinner_hooks_present(settings)

    cfg = load_config()
    cfg_path = get_config_dir() / "config.json"
    log_path = get_config_dir() / "debug.log"

    print(f"Hooks registered : {'yes' if active else 'no'}")
    print(f"Settings file    : {sp}")
    print(f"Config file      : {cfg_path}")
    print(f"Spinner mode     : {cfg.get('spinner', 'auto')}")
    print(f"Position         : {cfg.get('position', 'top-right')}")
    print(f"Color            : {cfg.get('color', '1;36')}")
    print(f"StatusLine       : {'enabled' if cfg.get('statusline', {}).get('enabled') else 'disabled'}")
    debug_on = cfg.get("debug", False)
    print(f"Debug            : {'on  ->  ' + str(log_path) if debug_on else 'off'}")

    pid_dir = get_config_dir()
    running = [p for p in pid_dir.glob("*.overlay.pid") if p.exists()]
    if running:
        print(f"Running daemons  : {len(running)}")
        for p in running:
            print(f"  {p.stem.replace('.overlay', '')} (pid {p.read_text().strip()})")
    else:
        print("Running daemons  : none")


def _set_config_flag(key, value):
    """Read config.json, set key=value, write back. Creates file if missing."""
    cfg_path = get_config_dir() / "config.json"
    if cfg_path.exists():
        try:
            cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
        except Exception:
            cfg = {}
    else:
        cfg = json.loads(json.dumps(DEFAULT_CONFIG))
    cfg[key] = value
    cfg_path.write_text(json.dumps(cfg, indent=2, ensure_ascii=False), encoding="utf-8")
    return cfg_path


def handle_debug_on():
    cfg_path = _set_config_flag("debug", True)
    log_path = get_config_dir() / "debug.log"
    print(f"Debug enabled in: {cfg_path}")
    print(f"Log file        : {log_path}")
    print("Hook/daemon log entries will appear on the next Claude Code event.")


def handle_debug_off():
    cfg_path = _set_config_flag("debug", False)
    print(f"Debug disabled in: {cfg_path}")


def handle_log(lines=50, clear=False):
    log_path = get_config_dir() / "debug.log"

    if clear:
        if log_path.exists():
            log_path.write_text("", encoding="utf-8")
            print(f"Cleared: {log_path}")
        else:
            print("No log file to clear.")
        return

    if not log_path.exists():
        print(f"No debug log found: {log_path}")
        print("Enable debug first:  claude_code_spinner.py debug-on")
        return

    try:
        all_lines = log_path.read_text(encoding="utf-8").splitlines()
    except Exception as e:
        print(f"Could not read log: {e}")
        return

    if not all_lines:
        print(f"Log is empty: {log_path}")
        return

    tail = all_lines[-lines:] if lines and len(all_lines) > lines else all_lines
    if len(all_lines) > lines:
        print(f"... ({len(all_lines) - lines} earlier lines omitted) ...\n")
    for line in tail:
        print(line)
    print(f"\n[{len(all_lines)} total lines | {log_path}]")


# ============================================================
# Hook Handler
# ============================================================

def handle_hook():
    cfg = load_config()
    payload = read_stdin_json()
    if not payload:
        debug_log(cfg, "hook", status="empty_payload")
        return

    raw_event = str(payload.get("hook_event_name", "")).strip()
    event_aliases = {
        "beforesubmitprompt": "UserPromptSubmit",
        "stop": "Stop",
        "stopsuccess": "Stop",
        "stopfailure": "StopFailure",
        "sessionstart": "SessionStart",
        "sessionend": "SessionEnd",
        "pretooluse": "UserPromptSubmit",
        "posttooluse": "Stop",
        "subagentstop": "Stop",
    }
    event = event_aliases.get(raw_event.lower(), raw_event)
    if not event:
        debug_log(cfg, "hook", status="missing_event")
        return

    session_id = str(payload.get("session_id", "")).strip() or "default"
    now = datetime.now(timezone.utc).isoformat()

    busy = event == "UserPromptSubmit"
    idle_events = {"Stop", "StopFailure", "SessionEnd"}

    existing = load_state(session_id)
    started_at = now if busy else (existing.get("started_at_utc") if existing else None)

    state = {
        "session_id": session_id,
        "busy": busy,
        "started_at_utc": started_at,
        "updated_at_utc": now,
        "last_event": event,
        "cwd": payload.get("cwd"),
    }
    save_state(session_id, state)

    debug_log(cfg, "hook", event=event, session=session_id, busy=busy)
    try:
        if busy:
            start_overlay_daemon(session_id, cfg)
            debug_log(cfg, "daemon", action="start", session=session_id)
        elif event in idle_events or event == "SessionStart":
            stop_overlay_daemon(session_id)
            debug_log(cfg, "daemon", action="stop", session=session_id)
    except Exception as exc:
        debug_log(cfg, "hook_error", event=event, session=session_id, error=type(exc).__name__)


# ============================================================
# StatusLine (fallback)
# ============================================================

def ansi(code, text):
    return f"\033[{code}m{text}\033[0m"


def get_git_branch(directory):
    if not directory:
        return None
    try:
        r = subprocess.run(
            ["git", "-C", directory, "branch", "--show-current"],
            capture_output=True, text=True, timeout=3,
        )
        if r.returncode == 0:
            b = r.stdout.strip()
            return b if b else None
    except Exception:
        return None


def handle_statusline():
    payload = read_stdin_json()
    if not payload:
        return

    cfg = load_config().get("statusline", DEFAULT_CONFIG["statusline"])
    if not cfg.get("enabled", False):
        return

    session_id = payload.get("session_id", "")
    state = load_state(session_id)
    busy = state.get("busy", False) if state else False
    started_at = state.get("started_at_utc") if state else None

    sp, _sp_warn = get_spinner_for_statusline(cfg.get("spinner", "crt_bar"))
    if _sp_warn:
        debug_log(load_config(), "statusline", warning=_sp_warn)
    display = get_next_statusline_frame(sp, session_id)[0].strip() if busy else "[DONE]"

    elapsed = "00:00"
    if busy and started_at:
        try:
            secs = max(0, int((datetime.now(timezone.utc) - datetime.fromisoformat(started_at)).total_seconds()))
            elapsed = f"{secs // 60:02d}:{secs % 60:02d}"
        except Exception:
            pass

    phase = "RUN" if busy else "IDLE"
    ws = deep_get(payload, "workspace", default={})
    cwd = deep_get(ws, "current_dir") or payload.get("cwd", "")
    repo = Path(cwd).name if cwd else "no-dir"
    model = deep_get(payload, "model", "display_name") or deep_get(payload, "model", "id") or "Claude"
    branch = get_git_branch(cwd) if cfg.get("show_git", True) else None
    cost_usd = deep_get(payload, "cost", "total_cost_usd")
    cost = f"${float(cost_usd):.2f}" if cost_usd is not None and cfg.get("show_cost", True) else None

    lc, sc, pc = ("38;5;214", "1;32", "1;33") if busy else ("38;5;244",) * 3
    sep = ansi("38;5;238", "│")
    label = cfg.get("label", "CRT")

    parts = [ansi(lc, label), ansi(sc, display), ansi(pc, phase)]
    if cfg.get("show_elapsed", True):
        parts.append(ansi("38;5;250", elapsed))
    if cfg.get("show_model", True):
        parts.append(ansi("38;5;39", model))
    parts.append(ansi("38;5;111", repo))
    if branch:
        parts.append(ansi("38;5;180", f"git:{branch}"))
    if cost:
        parts.append(ansi("38;5;246", cost))

    if sys.platform == "win32":
        try:
            sys.stdout.reconfigure(encoding="utf-8")
        except Exception:
            pass
    sys.stdout.write(f" {sep} ".join(parts) + "\n")
    sys.stdout.flush()


# ============================================================
# List / Preview
# ============================================================

def list_spinners():
    groups = {}
    for name, sp in sorted(SPINNERS.items()):
        key = (sp["w"], sp["h"])
        groups.setdefault(key, []).append(name)

    for (w, h) in sorted(groups.keys(), key=lambda x: (x[0] * x[1], x[0])):
        names = groups[(w, h)]
        print(f"\n  {ansi('1;36', f'{w}x{h}')} ({len(names)} variants)")
        for name in names:
            sp = SPINNERS[name]
            sample = sp["frames"][0][0][:24]
            print(f"    {ansi('1;33', name):30s}  {sp['ms']:3d}ms  {sample}")
    print()


def preview_spinner(name, duration=5.0):
    sp = get_spinner(name)
    h = sp["h"]
    print(f"  {name} ({sp['w']}x{sp['h']}, {sp['ms']}ms, {len(sp['frames'])} frames)\n")

    if h == 1:
        try:
            end = time.time() + duration
            while time.time() < end:
                sys.stdout.write(f"\r  {get_frame_at(sp)[0]}  ")
                sys.stdout.flush()
                time.sleep(sp["ms"] / 1000.0)
        except KeyboardInterrupt:
            pass
        print()
    else:
        for _ in range(h):
            print()
        try:
            end = time.time() + duration
            while time.time() < end:
                frame = get_frame_at(sp)
                sys.stdout.write(f"\033[{h}A")
                for line in frame:
                    sys.stdout.write(f"\r  {line}\033[K\n")
                sys.stdout.flush()
                time.sleep(sp["ms"] / 1000.0)
        except KeyboardInterrupt:
            pass
        print()


# ============================================================
# Main
# ============================================================

def main():
    parser = argparse.ArgumentParser(
        description="Claude Code Spinner - retro ASCII overlay for Claude Code terminals.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
quick start:
  python3 %(prog)s enable            register hooks, then restart Claude Code
  python3 %(prog)s disable           remove hooks, then restart Claude Code
  python3 %(prog)s status            show current state

  python3 %(prog)s list              browse available spinner variants
  python3 %(prog)s preview variation_1 --duration 8
  python3 %(prog)s preview variation_3

debugging:
  python3 %(prog)s debug-on          enable hook/daemon logging to debug.log
  python3 %(prog)s debug-off         disable logging
  python3 %(prog)s log               print last 50 log lines
  python3 %(prog)s log -n 100        print last 100 log lines
  python3 %(prog)s log --clear       erase the log file

  CLAUDE_SPINNER_DEBUG=1 overrides config and forces debug on for one run.

config: ~/.claude/claude-code-spinner/config.json
log:    ~/.claude/claude-code-spinner/debug.log
""",
    )
    sub = parser.add_subparsers(dest="mode", metavar="<command>")

    sub.add_parser(
        "enable",
        help="register hooks in ~/.claude/settings.json (activates spinner)",
        description="Register Claude Code lifecycle hooks and restart Claude Code to activate the spinner.",
    )
    sub.add_parser(
        "disable",
        help="remove hooks from ~/.claude/settings.json (deactivates spinner)",
        description="Remove spinner hooks from settings.json. Restart Claude Code to deactivate.",
    )
    sub.add_parser(
        "status",
        help="show hook registration state and current configuration",
        description="Print whether hooks are registered, the active config, and any running daemons.",
    )
    sub.add_parser(
        "hook",
        help="handle a Claude Code lifecycle event (called by hooks in settings.json)",
        description="Read a hook event payload from stdin and start/stop the overlay daemon accordingly.",
    )
    sub.add_parser(
        "statusline",
        help="emit a status-line string for Claude Code's status bar",
        description="Read session state and print a formatted status-line string to stdout.",
    )

    p = sub.add_parser(
        "overlay",
        help="run the spinner overlay daemon (launched automatically by 'hook')",
        description="Open the TTY directly and render the spinner. Normally started by the hook subcommand.",
    )
    p.add_argument("--session-id", default="default", metavar="ID",
                   help="session identifier used to locate the PID file (default: default)")

    p = sub.add_parser(
        "kill",
        help="kill the running overlay daemon for a session",
        description="Send SIGTERM to the overlay daemon identified by --session-id.",
    )
    p.add_argument("--session-id", default="default", metavar="ID",
                   help="session identifier (default: default)")

    sub.add_parser(
        "list",
        help="list available spinner variants",
        description="Print all spinner variants grouped by size with a sample frame.",
    )

    p = sub.add_parser(
        "preview",
        help="preview a spinner live in the terminal",
        description="Animate the named spinner in the current terminal for --duration seconds.",
    )
    p.add_argument("name", nargs="?", default="variation_1", metavar="NAME",
                   help="spinner name, e.g. variation_1 (default: variation_1)")
    p.add_argument("--duration", type=float, default=5.0, metavar="SECS",
                   help="how long to run the preview (default: 5.0)")

    sub.add_parser(
        "init-config",
        help="write default config.json (skips if it already exists)",
        description="Create ~/.claude/claude-code-spinner/config.json with default values.",
    )
    sub.add_parser(
        "debug-on",
        help='set "debug": true in config.json - enables hook/daemon logging',
        description='Set "debug": true in config.json. Log entries are written to debug.log on every hook event.',
    )
    sub.add_parser(
        "debug-off",
        help='set "debug": false in config.json - disables logging',
        description='Set "debug": false in config.json.',
    )
    p = sub.add_parser(
        "log",
        help="print recent lines from the debug log",
        description="Print the last N lines of debug.log. Use --clear to erase the file.",
    )
    p.add_argument("-n", "--lines", type=int, default=50, metavar="N",
                   help="number of lines to show from the end (default: 50)")
    p.add_argument("--clear", action="store_true",
                   help="erase the log file instead of printing it")

    args = parser.parse_args()

    if args.mode == "hook":
        handle_hook()
    elif args.mode == "statusline":
        handle_statusline()
    elif args.mode == "overlay":
        run_overlay(args.session_id, load_config())
    elif args.mode == "kill":
        stop_overlay_daemon(args.session_id)
    elif args.mode == "list":
        list_spinners()
    elif args.mode == "preview":
        preview_spinner(args.name, args.duration)
    elif args.mode == "init-config":
        p = get_config_dir() / "config.json"
        if not p.exists():
            p.write_text(json.dumps(DEFAULT_CONFIG, indent=2), encoding="utf-8")
            print(f"Created: {p}")
        else:
            print(f"Exists: {p}")
    elif args.mode == "enable":
        handle_enable()
    elif args.mode == "disable":
        handle_disable()
    elif args.mode == "status":
        handle_status()
    elif args.mode == "debug-on":
        handle_debug_on()
    elif args.mode == "debug-off":
        handle_debug_off()
    elif args.mode == "log":
        handle_log(lines=args.lines, clear=args.clear)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
