#!/home/feds/venv/bin/python3
"""
cc.py - Claude Code Replica Master Entrypoint
"The Most Insane Possible Coding AI"

Run with:
  python3 cc.py [prompt]
  python3 cc.py --insane
"""

import sys
import os
import argparse

# Ensure local package is in path
sys.path.insert(0, os.path.abspath(os.path.dirname(__file__)))

from claude_replica import cli
from claude_replica.config import ConfigManager

def main():
    parser = argparse.ArgumentParser(description="Claude Code Replica - Master Entrypoint")
    parser.add_argument("prompt", nargs="*", help="Direct prompt to execute")
    parser.add_argument("--insane", "--focus", action="store_true", dest="insane", help="Enable Maximum Focus mode: Autonomous auto-fixing and max reasoning depth.")
    parser.add_argument("--boost", action="store_true", help="Enable Boost mode (Alias for Maximum Focus)")
    parser.add_argument("--thinking", choices=["on", "off"], help="Enable or disable thinking/reasoning mode")
    parser.add_argument("--spinner", help="Set ASCII spinner")
    parser.add_argument("-d", "--dir", default=os.getcwd(), help="Target workspace directory")
    
    # Parse known args so we can pass the rest to the original CLI if needed
    args, unknown = parser.parse_known_args()

    # Modify sys.argv so cli.main() behaves correctly
    new_argv = [sys.argv[0]]
    if args.dir:
        new_argv.extend(["-d", args.dir])
    if args.thinking:
        new_argv.extend(["--thinking", args.thinking])
    if args.spinner:
        new_argv.extend(["--spinner", args.spinner])
    if args.prompt:
        new_argv.extend(args.prompt)
    new_argv.extend(unknown)
    
    sys.argv = new_argv

    cfg = ConfigManager()

    if args.insane or args.boost:
        print("\n\033[1;36m[SYSTEM] Maximum Focus Mode Activated: Autonomous Error Correction & Max Effort Enabled\033[0m\n")
        cfg.set("insane_mode", True)
        cfg.set("auto_confirm", True)
        cfg.set("dangerously_skip_permissions", True)
        cfg.set_effort("extra high")
    
    cli.main()

if __name__ == "__main__":
    main()
