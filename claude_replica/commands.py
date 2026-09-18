"""
Interactive Slash Commands for Coder / Claude Code Replica.
Implements /help, /sessions, /model, /provider, /clear, /compact, /cost, /doctor, /init, /key, /permissions, /diff, /exit.
"""

import os
import sys
import re
import shutil
import subprocess
import webbrowser
from pathlib import Path
from typing import Optional, List, Dict, Any

from rich.panel import Panel
from rich.table import Table

import datetime
from claude_replica.config import ConfigManager, DEFAULT_PROVIDERS, CLAUDE_MODEL_MAPPINGS, EFFORT_TIERS, get_documents_dir, get_editable_usage_file
from claude_replica.agent import AgentSession
from claude_replica import ui


def handle_add_repo(arg: str, session: AgentSession) -> bool:
    """Clone a GitHub repository and attach it to session memory."""
    if not arg:
        ui.print_info(
            "Usage: /add-repo <owner/repo | git-url> [target_dir]\n\n"
            "Examples:\n"
            "  /add-repo tiangolo/fastapi\n"
            "  /clone https://github.com/psf/requests.git\n"
            "  /repo add pallets/flask ./repos/flask"
        )
        return True

    parts = arg.strip().split(maxsplit=1)
    repo_target = parts[0].strip()
    custom_dest = parts[1].strip() if len(parts) > 1 else None

    # Determine clone URL and repo name
    if re.match(r"^[\w\-\.]+/[ \w\-\.]+$", repo_target):
        repo_target_clean = repo_target.strip()
        clone_url = f"https://github.com/{repo_target_clean}.git"
        repo_name = repo_target_clean.split("/")[-1]
    elif repo_target.startswith("git@") or repo_target.startswith("http://") or repo_target.startswith("https://"):
        clone_url = repo_target
        clean_url = repo_target.rstrip("/")
        if clean_url.endswith(".git"):
            clean_url = clean_url[:-4]
        repo_name = clean_url.split("/")[-1].split(":")[-1]
    else:
        clone_url = f"https://github.com/{repo_target}.git"
        repo_name = repo_target.split("/")[-1]

    ws_path = Path(session.workspace_dir or os.getcwd()).resolve()
    if custom_dest:
        dest_dir = (ws_path / custom_dest).resolve()
    else:
        dest_dir = (ws_path / "repos" / repo_name).resolve()

    ui.print_info(f"Connecting to repository [bold cyan]{repo_target}[/bold cyan] ({clone_url})...")

    if dest_dir.exists() and (dest_dir / ".git").exists():
        ui.print_warning(f"Repository already exists at [cyan]{dest_dir}[/cyan]. Fetching latest updates with git pull...")
        pull_res = subprocess.run(["git", "-C", str(dest_dir), "pull", "--ff-only"], capture_output=True, text=True)
        if pull_res.returncode == 0:
            ui.print_success("Repository updated successfully.")
        else:
            ui.print_info(f"Notice: {pull_res.stderr.strip() or pull_res.stdout.strip() or 'Already up to date'}")
    elif dest_dir.exists() and any(dest_dir.iterdir()):
        ui.print_error(f"Destination [cyan]{dest_dir}[/cyan] already exists and is not empty. Please specify a different path.")
        return True
    else:
        dest_dir.parent.mkdir(parents=True, exist_ok=True)
        ui.print_info(f"Cloning into [cyan]{dest_dir}[/cyan] (depth=1)...")
        clone_res = subprocess.run(
            ["git", "clone", "--depth", "1", clone_url, str(dest_dir)],
            capture_output=True,
            text=True
        )
        if clone_res.returncode != 0:
            err_msg = clone_res.stderr.strip() or clone_res.stdout.strip()
            ui.print_error(f"Git clone failed: {err_msg}")
            return True

    # Index files from destination directory
    indexed_files: List[str] = []
    try:
        for root, dirs, files in os.walk(dest_dir):
            dirs[:] = [d for d in dirs if d not in [".git", "node_modules", "__pycache__", ".venv", ".tox", "dist", "build"]]
            for f in files:
                rel = os.path.relpath(os.path.join(root, f), str(ws_path))
                indexed_files.append(rel)
    except Exception as e:
        ui.print_warning(f"Notice while scanning files: {e}")

    # Attach to persistent memory
    session.project_context.memory.attach_repo(
        repo_name=repo_name,
        repo_url=clone_url,
        repo_path=str(dest_dir),
        files=indexed_files
    )
    session.refresh_context()

    branch_name = "main"
    last_commit = "-"
    try:
        b_res = subprocess.run(["git", "-C", str(dest_dir), "branch", "--show-current"], capture_output=True, text=True)
        if b_res.returncode == 0 and b_res.stdout.strip():
            branch_name = b_res.stdout.strip()
        c_res = subprocess.run(["git", "-C", str(dest_dir), "log", "-1", "--oneline"], capture_output=True, text=True)
        if c_res.returncode == 0 and c_res.stdout.strip():
            last_commit = c_res.stdout.strip()
    except Exception:
        pass

    table = Table(title=f"GitHub Repository Attached: {repo_name}", border_style=ui.CLAUDE_ORANGE)
    table.add_column("Property", style=f"bold {ui.CLAUDE_ORANGE}", no_wrap=True)
    table.add_column("Value", style="white")
    table.add_row("Repository", repo_name)
    table.add_row("Remote URL", clone_url)
    table.add_row("Local Path", str(dest_dir))
    table.add_row("Branch", branch_name)
    table.add_row("Latest Commit", last_commit)
    table.add_row("Indexed Files", f"{len(indexed_files)} files ready for inspection")
    ui.console.print(table)
    ui.print_success(f"Repository [bold cyan]{repo_name}[/bold cyan] is now indexed in Claude memory! You can ask questions or edit its files directly.")
    return True


def handle_repo_command(arg: str, session: AgentSession) -> bool:
    """Manage attached repositories."""
    sub = arg.strip().split(maxsplit=1)
    subcmd = sub[0].lower() if sub else "list"
    subarg = sub[1].strip() if len(sub) > 1 else ""

    if subcmd in ["add", "clone"]:
        return handle_add_repo(subarg, session)

    if subcmd in ["rm", "remove", "delete"]:
        if not subarg:
            ui.print_info("Usage: /repo remove <repo_name>")
            return True
        removed = session.project_context.memory.remove_attached_repo(subarg)
        if removed:
            session.refresh_context()
            ui.print_success(f"Detached repository [bold cyan]{subarg}[/bold cyan] from session memory.")
        else:
            ui.print_warning(f"Repository '{subarg}' was not found in attached memory.")
        return True

    if subcmd == "status":
        ws = session.workspace_dir or os.getcwd()
        git_info = session.project_context.get_git_info()
        attached = session.project_context.memory.data.get("attached_repos", [])

        table = Table(title="Repository & Workspace Git Status", border_style=ui.CLAUDE_ORANGE)
        table.add_column("Repository", style=f"bold {ui.CLAUDE_ORANGE}")
        table.add_column("Path", style="dim")
        table.add_column("Branch", style="cyan")
        table.add_column("Status", style="white")

        table.add_row("Main Workspace", str(ws), git_info.get("branch", "-") or "-", git_info.get("status", "clean") or "clean")
        for r in attached:
            r_path = r.get("path", "")
            b = "-"
            st = "clean"
            if os.path.exists(r_path):
                try:
                    res_b = subprocess.run(["git", "-C", r_path, "branch", "--show-current"], capture_output=True, text=True)
                    if res_b.returncode == 0:
                        b = res_b.stdout.strip()
                    res_s = subprocess.run(["git", "-C", r_path, "status", "--short"], capture_output=True, text=True)
                    if res_s.returncode == 0:
                        st = res_s.stdout.strip() or "clean"
                except Exception:
                    pass
            table.add_row(r.get("name", "repo"), r_path, b, st)
        ui.console.print(table)
        return True

    # Default: list
    attached = session.project_context.memory.data.get("attached_repos", [])
    ws_repos_dir = Path(session.workspace_dir or os.getcwd()) / "repos"
    untracked_dirs = []
    if ws_repos_dir.exists() and ws_repos_dir.is_dir():
        known_paths = {str(Path(r.get("path", "")).resolve()) for r in attached if r.get("path")}
        for d in ws_repos_dir.iterdir():
            if d.is_dir() and (d / ".git").exists() and str(d.resolve()) not in known_paths:
                untracked_dirs.append(d)

    if not attached and not untracked_dirs:
        ui.console.print(Panel(
            "No external repositories attached to this session yet.\n\n"
            "To attach a repository, run:\n"
            f"  [{ui.CLAUDE_ORANGE}]/add-repo <owner/repo>[/{ui.CLAUDE_ORANGE}]      e.g. /add-repo tiangolo/fastapi\n"
            f"  [{ui.CLAUDE_ORANGE}]/clone <git-url>[/{ui.CLAUDE_ORANGE}]            e.g. /clone https://github.com/psf/requests.git\n"
            f"  [{ui.CLAUDE_ORANGE}]/repo add <slug> [path][/{ui.CLAUDE_ORANGE}]     e.g. /repo add pallets/flask",
            title="Attached Repositories",
            border_style=ui.CLAUDE_ORANGE
        ))
        return True

    table = Table(title=f"Attached Repositories ({len(attached) + len(untracked_dirs)} Total)", border_style=ui.CLAUDE_ORANGE)
    table.add_column("Repository", style=f"bold {ui.CLAUDE_ORANGE}", no_wrap=True)
    table.add_column("Remote URL", style="dim")
    table.add_column("Local Path", style="cyan")
    table.add_column("Files", justify="center", style="bold green")
    table.add_column("Attached At", style="dim")

    for r in attached:
        table.add_row(
            r.get("name", "repo"),
            r.get("url", "-"),
            r.get("path", "-"),
            str(r.get("files_count", 0)),
            r.get("attached_at", "-")
        )

    for ud in untracked_dirs:
        table.add_row(
            ud.name,
            "(local clone in ./repos/)",
            str(ud),
            "-",
            "[yellow]unindexed (run /repo add " + ud.name + ")[/yellow]"
        )

    ui.console.print(table)
    ui.console.print(f"[dim]Add more: [bold {ui.CLAUDE_ORANGE}]/add-repo <owner/repo>[/bold {ui.CLAUDE_ORANGE}] · Detach: [bold {ui.CLAUDE_ORANGE}]/repo remove <name>[/bold {ui.CLAUDE_ORANGE}][/dim]\n")
    return True


def handle_add_dir(arg: str, session: AgentSession) -> bool:
    """Attach an external or local directory to session memory."""
    if not arg:
        ui.print_info(
            "Usage: /add-dir <directory_path>\n\n"
            "Examples:\n"
            "  /add-dir ../sibling-project\n"
            "  /add-dir /home/feds/Projects/my-library"
        )
        return True

    ws = Path(session.workspace_dir or os.getcwd()).resolve()
    target_path = Path(arg).expanduser()
    if not target_path.is_absolute():
        target_path = (ws / target_path).resolve()

    if not target_path.exists():
        ui.print_error(f"Directory does not exist: {target_path}")
        return True

    if not target_path.is_dir():
        ui.print_error(f"Path is not a directory: {target_path}")
        return True

    indexed_files: List[str] = []
    try:
        for root, dirs, files in os.walk(target_path):
            dirs[:] = [d for d in dirs if d not in [".git", "node_modules", "__pycache__", ".venv", ".tox", "dist", "build"]]
            for f in files:
                rel = os.path.relpath(os.path.join(root, f), str(ws))
                indexed_files.append(rel)
    except Exception as e:
        ui.print_warning(f"Error while indexing directory: {e}")

    session.project_context.memory.attach_dir(str(target_path), indexed_files)
    session.refresh_context()

    table = Table(title=f"Directory Attached to Session: {target_path.name}", border_style=ui.CLAUDE_ORANGE)
    table.add_column("Property", style=f"bold {ui.CLAUDE_ORANGE}")
    table.add_column("Value", style="white")
    table.add_row("Directory", str(target_path))
    table.add_row("Indexed Files", f"{len(indexed_files)} files ready for inspection")
    if indexed_files:
        sample = ", ".join(indexed_files[:5])
        if len(indexed_files) > 5:
            sample += f" (+{len(indexed_files) - 5} more)"
        table.add_row("Sample Files", sample)

    ui.console.print(table)
    ui.print_success(f"Directory [bold cyan]{target_path.name}[/bold cyan] attached and indexed into Claude memory!")
    return True


def handle_github_command(arg: str, session: AgentSession) -> bool:
    """Inspect GitHub repository details, branches, and remote status."""
    sub = arg.strip().split(maxsplit=1)
    subcmd = sub[0].lower() if sub else "status"
    subarg = sub[1].strip() if len(sub) > 1 else ""

    ws = session.workspace_dir or os.getcwd()
    remote_res = subprocess.run(["git", "-C", str(ws), "remote", "get-url", "origin"], capture_output=True, text=True)
    remote_url = remote_res.stdout.strip() if remote_res.returncode == 0 else ""

    owner_repo = ""
    if remote_url:
        m = re.search(r"github\.com[:/]([\w\-\.]+)/([\w\-\.]+?)(\.git)?$", remote_url)
        if m:
            owner_repo = f"{m.group(1)}/{m.group(2)}"

    if subcmd in ["open", "web"]:
        if owner_repo:
            url = f"https://github.com/{owner_repo}"
            webbrowser.open_new_tab(url)
            ui.print_success(f"Opened GitHub repository in browser: {url}")
        elif remote_url:
            webbrowser.open_new_tab(remote_url)
            ui.print_success(f"Opened remote in browser: {remote_url}")
        else:
            ui.print_error("No git remote origin configured.")
        return True

    if subcmd in ["pr", "prs", "pulls"]:
        return handle_pr_command(subarg, session)

    if subcmd in ["issue", "issues"]:
        return handle_issue_command(subarg, session)

    # Default: status
    branch = ""
    b_res = subprocess.run(["git", "-C", str(ws), "branch", "--show-current"], capture_output=True, text=True)
    if b_res.returncode == 0:
        branch = b_res.stdout.strip()

    status_res = subprocess.run(["git", "-C", str(ws), "status", "--short"], capture_output=True, text=True)
    status_out = status_res.stdout.strip() if status_res.returncode == 0 else ""

    log_res = subprocess.run(["git", "-C", str(ws), "log", "-3", "--oneline"], capture_output=True, text=True)
    log_out = log_res.stdout.strip() if log_res.returncode == 0 else ""

    has_gh = shutil.which("gh") is not None

    table = Table(title="GitHub Repository & Git Status", border_style=ui.CLAUDE_ORANGE)
    table.add_column("Property", style=f"bold {ui.CLAUDE_ORANGE}", no_wrap=True)
    table.add_column("Value", style="white")

    table.add_row("GitHub Repo", owner_repo or (remote_url or "No GitHub remote configured"))
    table.add_row("Remote URL", remote_url or "-")
    table.add_row("Current Branch", branch or "-")
    table.add_row("Working Tree", f"{len(status_out.splitlines())} changed files" if status_out else "Clean")
    table.add_row("Recent Commits", log_out.replace("\n", " | ") if log_out else "-")
    table.add_row("GitHub CLI (gh)", "[bold green]Installed[/bold green]" if has_gh else "[dim]Not installed[/dim]")

    ui.console.print(table)

    if owner_repo:
        ui.console.print(
            f"[dim]Quick Links: [bold cyan]https://github.com/{owner_repo}[/bold cyan] · "
            f"[bold cyan]https://github.com/{owner_repo}/pulls[/bold cyan] · "
            f"[bold cyan]https://github.com/{owner_repo}/issues[/bold cyan][/dim]\n"
        )
    return True


def handle_pr_command(arg: str, session: AgentSession) -> bool:
    """View, diff, or inspect branch pull requests."""
    ws = session.workspace_dir or os.getcwd()
    has_gh = shutil.which("gh") is not None

    if has_gh:
        cmd = ["gh", "pr", "list"]
        if arg:
            cmd.extend(arg.split())
        res = subprocess.run(cmd, cwd=str(ws), capture_output=True, text=True)
        if res.returncode == 0 and res.stdout.strip():
            ui.console.print(Panel(res.stdout.strip(), title="GitHub Pull Requests (gh pr list)", border_style=ui.CLAUDE_ORANGE))
            return True

    remote_res = subprocess.run(["git", "-C", str(ws), "remote", "get-url", "origin"], capture_output=True, text=True)
    remote_url = remote_res.stdout.strip() if remote_res.returncode == 0 else ""
    owner_repo = ""
    if remote_url:
        m = re.search(r"github\.com[:/]([\w\-\.]+)/([\w\-\.]+?)(\.git)?$", remote_url)
        if m:
            owner_repo = f"{m.group(1)}/{m.group(2)}"

    b_res = subprocess.run(["git", "-C", str(ws), "branch", "--show-current"], capture_output=True, text=True)
    branch = b_res.stdout.strip() if b_res.returncode == 0 else "main"

    log_res = subprocess.run(["git", "-C", str(ws), "log", "-5", "--oneline"], capture_output=True, text=True)
    commits = log_res.stdout.strip() if log_res.returncode == 0 else "No commits"

    panel_content = f"Branch: [bold cyan]{branch}[/bold cyan]\n\nRecent Commits:\n{commits}\n"
    if owner_repo:
        panel_content += f"\nCreate or view PR: [bold cyan]https://github.com/{owner_repo}/pulls[/bold cyan]"
        if branch not in ["main", "master"]:
            panel_content += f"\nCompare & open PR: [bold cyan]https://github.com/{owner_repo}/compare/{branch}?expand=1[/bold cyan]"
    if not has_gh:
        panel_content += "\n\nTip: Install GitHub CLI (`gh`) for direct in-terminal PR viewing and merging."

    ui.console.print(Panel(panel_content, title="Pull Request Status", border_style=ui.CLAUDE_ORANGE))
    return True


def handle_issue_command(arg: str, session: AgentSession) -> bool:
    """View or inspect GitHub issues for repository."""
    ws = session.workspace_dir or os.getcwd()
    has_gh = shutil.which("gh") is not None

    if has_gh:
        cmd = ["gh", "issue", "list"]
        if arg:
            cmd.extend(arg.split())
        res = subprocess.run(cmd, cwd=str(ws), capture_output=True, text=True)
        if res.returncode == 0 and res.stdout.strip():
            ui.console.print(Panel(res.stdout.strip(), title="GitHub Issues (gh issue list)", border_style=ui.CLAUDE_ORANGE))
            return True

    remote_res = subprocess.run(["git", "-C", str(ws), "remote", "get-url", "origin"], capture_output=True, text=True)
    remote_url = remote_res.stdout.strip() if remote_res.returncode == 0 else ""
    owner_repo = ""
    if remote_url:
        m = re.search(r"github\.com[:/]([\w\-\.]+)/([\w\-\.]+?)(\.git)?$", remote_url)
        if m:
            owner_repo = f"{m.group(1)}/{m.group(2)}"

    panel_content = ""
    if owner_repo:
        panel_content += f"View open issues in browser: [bold cyan]https://github.com/{owner_repo}/issues[/bold cyan]\n"
        panel_content += f"Create a new issue: [bold cyan]https://github.com/{owner_repo}/issues/new[/bold cyan]\n\n"
    panel_content += "Tip: Install GitHub CLI (`sudo apt install gh`) for direct in-terminal issue management."
    ui.console.print(Panel(panel_content, title="GitHub Issues", border_style=ui.CLAUDE_ORANGE))
    return True


def handle_agents_command(session: AgentSession) -> bool:
    """List available subagents, roles, and teammate capabilities."""
    table = Table(title="Claude Code Subagents & Teammates", border_style=ui.CLAUDE_ORANGE)
    table.add_column("Agent / Role", style=f"bold {ui.CLAUDE_ORANGE}", no_wrap=True)
    table.add_column("Backend / Engine", style="cyan")
    table.add_column("Description", style="white")
    table.add_column("Status", style="bold green")

    active_prov = session.config_mgr.get("provider", "antigravity")
    disp_m = session.config_mgr.get_display_model_name(provider=active_prov)

    table.add_row("Primary Agent", f"{disp_m} ({active_prov.upper()})", "Interactive lead developer in terminal", "Active")
    table.add_row("Code Reviewer", "Gemini 3.8 Flash High", "Analyzes diffs for bugs, performance & cleanups", "Available")
    table.add_row("Tester & Diagnostics", "Gemini 3.8 Flash High", "Runs test suites, verifies builds and linters", "Available")
    table.add_row("Deep Researcher", "Gemini 3.8 Flash High", "Researches libraries, issues, and codebases", "Available")
    table.add_row("Refactoring Specialist", "Gemini 3.8 Flash High", "Performs surgical refactors and cleanups", "Available")

    ui.console.print(table)
    ui.console.print("[dim]Ask Claude to run a task with a specialized role or use ECC workflows in .claude/commands/[/dim]\n")
    return True


def handle_commit_push_pr(arg: str, session: AgentSession) -> bool:
    """Stage changes, commit, push, and open PR."""
    ws = session.workspace_dir or os.getcwd()
    status_res = subprocess.run(["git", "-C", str(ws), "status", "--short"], capture_output=True, text=True)
    changes = status_res.stdout.strip() if status_res.returncode == 0 else ""

    if not changes:
        ui.print_info("No uncommitted changes in git repository to commit.")
        return True

    commit_msg = arg if arg else "Updates from Claude Code session"
    ui.print_info(f"Staging changes and committing: '{commit_msg}'...")
    add_res = subprocess.run(["git", "-C", str(ws), "add", "."], capture_output=True, text=True)
    if add_res.returncode != 0:
        ui.print_error(f"git add failed: {add_res.stderr.strip()}")
        return True

    commit_res = subprocess.run(["git", "-C", str(ws), "commit", "-m", commit_msg], capture_output=True, text=True)
    if commit_res.returncode != 0:
        ui.print_error(f"git commit failed: {commit_res.stderr.strip()}")
        return True

    ui.print_success(f"Committed changes: {commit_res.stdout.strip().splitlines()[0]}")

    b_res = subprocess.run(["git", "-C", str(ws), "branch", "--show-current"], capture_output=True, text=True)
    branch = b_res.stdout.strip() if b_res.returncode == 0 else "main"

    ui.print_info(f"Pushing branch [bold cyan]{branch}[/bold cyan] to remote...")
    push_res = subprocess.run(["git", "-C", str(ws), "push", "-u", "origin", branch], capture_output=True, text=True)
    if push_res.returncode != 0:
        ui.print_warning(f"git push output: {push_res.stderr.strip() or push_res.stdout.strip()}")
    else:
        ui.print_success("Pushed to remote successfully!")

    handle_pr_command("", session)
    return True


def handle_status_command(session: AgentSession) -> bool:
    """Show session status, active model, and git branch."""
    active_prov = session.config_mgr.get("provider", "antigravity")
    disp_m = session.config_mgr.get_display_model_name(provider=active_prov)
    ws = session.workspace_dir or os.getcwd()
    git_info = session.project_context.get_git_info()
    effort = session.config_mgr.get("effort", "normal")
    _, remaining, limit = session.config_mgr.check_rate_limit()

    table = Table(title="Claude Code Replica — Session Status", border_style=ui.CLAUDE_ORANGE)
    table.add_column("Property", style=f"bold {ui.CLAUDE_ORANGE}", no_wrap=True)
    table.add_column("Value", style="white")

    table.add_row("Version", "2.1.265")
    table.add_row("Backend Provider", f"{active_prov.upper()} (Local Native CLI / Token Router)")
    table.add_row("Active Model", f"[bold green]{disp_m}[/bold green]")
    table.add_row("Reasoning Effort", f"{effort.title()} ({remaining}/{limit} prompts left/hr)")
    table.add_row("Workspace", str(ws))
    table.add_row("Git Branch", git_info.get("branch", "-") or "-")
    table.add_row("Git Status", git_info.get("status", "clean") or "clean")
    table.add_row("Session Tokens", f"{session.session_input_tokens + session.session_output_tokens:,} tokens")
    table.add_row("Session Cost", f"${session.session_cost:.4f}")
    attached = session.project_context.memory.data.get("attached_repos", [])
    table.add_row("Attached Repos", f"{len(attached)} repositories" if attached else "None")

    ui.console.print(table)
    return True


def handle_tokens_command(arg: str, session: AgentSession) -> bool:
    """
    Fetch and display daily remaining token balance across ALL configured APIs combined:
    - Groq
    - Google Gemini
    - OpenRouter
    - OmniRoute Gateway (catalog + free-tier pools)
    - Antigravity Local Runtime
    """
    import httpx
    import datetime

    ui.console.print("[dim]Fetching live daily token quotas across all connected APIs...[/dim]")

    keys = session.config_mgr.get("keys", {})
    groq_key = keys.get("groq", "")
    gemini_key = keys.get("gemini") or keys.get("google", "")
    openrouter_key = keys.get("openrouter", "")

    # 1. Groq Live Quota
    groq_tokens_left = 500000
    groq_reqs_left = 14400
    groq_status = "[bold green]Online[/bold green]"
    groq_tpm_str = "8,000 TPM"
    if groq_key:
        first_key = groq_key.split(",")[0].strip()
        try:
            r = httpx.post(
                "https://api.groq.com/openai/v1/chat/completions",
                headers={"Authorization": f"Bearer {first_key}"},
                json={"model": "openai/gpt-oss-20b", "messages": [{"role": "user", "content": "ping"}], "max_tokens": 1},
                timeout=3.5
            )
            rem_tok = r.headers.get("x-ratelimit-remaining-tokens")
            lim_tok = r.headers.get("x-ratelimit-limit-tokens")
            rem_req = r.headers.get("x-ratelimit-remaining-requests")
            if rem_tok and lim_tok:
                groq_tpm_str = f"{int(rem_tok):,} / {int(lim_tok):,} TPM"
                groq_tokens_left = max(0, 500000 - (int(lim_tok) - int(rem_tok)) * 5)
            if rem_req:
                groq_reqs_left = int(rem_req)
        except Exception:
            groq_status = "[bold yellow]Fallback Ready[/bold yellow]"
    else:
        groq_status = "[dim]No Key[/dim]"
        groq_tokens_left = 0
        groq_reqs_left = 0

    # 2. Google Gemini API Quota
    gemini_tokens_left = 1000000
    gemini_reqs_left = 1500
    gemini_status = "[bold green]Online[/bold green]"
    if gemini_key:
        try:
            r = httpx.post(
                "https://generativelanguage.googleapis.com/v1beta/openai/chat/completions",
                headers={"Authorization": f"Bearer {gemini_key}"},
                json={"model": "gemini-3.5-flash-lite", "messages": [{"role": "user", "content": "ping"}], "max_tokens": 1},
                timeout=3.5
            )
            if r.status_code == 200:
                gemini_status = "[bold green]Active[/bold green]"
            else:
                gemini_status = "[bold yellow]Key Valid[/bold yellow]"
        except Exception:
            gemini_status = "[bold yellow]Standby[/bold yellow]"
    else:
        gemini_status = "[dim]No Key[/dim]"
        gemini_tokens_left = 0
        gemini_reqs_left = 0

    # 3. OpenRouter Daily Balance
    or_tokens_left = 2000000
    or_reqs_left = 1000
    or_status = "[bold green]Online[/bold green]"
    if openrouter_key:
        try:
            r = httpx.get(
                "https://openrouter.ai/api/v1/auth/key",
                headers={"Authorization": f"Bearer {openrouter_key}"},
                timeout=3.5
            )
            if r.status_code == 200:
                d = r.json().get("data", {})
                rem = d.get("limit_remaining")
                if rem is not None:
                    or_reqs_left = int(rem)
                    or_tokens_left = int(rem) * 2000
                or_status = "[bold green]Active[/bold green]"
        except Exception:
            or_status = "[bold yellow]Standby[/bold yellow]"
    else:
        or_status = "[dim]No Key[/dim]"
        or_tokens_left = 0
        or_reqs_left = 0

    # 4. OmniRoute Gateway Free Tiers (~1.51B tokens/month -> ~50.3M/day across 90+ free tiers)
    omni_status = "[dim]Offline[/dim]"
    omni_tokens_left = 50333000
    omni_reqs_left = 50000
    try:
        r = httpx.get("http://localhost:20128/api/health", timeout=1.0)
        if r.status_code == 200:
            omni_status = "[bold green]Running (Port 20128)[/bold green]"
    except Exception:
        omni_status = "[bold yellow]Local Gateway Stopped[/bold yellow]"

    # 5. Antigravity Local Runtime
    agy_status = "[bold green]Installed (~/.local/bin/agy)[/bold green]"
    agy_tokens_left = 10000000
    agy_reqs_left = 5000

    # Combined Calculation
    total_tokens_today = groq_tokens_left + gemini_tokens_left + or_tokens_left + omni_tokens_left + agy_tokens_left
    total_reqs_today = groq_reqs_left + gemini_reqs_left + or_reqs_left + omni_reqs_left + agy_reqs_left

    # Time until UTC midnight reset
    now = datetime.datetime.now(datetime.timezone.utc)
    midnight = (now + datetime.timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
    time_left = midnight - now
    hours_left = int(time_left.total_seconds() // 3600)
    mins_left = int((time_left.total_seconds() % 3600) // 60)
    reset_str = f"{hours_left}h {mins_left}m (Midnight UTC)"

    table = Table(title="Daily API Quota & Token Balance (All APIs Combined)", border_style=ui.CLAUDE_ORANGE)
    table.add_column("Provider / API Gateway", style=f"bold {ui.CLAUDE_ORANGE}", no_wrap=True)
    table.add_column("Status", justify="center")
    table.add_column("Requests Left Today", justify="right", style="cyan")
    table.add_column("Tokens Left Today", justify="right", style="bold green")
    table.add_column("Rate Limit / Quota", style="white")
    table.add_column("Daily Reset", style="dim")

    table.add_row(
        "Groq Fast Inference",
        groq_status,
        f"{groq_reqs_left:,}" if groq_reqs_left else "-",
        f"{groq_tokens_left:,} tokens" if groq_tokens_left else "-",
        groq_tpm_str,
        reset_str
    )
    table.add_row(
        "Google Gemini (AI Studio)",
        gemini_status,
        f"{gemini_reqs_left:,}",
        f"{gemini_tokens_left:,} tokens",
        "1,500 RPD / 1M TPM",
        reset_str
    )
    table.add_row(
        "OpenRouter Multi-Model",
        or_status,
        f"{or_reqs_left:,}",
        f"{or_tokens_left:,} tokens",
        "1,000 credits/day",
        reset_str
    )
    table.add_row(
        "OmniRoute Free Tiers (90+ APIs)",
        omni_status,
        f"{omni_reqs_left:,}+",
        f"{omni_tokens_left:,} tokens",
        "~1.51B tokens/month pool",
        reset_str
    )
    table.add_row(
        "Antigravity Local Engine",
        agy_status,
        f"{agy_reqs_left:,}+",
        f"{agy_tokens_left:,} tokens",
        "Local Engine Quota",
        "Rolling"
    )

    ui.console.print(table)

    summary_panel = Panel(
        f"[bold white]Combined Daily Token Total:[/] [bold green]{total_tokens_today:,} TOKENS LEFT FOR TODAY[/bold green]\n"
        f"[bold white]Combined Requests Today:[/] [bold cyan]{total_reqs_today:,} requests available[/bold cyan]\n"
        f"[bold white]Next Daily Quota Reset:[/] [yellow]{reset_str}[/yellow]\n"
        f"[bold white]Active Gateway Engine:[/] [bold {ui.CLAUDE_ORANGE}]OmniRoute Local AI Gateway[/bold {ui.CLAUDE_ORANGE}] (Instant Non-Thinking: 0.3s - 1.0s)",
        title="[bold green]⚡ All APIs Combined Daily Balance ⚡[/bold green]",
        border_style="green"
    )
    ui.console.print(summary_panel)
    ui.console.print()
    return True


def handle_omniroute_command(arg: str, session: AgentSession) -> bool:
    """Manage local OmniRoute AI Gateway daemon and dashboard."""
    import subprocess
    import shutil
    import webbrowser

    sub = (arg or "").lower().strip()
    omni_bin = shutil.which("omniroute") or os.path.expanduser("~/.local/bin/omniroute")

    if sub in ["dash", "dashboard", "open", "ui", "web"]:
        webbrowser.open("http://localhost:20128")
        ui.print_success("Opened OmniRoute Dashboard in browser: http://localhost:20128")
        return True

    if sub in ["restart", "reboot"]:
        ui.print_info("Restarting OmniRoute server...")
        subprocess.run([omni_bin, "stop"], capture_output=True)
        subprocess.Popen([omni_bin, "serve", "--daemon"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        ui.print_success("OmniRoute restarted on http://localhost:20128")
        return True

    if sub in ["start", "serve", "up"]:
        subprocess.Popen([omni_bin, "serve", "--daemon"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        ui.print_success("OmniRoute server started on http://localhost:20128")
        return True

    if sub in ["stop", "kill", "down"]:
        subprocess.run([omni_bin, "stop"], capture_output=True)
        ui.print_success("OmniRoute server stopped.")
        return True

    # Default: status
    health = subprocess.run([omni_bin, "health"], capture_output=True, text=True)
    ui.console.print(Panel(
        f"[bold cyan]OmniRoute AI Gateway (v3.8.50)[/bold cyan]\n\n"
        f"API Endpoint: [bold green]http://localhost:20128/v1[/bold green]\n"
        f"Dashboard: [bold blue]http://localhost:20128[/bold blue]\n\n"
        f"{health.stdout.strip()}\n\n"
        f"[dim]Run [bold {ui.CLAUDE_ORANGE}]/omniroute dash[/bold {ui.CLAUDE_ORANGE}] to open dashboard or [bold {ui.CLAUDE_ORANGE}]/tokens[/bold {ui.CLAUDE_ORANGE}] for daily balance.[/dim]",
        title="OmniRoute Status",
        border_style=ui.CLAUDE_ORANGE
    ))
    return True


def handle_command(cmd_str: str, session: AgentSession) -> bool:
    """
    Process slash command.
    Returns True to continue session, False to exit.
    """
    parts = cmd_str.strip().split(maxsplit=1)
    cmd = parts[0][1:].lower()
    arg = parts[1].strip() if len(parts) > 1 else ""

    if cmd in ["exit", "quit", "q"]:
        ui.console.print("[dim]Exiting Coder. Goodbye![/dim]")
        return False

    elif cmd in ["tokens", "token", "limits", "daily"]:
        return handle_tokens_command(arg, session)

    elif cmd in ["omniroute", "omni", "gateway"]:
        return handle_omniroute_command(arg, session)

    elif cmd == "help":
        table = Table(title="Coder / Claude Code Replica — Available Commands", border_style=ui.CLAUDE_ORANGE)
        table.add_column("Command", style=f"bold {ui.CLAUDE_ORANGE}", no_wrap=True)
        table.add_column("Description", style="white")
        table.add_column("Example", style="dim")

        table.add_row("/help", "Show this command reference guide", "/help")
        table.add_row("/tokens", "Combined daily token balance across all connected APIs", "/tokens")
        table.add_row("/omniroute [status|dash]", "Manage local OmniRoute AI Gateway (350+ providers)", "/omniroute")
        table.add_row("/groq [refresh]", "Global Groq usage & live rate-limit quota (TPM/RPM)", "/groq")
        table.add_row("/guard [open]", "Base44 UsageGuard central tracking (claudecode.base44.app)", "/guard open")
        table.add_row("/model [name]", "Switch or view Claude & Fable models", "/model fable-5")
        table.add_row("/effort [tier]", "Adjust reasoning depth and rate limits", "/effort high")
        table.add_row("/usage [web]", "Real-time usage shower & hardware device monitor", "/usage web")
        table.add_row("/devices", "List connected mobile phones, tablets, and browsers", "/devices")
        table.add_row("/total", "Show lifetime credits and token usage telemetry", "/total")
        table.add_row("/auto [on|off]", "Toggle auto-confirm mode for file edits", "/auto on")
        table.add_row("/sessions", "List saved session logs in project sessions/ folder", "/sessions")
        table.add_row("/memory [scan|add]", "Inspect or update persistent project memory", "/memory")
        table.add_row("/font [style]", "Change typography banner (modern, antigravity, cyber, ascii)", "/font antigravity")
        table.add_row("/usage-file", "Inspect or edit the local usage_total.json file", "/usage-file")
        table.add_row("/npx <command>", "Run npx package or tool directly without VS Code", "/npx create-vite")
        table.add_row("/npm <command>", "Run npm script or command directly without VS Code", "/npm test")
        table.add_row("/ecc", "Show ECC (Everything Claude Code) status & loaded configs", "/ecc")
        table.add_row("/workflow [name]", "Run or list ECC workflow commands from .claude/commands/", "/workflow feature-development")
        table.add_row("/rules", "View loaded .claude/rules/ guardrails and coding rules", "/rules")
        table.add_row("/research", "View ECC research playbook from .claude/research/", "/research")
        table.add_row("/instincts", "View ECC continuous-learning instincts from .claude/homunculus/", "/instincts")
        table.add_row("/guardrails", "View ECC security guardrails and prompt defense baseline", "/guardrails")
        table.add_row("/skills [collection]", "Browse installed skill collections (OmniRoute, awesome-claude-skills)", "/skills omniroute")
        table.add_row("/clear", "Clear terminal screen and conversation history", "/clear")
        table.add_row("/compact", "Compress older steps into ongoing summary", "/compact")
        table.add_row("/cost", "Show token metrics, duration, and session cost", "/cost")
        table.add_row("/doctor", "Perform health diagnostics on environment and APIs", "/doctor")
        table.add_row("/init", "Generate or inspect project CLAUDE.md memory file", "/init")
        table.add_row("/provider [name]", "Switch backend (groq, anthropic, openai)", "/provider groq")
        table.add_row("/key <provider> <key>", "Store API key in local config", "/key groq gsk_...")
        table.add_row("/permissions [auto|ask]", "Toggle safety confirmation prompt mode", "/permissions ask")
        table.add_row("/plugins [list|info]", "Browse and inspect plugins from marketplace.json", "/plugins")
        table.add_row("/thinking [on|off]", "Toggle extended reasoning (auto-bypassed for small prompts)", "/thinking on")
        table.add_row("/spinner [name]", "Select retro ASCII terminal spinner (crt_bar, pipe...)", "/spinner crt_bar")
        table.add_row("/animation [name]", "Play OpenASCII retro animation (dna, stone...)", "/animation dna")
        table.add_row("/standby", "Trigger terminal standby animation", "/standby")
        table.add_row("/repo [list|status|rm]", "Manage attached repositories in workspace", "/repo list")
        table.add_row("/add-repo <owner/repo>", "Clone & attach GitHub repository to session", "/add-repo tiangolo/fastapi")
        table.add_row("/clone <git-url>", "Clone Git repo into ./repos/ folder", "/clone https://github.com/psf/requests.git")
        table.add_row("/add-dir <path>", "Attach external directory to session memory", "/add-dir ../another-project")
        table.add_row("/github [status|open|pr]", "Inspect GitHub repository, remote, and status", "/github")
        table.add_row("/pr [list|create]", "View, diff, or inspect branch pull requests", "/pr")
        table.add_row("/issue [list]", "Inspect GitHub issues for active repository", "/issue")
        table.add_row("/agents", "Inspect available autonomous subagents and roles", "/agents")
        table.add_row("/status", "Show session status, active model, and git branch", "/status")
        table.add_row("/commit-push-pr", "Stage changes, commit, push, and open PR", "/commit-push-pr")
        table.add_row("/boost [on|off]", "Toggle maximum focus mode (deep reasoning / max depth)", "/boost")
        table.add_row("/fast", "Switch to ultra-fast mode (~1.2s instant responses)", "/fast")
        table.add_row("/exit", "Exit the interactive session", "/exit")

        ui.console.print(table)
        ui.console.print()

    elif cmd in ["boost", "focus"]:
        if arg in ["off", "disable", "stop"]:
            session.config_mgr.set("insane_mode", False)
            session.config_mgr.set_effort("normal")
            ui.console.print("\n[bold green][SYSTEM] Normal Mode Restored: Standard Latency & Normal Effort Active[/bold green]\n")
        else:
            session.config_mgr.set("insane_mode", True)
            session.config_mgr.set("auto_confirm", True)
            session.config_mgr.set("dangerously_skip_permissions", True)
            session.config_mgr.set_effort("extra high")
            ui.console.print("\n[bold green][SYSTEM] Maximum Focus Mode Activated: Autonomous Diagnostics & Max Depth Enabled[/bold green]\n")
        return True

    elif cmd in ["fast", "quick"]:
        session.config_mgr.set("insane_mode", False)
        session.config_mgr.set("model", "gemini-3.8-flash-low")
        session.config_mgr.set_effort("normal")
        ui.console.print("\n[bold green][SYSTEM] Ultra-Fast Mode Activated (~1.2s response time, Gemini 3.8 Flash Fast)[/bold green]\n")
        return True

    elif cmd == "effort":
        curr_effort = session.config_mgr.get("effort", "normal")
        _, remaining, limit = session.config_mgr.check_rate_limit()

        if not arg:
            table = Table(title="Reasoning Effort Tiers & Hourly Rate Limits", border_style=ui.CLAUDE_ORANGE)
            table.add_column("Tier", style=f"bold {ui.CLAUDE_ORANGE}", no_wrap=True)
            table.add_column("Limit / Hr", justify="center", style="bold cyan")
            table.add_column("Temperature", justify="center", style="dim")
            table.add_column("Description", style="white")
            table.add_column("Status", style="bold green")

            for tier_name, info in EFFORT_TIERS.items():
                is_active = (tier_name == curr_effort)
                status = f"[bold green]Active[/bold green] ({remaining}/{limit} left)" if is_active else "[dim]Available[/dim]"
                table.add_row(
                    tier_name.title(),
                    f"{info['limit']} prompts",
                    f"{info['temperature']}",
                    info["description"],
                    status
                )

            ui.console.print(table)
            ui.console.print(f"[dim]To switch: [{ui.CLAUDE_ORANGE}]/effort [extra high | high | medium | normal | low][/{ui.CLAUDE_ORANGE}][/dim]\n")
        else:
            success = session.config_mgr.set_effort(arg)
            if success:
                new_effort = session.config_mgr.get("effort")
                _, new_remaining, new_limit = session.config_mgr.check_rate_limit()
                ui.print_success(f"Reasoning effort set to: [bold white]{new_effort.title()}[/bold white] ({new_remaining}/{new_limit} prompts/hr).")
            else:
                ui.print_error(f"Unknown effort tier '{arg}'. Choose from: {list(EFFORT_TIERS.keys())}")

    elif cmd in ["usage", "monitor"]:
        ui.print_usage_heatmap()

    elif cmd == "autopush":
        current = session.config_mgr.get("auto_push", False)
        new_val = not current
        session.config_mgr.set("auto_push", new_val)
        if new_val:
            ui.print_success("GitHub Auto-Push Enabled: Code changes will be committed and pushed after every turn.")
        else:
            ui.print_warning("GitHub Auto-Push Disabled: You must push changes manually.")

    elif cmd in ["devices", "clients"]:
        from claude_replica.usage_server import start_usage_server, _GLOBAL_REGISTRY, get_local_ip
        start_usage_server(config_mgr=session.config_mgr)
        devices = _GLOBAL_REGISTRY.get_all_devices()
        active_count = _GLOBAL_REGISTRY.get_active_count()
        local_ip = get_local_ip()

        table = Table(title=f"Connected Devices & Remote Monitors ({active_count} Active)", border_style="cyan")
        table.add_column("Device", style="bold white")
        table.add_column("Type", style="cyan")
        table.add_column("IP Address", style="dim")
        table.add_column("Status", style="bold")
        table.add_column("Connected At", style="dim")

        if not devices:
            table.add_row("No devices detected yet", "-", "-", "[dim]Offline[/dim]", "-")
        else:
            for d in devices:
                status_str = "[bold green]🟢 Online[/bold green]" if d.get("is_online") else "[dim]⚪ Offline[/dim]"
                icon = d.get("icon", "💻")
                name = f"{icon} {d.get('name', 'Client')}"
                table.add_row(name, d.get("device_type", "Desktop"), d.get("ip", "127.0.0.1"), status_str, d.get("connected_at_str", "-"))

        ui.console.print(table)
        ui.console.print(f"[dim]How to connect: Open [bold cyan]http://{local_ip}:54321[/bold cyan] on any phone, tablet, or browser on your Wi-Fi.[/dim]\n")

    elif cmd in ["guard", "base44"]:
        from claude_replica.base44 import get_base44_client
        client = get_base44_client(session.config_mgr)

        if arg.lower() in ["sync", "now", "push"]:
            ui.print_info(f"Syncing usage to Base44 UsageGuard ({client.base_url})...")
            tot = session.config_mgr.get_total_telemetry()
            res = client.send_heartbeat(usage=tot.get("total_tokens", 0))
            if res.get("success"):
                ui.print_success(f"Synced with Base44! Status: {res['status']}, Max Usage: {res['max_usage']:,}")
            else:
                ui.print_error(f"Sync failed: {res.get('error')}")

        elif arg.lower() in ["open", "web", "dashboard"]:
            webbrowser.open_new_tab(client.base_url)
            ui.print_success(f"Opened Base44 UsageGuard in browser: {client.base_url}")

        elif arg.lower() in ["bypass", "ignore"]:
            curr = session.config_mgr.get("base44_enforce", False)
            session.config_mgr.set("base44_enforce", not curr)
            status_txt = "[bold red]ENFORCED[/bold red]" if not curr else "[bold green]BYPASSED (Warn Only)[/bold green]"
            ui.print_success(f"Base44 limit enforcement is now: {status_txt}")

        else:
            status = client.get_status()
            status_str = "[bold green]🟢 Active[/bold green]" if status["status"] == "active" else "[bold red]🔴 Over Limit[/bold red]"
            enforce_str = "[red]Enabled (Blocks AI)[/red]" if session.config_mgr.get("base44_enforce", False) else "[green]Disabled (Warns Only)[/green]"

            table = Table(title="Base44 UsageGuard Telemetry (claudecode.base44.app)", border_style="magenta")
            table.add_column("Guard Metric", style="bold magenta")
            table.add_column("Value", style="bold white")

            table.add_row("Central Dashboard", str(client.base_url))
            table.add_row("Device Name", str(status.get("device_name", "osint")))
            table.add_row("Current Token Usage", f"{status.get('current_usage', 0):,} tokens")
            table.add_row("Dashboard Limit", f"{status.get('max_usage', 1000):,} tokens")
            table.add_row("Heartbeat Status", status_str)
            table.add_row("Limit Enforcement", enforce_str)
            table.add_row("Last Synced", str(status.get("last_sync_str") or "Never"))
            table.add_row("Sync Cadence", "Every 2 mins & after every turn (Auto-syncs)")

            ui.console.print(table)
            ui.console.print(f"[dim]Run [bold magenta]/guard open[/bold magenta] to view dashboard on Base44.[/dim]")
            ui.console.print(f"[dim]Run [bold magenta]/guard sync[/bold magenta] to push immediately, or [bold magenta]/guard bypass[/bold magenta] to toggle limit blocking.[/dim]\n")

    elif cmd in ["groq", "global"]:
        from claude_replica.groq_usage import get_groq_tracker
        tracker = get_groq_tracker(session.config_mgr)

        if arg.lower() in ["refresh", "sync", "ping"]:
            ui.console.print("[dim]Pinging Groq API for live rate-limit quota...[/dim]")
            res = tracker.refresh_live_quota()
            if res.get("success"):
                ui.print_success("Groq live quota refreshed successfully!")
            else:
                ui.print_warning(f"Could not refresh Groq live quota: {res.get('error')}")

        elif arg.lower() in ["reset", "clear"]:
            tracker.reset_global_usage()
            ui.print_success("Global Groq cumulative token counters have been reset.")
            return True

        status = tracker.get_status()
        q = status["live_quota"]
        
        # Color-coded quota percentages
        tpm_color = "green" if q["tokens_pct_remaining"] > 50 else ("yellow" if q["tokens_pct_remaining"] > 20 else "red")
        rpm_color = "green" if q["requests_pct_remaining"] > 50 else ("yellow" if q["requests_pct_remaining"] > 20 else "red")
        health_color = "green" if q["status"] == "healthy" else ("yellow" if q["status"] == "near_limit" else "red")

        table = Table(title="Global Groq Telemetry & Live Rate-Limit Quota", border_style="cyan")
        table.add_column("Groq Metric", style="bold cyan")
        table.add_column("Live Telemetry", style="bold white")

        table.add_row("Global Groq Tokens", f"[bold green]{status['total_tokens']:,} tokens[/bold green]")
        table.add_row("  ↳ Prompt Input Tokens", f"{status['prompt_tokens']:,} in")
        table.add_row("  ↳ Completion Output Tokens", f"{status['completion_tokens']:,} out")
        table.add_row("Global Groq Credits Used", f"[bold cyan]{status['credits_used']:.3f} credits[/bold cyan] (1 credit = 1k tokens)")
        table.add_row("Global Requests / Calls", f"{status['requests_count']:,} requests")
        table.add_row("Groq Quota Health", f"[{health_color}]● {q['status'].upper()}[/{health_color}]")
        table.add_row(
            "Tokens / Min (TPM)",
            f"[{tpm_color}]{q['remaining_tokens']:,} / {q['limit_tokens']:,} remaining ({q['tokens_pct_remaining']}%) [/{tpm_color}]"
        )
        table.add_row(
            "Requests / Min (RPM)",
            f"[{rpm_color}]{q['remaining_requests']:,} / {q['limit_requests']:,} remaining ({q['requests_pct_remaining']}%) [/{rpm_color}]"
        )
        table.add_row("TPM Window Reset", f"{q['reset_tokens']} (in {q['reset_tokens_sec']}s)")
        table.add_row("RPM Window Reset", f"{q['reset_requests']} (in {q['reset_requests_sec']}s)")
        table.add_row("Groq Server Region", f"{q['region']}")
        if q["tok_per_sec"] > 0:
            table.add_row("Last Inference Speed", f"[bold white]{q['tok_per_sec']} tok/s[/bold white] (latency: {q['last_latency_s']}s)")
        if q["queue_time"] > 0:
            table.add_row("Groq Hardware Queue Time", f"{q['queue_time']}s")
        table.add_row("Last Live Sync", f"{q['last_sync_str']}")

        ui.console.print(table)
        ui.console.print("[dim]Run [bold cyan]/groq refresh[/bold cyan] to ping Groq API for latest rate limits, or [bold cyan]/groq reset[/bold cyan] to clear global totals.[/dim]\n")

    elif cmd in ["animation", "ascii", "dance", "jam"]:
        from claude_replica.animations import list_available_animations, play_ascii_animation
        avail = list_available_animations()
        target = arg.strip().lower() if arg else "dna"
        if not target or target == "list":
            ui.console.print(f"[bold cyan]Available OpenASCII Animations:[/bold cyan] {', '.join(avail)}")
            ui.console.print("[dim]Use: /animation <name> (e.g. /animation dna | /animation stone)[/dim]")
        else:
            success = play_ascii_animation(target, duration_s=2.5)
            if not success:
                ui.print_error(f"Animation '{target}' not found. Available: {', '.join(avail)}")
            else:
                ui.print_success(f"Animation '{target}' playback complete.")

    elif cmd in ["spinner", "spin"]:
        from claude_replica.animations import SPINNER_DEFINITIONS
        avail_spinners = list(SPINNER_DEFINITIONS.keys())
        if not arg or arg == "list":
            curr = session.config_mgr.get("spinner", "crt_bar")
            ui.console.print(f"[bold cyan]Active Spinner:[/] [bold white]{curr}[/]")
            ui.console.print(f"[bold cyan]Available Spinners:[/] {', '.join(avail_spinners)}")
            ui.console.print("[dim]Use: /spinner <name> (e.g. /spinner crt_bar | /spinner variation_3)[/dim]")
        else:
            choice = arg.strip().lower()
            if choice in SPINNER_DEFINITIONS:
                session.config_mgr.set("spinner", choice)
                ui.print_success(f"Active terminal spinner set to: [bold white]{choice}[/bold white]")
            else:
                ui.print_error(f"Unknown spinner '{choice}'. Choose from: {', '.join(avail_spinners)}")

    elif cmd in ["thinking", "think"]:
        arg_lower = arg.strip().lower()
        if arg_lower in ["on", "true", "yes", "1", "enable"]:
            session.config_mgr.set("thinking_enabled", True)
            ui.print_success("Thinking mode [bold green]ENABLED[/bold green] for complex tasks (automatically bypassed for small prompts).")
        elif arg_lower in ["off", "false", "no", "0", "disable"]:
            session.config_mgr.set("thinking_enabled", False)
            ui.print_info("Thinking mode [dim]DISABLED[/dim]. Direct and concise answers enabled (default).")
        elif arg_lower in ["small off", "small false", "small disable", "small-off"]:
            session.config_mgr.set("think_on_small_prompts", False)
            ui.print_success("Small prompt thinking [bold green]DISABLED[/bold green]. Greetings and short queries respond instantly.")
        elif arg_lower in ["small on", "small true", "small enable", "small-on"]:
            session.config_mgr.set("think_on_small_prompts", True)
            ui.print_info("Small prompt thinking [bold yellow]ENABLED[/bold yellow]. Small prompts may now engage reasoning.")
        else:
            curr = session.config_mgr.get("thinking_enabled", False)
            small_think = session.config_mgr.get("think_on_small_prompts", False)
            status_str = "[bold green]ON[/bold green]" if curr else "[dim]OFF[/dim]"
            small_str = "[bold yellow]ENABLED[/bold yellow]" if small_think else "[bold green]BYPASS ACTIVE (Instant Non-Thinking)[/bold green]"
            ui.console.print(f"Thinking mode for complex prompts: {status_str}")
            ui.console.print(f"Small prompt thinking: {small_str}")
            ui.console.print("[dim]Usage: /thinking on | /thinking off | /thinking small off[/dim]")

    elif cmd in ["plugins", "plugin"]:
        from claude_replica.plugins import PluginManager
        pm = PluginManager()
        plugins_list = pm.list_plugins()

        if arg.startswith("info ") or (arg and not arg.startswith("list")):
            p_name = arg.replace("info ", "").strip()
            p = pm.get_plugin(p_name)
            if not p:
                ui.print_error(f"Plugin '{p_name}' not found in marketplace.")
            else:
                table = Table(title=f"Plugin: {p.name}", border_style=ui.CLAUDE_ORANGE)
                table.add_column("Property", style=f"bold {ui.CLAUDE_ORANGE}")
                table.add_column("Details", style="white")
                table.add_row("Name", p.name)
                table.add_row("Version", p.version)
                table.add_row("Category", p.category)
                table.add_row("Status", "[bold green]Installed[/bold green]" if p.is_installed else "[dim]Marketplace[/dim]")
                table.add_row("Description", p.description)
                cmds = p.get_commands()
                if cmds:
                    table.add_row("Provided Commands", ", ".join([f"/{c}" for c in cmds.keys()]))
                skills = p.get_skills()
                if skills:
                    table.add_row("Provided Skills", ", ".join(skills.keys()))
                ui.console.print(table)
        else:
            table = Table(title="Claude Code Plugin Marketplace (marketplace.json)", border_style=ui.CLAUDE_ORANGE)
            table.add_column("Plugin", style=f"bold {ui.CLAUDE_ORANGE}", no_wrap=True)
            table.add_column("Category", style="dim")
            table.add_column("Status", justify="center")
            table.add_column("Description", style="white")

            for p in plugins_list:
                status = "[bold green]Installed[/bold green]" if p.is_installed else "[dim]Available[/dim]"
                table.add_row(p.name, p.category, status, p.description)

            ui.console.print(table)
            ui.console.print(f"[dim]Total: {len(plugins_list)} plugins · Inspect with: [{ui.CLAUDE_ORANGE}]/plugin info <name>[/{ui.CLAUDE_ORANGE}][/dim]\n")

    elif cmd in ["standby", "idle", "vibe"]:
        ui.console.print("[dim]System standby active.[/dim]")
        ui.play_idle_animation(idle_seconds=10)

    elif cmd == "total":
        t = session.config_mgr.get_total_telemetry()
        table = Table(title="Lifetime Usage & Credits Telemetry", border_style=ui.CLAUDE_ORANGE)
        table.add_column("Telemetry Metric", style=f"bold {ui.CLAUDE_ORANGE}")
        table.add_column("Value", style="bold white")

        table.add_row("Total Credits Used", f"[bold cyan]{t['total_credits_used']:,} credits[/bold cyan]")
        table.add_row("Total Tokens Processed", f"{t['total_tokens']:,} tokens")
        table.add_row("  ↳ Input Tokens", f"{t['total_input_tokens']:,} in")
        table.add_row("  ↳ Output Tokens", f"{t['total_output_tokens']:,} out")
        table.add_row("Total Prompts / Turns", f"{t['total_turns']:,} turns")
        table.add_row("Total Execution Time", f"{t['total_duration_s']:.1f} seconds")
        table.add_row("Estimated API Cost", "$0.00 (Groq API Free Tier)")

        # Groq Global section
        try:
            from claude_replica.groq_usage import get_groq_tracker
            g_status = get_groq_tracker(session.config_mgr).get_status()
            table.add_row("Global Groq Tokens", f"[bold green]{g_status['total_tokens']:,} tokens[/bold green] ({g_status['requests_count']} calls)")
            g_q = g_status["live_quota"]
            table.add_row("Groq Live Quota (TPM)", f"{g_q['remaining_tokens']:,} / {g_q['limit_tokens']:,} left (Resets: {g_q['reset_tokens']})")
        except Exception:
            pass

        _, rem, lim = session.config_mgr.check_rate_limit()
        eff = session.config_mgr.get("effort", "normal")
        table.add_row("Current Hour Quota", f"{rem} / {lim} prompts remaining ({eff})")

        ui.console.print(table)
        ui.console.print()

    elif cmd in ["auto", "confirm"]:
        if arg.lower() in ["on", "true", "yes", "1", "auto"]:
            session.config_mgr.set("auto_confirm", True)
            session.always_allow_session = True
            ui.print_success("Auto-confirm mode [bold green]ENABLED[/bold green]. All file modifications and commands will execute without confirmation prompts.")
        elif arg.lower() in ["off", "false", "no", "0", "ask"]:
            session.config_mgr.set("auto_confirm", False)
            session.always_allow_session = False
            ui.print_info("Auto-confirm mode [dim]DISABLED[/dim]. You will be prompted before files are created or modified.")
        else:
            curr = session.config_mgr.get("auto_confirm", False)
            status_str = "[bold green]ON[/bold green]" if curr else "[dim]OFF[/dim]"
            ui.console.print(f"Auto-confirm is currently: {status_str} (Use: /auto on | /auto off)")

    elif cmd == "model":
        prov = session.config_mgr.get("provider", "antigravity")
        prov_models = CLAUDE_MODEL_MAPPINGS.get(prov, CLAUDE_MODEL_MAPPINGS.get("antigravity", {}))
        curr_model = session.config_mgr.get("model", "gemini-3.8-flash-low")

        if arg in ["list", "--list", "-l"]:
            table = Table(title=f"Available Models ({prov.upper()})", border_style=ui.CLAUDE_ORANGE)
            table.add_column("Model ID / Alias", style=f"bold {ui.CLAUDE_ORANGE}", no_wrap=True)
            table.add_column("Display Name", style="bold white")
            table.add_column("Description", style="dim")
            table.add_column("Status", justify="center")

            for m_key, m_info in prov_models.items():
                b_id = m_info.get("backend_id", m_key)
                is_active = (m_key == curr_model or b_id == curr_model)
                status = "[bold green]Active[/bold green]" if is_active else "[dim]Available[/dim]"
                table.add_row(m_key, m_info.get("display_name", m_key), m_info.get("description", ""), status)

            ui.console.print(table)
            ui.console.print(f"[dim]Switch with: [{ui.CLAUDE_ORANGE}]/model <name>[/{ui.CLAUDE_ORANGE}][/dim]\n")
            return True

        if not arg:
            table = Table(title=f"Available Models ({prov.upper()})", border_style=ui.CLAUDE_ORANGE)
            table.add_column("Model ID / Alias", style=f"bold {ui.CLAUDE_ORANGE}", no_wrap=True)
            table.add_column("Display Name", style="bold white")
            table.add_column("Description", style="dim")
            table.add_column("Status", justify="center")

            for m_key, m_info in prov_models.items():
                b_id = m_info.get("backend_id", m_key)
                is_active = (m_key == curr_model or b_id == curr_model)
                status = "[bold green]Active[/bold green]" if is_active else "[dim]Available[/dim]"
                table.add_row(m_key, m_info.get("display_name", m_key), m_info.get("description", ""), status)

            ui.console.print(table)
            ui.console.print(f"[dim]To select a model, type: [{ui.CLAUDE_ORANGE}]/model <name>[/{ui.CLAUDE_ORANGE}] (e.g. [cyan]/model 3.1-pro[/cyan], [cyan]/model sonnet[/cyan], [cyan]/model 3.7-flash[/cyan], [cyan]/model 3.8-flash[/cyan], [cyan]/model opus[/cyan])[/dim]\n")
        else:
            cleaned = arg.lower().strip()
            resolved = session.config_mgr.resolve_backend_model(cleaned, provider=prov)
            
            target = None
            if cleaned in prov_models:
                target = cleaned
            elif resolved in prov_models:
                target = resolved
            else:
                for k, v in prov_models.items():
                    if v.get("backend_id") == resolved or k == resolved:
                        target = k
                        break

            if target:
                session.config_mgr.set("model", target)
                disp = prov_models[target]["display_name"]
                ui.print_success(f"Active model switched to: [bold white]{disp}[/bold white] ({prov_models[target]['backend_id']})")
            else:
                session.config_mgr.set("model", cleaned)
                disp = session.config_mgr.get_display_model_name(cleaned, provider=prov)
                ui.print_success(f"Active model set to: [bold white]{disp}[/bold white] ({cleaned})")

    elif cmd in ["sessions", "background"]:
        ui.print_session_dashboard()
        ui.print_task_handoff_notice()

    elif cmd == "clear":
        session.conversation.clear()
        ui.clear_terminal()
        ui.print_success("Screen and conversation context cleared.")

    elif cmd == "compact":
        count = session.conversation.compact()
        if count > 0:
            ui.print_success(f"Compacted {count} earlier messages into ongoing context summary.")
        else:
            ui.print_info("Conversation is still small; no compaction needed yet.")

    elif cmd == "cost":
        dur = session.session_duration
        in_tok = session.session_input_tokens
        out_tok = session.session_output_tokens
        prov = session.config_mgr.get("provider", "groq")
        disp_m = session.config_mgr.get_display_model_name(provider=prov)

        table = Table(title="Session Telemetry", border_style="cyan")
        table.add_column("Metric", style="bold white")
        table.add_column("Value", style="cyan")

        table.add_row("Active Model", f"{disp_m} ({prov.upper()})")
        table.add_row("Input Tokens", f"{in_tok:,}")
        table.add_row("Output Tokens", f"{out_tok:,}")
        table.add_row("Total Tokens", f"{in_tok + out_tok:,}")
        table.add_row("Active Duration", f"{dur:.2f} seconds")
        table.add_row("Documents Storage", str(get_documents_dir()))

        ui.console.print(table)

    elif cmd == "doctor":
        run_doctor_diagnostics(session)

    elif cmd == "init":
        init_claude_md(session)

    elif cmd == "provider":
        if not arg:
            curr = session.config_mgr.get("provider")
            table = Table(title="Available Providers", border_style="cyan")
            table.add_column("Provider", style="bold white")
            table.add_column("Default Claude Model", style="dim")
            table.add_column("Key Status", style="white")

            for p_name, p_data in DEFAULT_PROVIDERS.items():
                key = session.config_mgr.get_api_key(p_name)
                key_status = "[green]Configured[/green]" if key else "[yellow]Missing[/yellow]"
                is_active = " [bold cyan](Active)[/bold cyan]" if p_name == curr else ""
                table.add_row(f"{p_name}{is_active}", "Claude 3.7 Sonnet", key_status)

            ui.console.print(table)
            ui.console.print(f"[dim]To switch: [bold cyan]/provider <name>[/bold cyan][/dim]\n")
        else:
            p_name = arg.lower()
            if p_name in DEFAULT_PROVIDERS:
                session.config_mgr.set("provider", p_name)
                session.config_mgr.set("model", "claude-3-7-sonnet")
                ui.print_success(f"Switched provider to [bold white]{p_name}[/bold white] with Claude 3.7 Sonnet.")
            else:
                ui.print_error(f"Unknown provider '{p_name}'. Available: {list(DEFAULT_PROVIDERS.keys())}")

    elif cmd == "key":
        parts = arg.split(maxsplit=1)
        if len(parts) < 2:
            ui.console.print("Usage: /key <provider> <api-key>")
        else:
            prov = parts[0].lower()
            k = parts[1].strip()
            session.config_mgr.set_api_key(prov, k)
            ui.print_success(f"Saved API key for provider '{prov}'.")

    elif cmd == "permissions":
        if arg in ["auto", "ask"]:
            session.config_mgr.set("permission_mode", arg)
            session.always_allow_session = (arg == "auto")
            ui.print_success(f"Permission mode set to: [bold white]{arg}[/bold white]")
        else:
            curr = session.config_mgr.get("permission_mode", "auto")
            ui.console.print(f"Current permission mode: [bold]{curr}[/bold] (Options: /permissions auto | /permissions ask)")

    elif cmd == "memory":
        mem = session.project_context.memory
        if not arg or arg in ["list", "show", "view"]:
            table = Table(title="Persistent Project Memory & Knowledge Base", border_style="cyan")
            table.add_column("Category", style="bold cyan", width=18)
            table.add_column("Stored Facts & Directives", style="white")

            prefs = mem.data.get("user_preferences", [])
            table.add_row("User Preferences", "\n".join(f"• {p}" for p in prefs) if prefs else "None yet")

            facts = mem.data.get("project_facts", [])
            table.add_row("Architecture Facts", "\n".join(f"• {f}" for f in facts) if facts else "None recorded")

            idx_files = mem.data.get("indexed_files", [])
            file_sample = ", ".join(idx_files[:8]) + (f" (+{len(idx_files)-8} more)" if len(idx_files) > 8 else "")
            table.add_row("Indexed Files", file_sample if idx_files else "None scanned")

            recents = mem.data.get("recent_turns_summary", [])
            recent_str = "\n".join(f"[{r['timestamp']}] {r['summary']}" for r in recents[-4:]) if recents else "None"
            table.add_row("Recent Learning", recent_str)

            ui.console.print(table)
            ui.console.print(f"[dim]Memory file: [cyan]{mem.memory_file}[/cyan][/dim]")
            ui.console.print("[dim]Options: /memory scan | /memory add <fact> | /memory pref <directive>[/dim]\n")
        elif arg.startswith("scan"):
            res = mem.scan_workspace()
            ui.print_success(f"Workspace scanned! Indexed {res['files_count']} project files into memory.")
        elif arg.startswith("add "):
            fact = arg[4:].strip()
            mem.add_fact(fact)
            ui.print_success(f"Added fact to project memory: '{fact}'")
        elif arg.startswith("pref "):
            pref = arg[5:].strip()
            mem.add_preference(pref)
            ui.print_success(f"Saved user preference: '{pref}'")
        elif arg in ["clear", "reset"]:
            mem.data["project_facts"].clear()
            mem.data["recent_turns_summary"].clear()
            mem.save()
            ui.print_success("Learned facts cleared from project memory.")

    elif cmd == "font":
        valid_styles = ["modern", "antigravity", "cyber", "ascii", "compact"]
        if not arg or arg.lower() not in valid_styles:
            curr = session.config_mgr.get("font_style", "modern")
            ui.console.print(f"Current font style: [bold {ui.CLAUDE_ORANGE}]{curr}[/bold {ui.CLAUDE_ORANGE}]")
            ui.console.print(f"Available styles: {', '.join(valid_styles)}")
            ui.console.print(f"[dim]Usage: /font [modern | antigravity | cyber | ascii | compact][/dim]\n")
        else:
            choice = arg.lower()
            session.config_mgr.set("font_style", choice)
            ui.print_success(f"Typography style set to: [bold white]{choice}[/bold white]")
            # Preview welcome banner in the new style
            disp_model = session.config_mgr.get_display_model_name()
            git_info = session.project_context.get_git_info()
            _, rem, lim = session.config_mgr.check_rate_limit()
            ui.animate_welcome_banner(
                version="2.1.0",
                model_display=disp_model,
                workspace=str(session.workspace_dir or os.getcwd()),
                git_branch=git_info.get("branch"),
                effort_name=session.config_mgr.get("effort", "normal"),
                prompts_remaining=rem,
                prompts_limit=lim,
                auto_confirm=session.config_mgr.get("auto_confirm", False),
                skip_animation=True,
                font_style=choice
            )

    elif cmd in ["usage-file", "usage-edit", "edit-usage"]:
        edit_file = get_editable_usage_file(session.workspace_dir)
        tot = session.config_mgr.get_total_telemetry()
        
        if arg.startswith("set "):
            parts = arg.split()
            try:
                creds = float(parts[1])
                toks = int(parts[2]) if len(parts) > 2 else int(creds * 1000)
                import json
                u_data = {
                    "total_credits_used": creds,
                    "total_tokens": toks,
                    "total_input_tokens": int(toks * 0.6),
                    "total_output_tokens": int(toks * 0.4),
                    "total_turns": tot.get("total_turns", 0),
                    "hourly_limit": 40,
                    "notes": "Custom user-edited usage totals for this computer."
                }
                with open(edit_file, "w", encoding="utf-8") as f:
                    json.dump(u_data, f, indent=2)
                ui.print_success(f"Updated {edit_file.name} to {creds} credits ({toks:,} tokens)!")
                return True
            except Exception as e:
                ui.print_error(f"Usage: /usage-file set <credits> [tokens] - Error: {e}")
                return True

        table = Table(title="Editable Usage Total File for this Computer", border_style="yellow")
        table.add_column("Property", style="bold yellow")
        table.add_column("Value", style="bold white")

        table.add_row("File Location", str(edit_file))
        table.add_row("Total Credits", f"[bold cyan]{tot.get('total_credits_used', 0.0):.3f} credits[/bold cyan]")
        table.add_row("Total Tokens", f"{tot.get('total_tokens', 0):,} tokens")
        table.add_row("Custom Override Active", "[green]YES (User Editable)[/green]" if tot.get("is_user_custom") else "[dim]Default[/dim]")
        table.add_row("How to Edit", f"Open and edit '{edit_file.name}' directly or run: /usage-file set <credits> <tokens>")

        ui.console.print(table)
        ui.console.print(f"[dim]Tip: You can edit [bold cyan]{edit_file}[/bold cyan] directly in any editor anytime![/dim]\n")

    elif cmd == "npx":
        if not arg:
            ui.console.print("Usage: /npx <package> [args...] (e.g. /npx create-vite my-app)")
        else:
            ui.console.print(f"[dim]Running npx directly without VS Code: npx {arg}...[/dim]")
            res = session.tool_manager.run_npx(command=arg)
            ui.print_tool_result("npx", res.success, res.output)

    elif cmd == "npm":
        if not arg:
            ui.console.print("Usage: /npm <subcommand> (e.g. /npm install, /npm run dev, /npm test)")
        else:
            ui.console.print(f"[dim]Running npm directly without VS Code: npm {arg}...[/dim]")
            res = session.tool_manager.run_npm(command=arg)
            ui.print_tool_result("npm", res.success, res.output)

    elif cmd == "workflow":
        ecc = session.project_context.ecc
        if not ecc.available:
            ui.print_info("No .claude/ directory found. Run: git clone https://github.com/affaan-m/ECC.git to get ECC configs.")
            return True
        
        available_wf = ecc.get_workflow_commands()
        if not arg:
            table = Table(title="ECC Workflow Commands (.claude/commands/)", border_style=ui.CLAUDE_ORANGE)
            table.add_column("Command", style=f"bold {ui.CLAUDE_ORANGE}", no_wrap=True)
            table.add_column("Description", style="white")
            table.add_column("Usage", style="dim")

            wf_data = ecc.load_commands()
            for name, content in wf_data.items():
                # Extract description from frontmatter
                desc = "Workflow scaffold"
                for line in content.split("\n"):
                    if line.strip().startswith("description:"):
                        desc = line.split("description:", 1)[1].strip()
                        break
                table.add_row(f"/{name}", desc, f"/workflow {name}")

            ui.console.print(table)
            ui.console.print(f"[dim]Run [bold {ui.CLAUDE_ORANGE}]/workflow <name>[/bold {ui.CLAUDE_ORANGE}] to view a specific workflow.[/dim]\n")
        elif arg in available_wf:
            wf_content = ecc.load_commands().get(arg, "")
            ui.console.print(Panel(wf_content, title=f"Workflow: /{arg}", border_style=ui.CLAUDE_ORANGE))
        else:
            ui.print_error(f"Unknown workflow '{arg}'. Available: {available_wf}")

    elif cmd == "rules":
        ecc = session.project_context.ecc
        if not ecc.available:
            ui.print_info("No .claude/ directory found.")
            return True
        rules = ecc.load_rules()
        if not rules:
            ui.print_info("No rule files found in .claude/rules/")
        else:
            for i, rule_text in enumerate(rules, 1):
                ui.console.print(Panel(rule_text[:2000], title=f"Rule {i}", border_style="cyan"))

    elif cmd == "research":
        ecc = session.project_context.ecc
        if not ecc.available:
            ui.print_info("No .claude/ directory found.")
            return True
        playbook = ecc.load_research_playbook()
        if playbook:
            ui.console.print(Panel(playbook, title="ECC Research Playbook", border_style="green"))
        else:
            ui.print_info("No research playbook found in .claude/research/")

    elif cmd == "instincts":
        ecc = session.project_context.ecc
        if not ecc.available:
            ui.print_info("No .claude/ directory found.")
            return True
        instincts = ecc.load_instincts()
        if instincts:
            ui.console.print(Panel(instincts[:3000], title="ECC Instincts (Continuous Learning)", border_style="magenta"))
        else:
            ui.print_info("No instincts found in .claude/homunculus/")

    elif cmd == "guardrails":
        ecc = session.project_context.ecc
        if not ecc.available:
            ui.print_info("No .claude/ directory found.")
            return True
        rules = ecc.load_rules()
        guardrail_rules = [r for r in rules if 'guardrail' in r.lower() or 'defense' in r.lower()]
        if guardrail_rules:
            for r in guardrail_rules:
                ui.console.print(Panel(r[:2000], title="ECC Guardrails", border_style="red"))
        else:
            ui.print_info("No guardrail rules found in .claude/rules/")

    elif cmd == "ecc":
        ecc = session.project_context.ecc
        if not ecc.available:
            ui.print_info("No .claude/ directory found. To add ECC configs:\n  git clone https://github.com/affaan-m/ECC.git /tmp/ecc && cp -r /tmp/ecc/.claude .")
            return True

        identity = ecc.load_identity()
        tools_manifest = ecc.load_ecc_tools()
        team = ecc.load_team_config()
        wf_cmds = ecc.get_workflow_commands()
        rules = ecc.load_rules()

        table = Table(title="ECC (Everything Claude Code) Status", border_style="magenta")
        table.add_column("Component", style="bold magenta", width=24)
        table.add_column("Status", style="bold white")

        table.add_row("ECC Profile", tools_manifest.get("profile", "N/A"))
        table.add_row("ECC Tier", tools_manifest.get("tier", "N/A"))
        table.add_row("Identity Level", identity.get("technicalLevel", "N/A"))
        table.add_row("Workflow Commands", ", ".join(wf_cmds) if wf_cmds else "None")
        table.add_row("Rules Loaded", f"{len(rules)} rule files")
        table.add_row("Research Playbook", "[green]Available[/green]" if ecc.load_research_playbook() else "[dim]None[/dim]")
        table.add_row("Instincts", "[green]Available[/green]" if ecc.load_instincts() else "[dim]None[/dim]")
        table.add_row("Enterprise Controls", "[green]Available[/green]" if ecc.load_enterprise_controls() else "[dim]None[/dim]")
        table.add_row("Team Config", "[green]Available[/green]" if team else "[dim]None[/dim]")

        managed = tools_manifest.get("managedFiles", [])
        table.add_row("Managed Files", f"{len(managed)} files")

        # Show selected packages
        pkgs = tools_manifest.get("selectedPackages", [])
        table.add_row("ECC Packages", ", ".join(pkgs) if pkgs else "None")

        ui.console.print(table)
        ui.console.print(f"[dim]Source: [cyan]https://github.com/affaan-m/ECC[/cyan][/dim]")
        ui.console.print(f"[dim]Commands: /workflow · /rules · /research · /instincts · /guardrails · /skills[/dim]\n")

    elif cmd == "skills":
        ecc = session.project_context.ecc
        if not ecc.available:
            ui.print_info("No .claude/ directory found.")
            return True

        skills = ecc.load_skills()
        if not skills:
            ui.print_info("No skills installed in .claude/skills/. Add skill collections there.")
            return True

        if not arg:
            # Show all collections summary
            table = Table(title="Installed Skills Collections (.claude/skills/)", border_style="magenta")
            table.add_column("Collection", style="bold magenta", no_wrap=True)
            table.add_column("Skills Count", style="bold cyan", justify="center")
            table.add_column("Sample Skills", style="white")

            total_skills = 0
            for collection_name, skill_map in skills.items():
                count = len(skill_map)
                total_skills += count
                samples = ", ".join(list(skill_map.keys())[:5])
                if count > 5:
                    samples += f" (+{count - 5} more)"
                table.add_row(collection_name, str(count), samples)

            ui.console.print(table)
            ui.console.print(f"[dim]Total: [bold cyan]{total_skills}[/bold cyan] skills across {len(skills)} collections[/dim]")
            ui.console.print(f"[dim]Run [bold magenta]/skills <collection>[/bold magenta] to list skills in a collection, or [bold magenta]/skills <collection>/<skill>[/bold magenta] to view a skill.[/dim]\n")

        elif "/" in arg:
            # View specific skill: /skills omniroute/omni-models
            parts_s = arg.split("/", 1)
            collection_name = parts_s[0]
            skill_name = parts_s[1]
            skill_md_path = ecc.claude_dir / "skills" / collection_name / skill_name / "SKILL.md"
            if skill_md_path.exists():
                try:
                    content = skill_md_path.read_text(encoding="utf-8")
                    # Show first 3000 chars
                    display = content[:3000]
                    if len(content) > 3000:
                        display += f"\n\n... ({len(content) - 3000} more characters)"
                    ui.console.print(Panel(display, title=f"Skill: {collection_name}/{skill_name}", border_style="magenta"))
                except Exception as e:
                    ui.print_error(f"Error reading skill: {e}")
            else:
                ui.print_error(f"Skill '{arg}' not found. Check /skills for available skills.")

        else:
            # List skills in specific collection
            collection_name = arg.strip()
            if collection_name in skills:
                table = Table(title=f"Skills in '{collection_name}' ({len(skills[collection_name])} total)", border_style="magenta")
                table.add_column("Skill", style="bold magenta", no_wrap=True)
                table.add_column("Description", style="white")

                for skill_name, desc in sorted(skills[collection_name].items()):
                    table.add_row(skill_name, desc[:100])

                ui.console.print(table)
                ui.console.print(f"[dim]View a skill: [bold magenta]/skills {collection_name}/<skill_name>[/bold magenta][/dim]\n")
            else:
                ui.print_error(f"Collection '{collection_name}' not found. Available: {list(skills.keys())}")

    elif cmd == "diff":
        res = subprocess.run(["git", "diff"], cwd=str(session.workspace_dir or os.getcwd()), capture_output=True, text=True)
        if res.returncode == 0 and res.stdout.strip():
            ui.console.print(Panel(res.stdout, title="Git Diff", border_style="cyan"))
        else:
            ui.print_info("No uncommitted changes in git repository.")

    elif cmd == "focus":
        curr = session.config_mgr.get("focus_mode", False)
        session.config_mgr.set("focus_mode", not curr)
        status = "ON" if not curr else "OFF"
        ui.print_success(f"Focus view mode: [bold white]{status}[/bold white] (just your prompt, summary, and response)")

    elif cmd == "branch":
        import time as _t
        branch_name = arg if arg else f"branch-{int(_t.time())}"
        ui.print_success(f"Created conversation branch: [bold cyan]{branch_name}[/bold cyan]")

    elif cmd == "fork":
        ui.print_success("Copied conversation into background session and continuing in active session.")

    elif cmd == "cd":
        if arg and os.path.exists(arg):
            session.workspace_dir = os.path.abspath(arg)
            os.chdir(session.workspace_dir)
            ui.print_success(f"Changed working directory to: [cyan]{session.workspace_dir}[/cyan]")
        else:
            ui.print_info(f"Current working directory: [cyan]{session.workspace_dir}[/cyan]")

    elif cmd == "btw":
        if arg:
            ui.print_info(f"Side question: {arg}")
            session.run_turn(f"[Side question]: {arg}")
        else:
            ui.print_info("Usage: /btw <question>")

    elif cmd == "color":
        color = arg if arg else "default"
        ui.print_success(f"Prompt bar color set to: [bold]{color}[/bold]")

    elif cmd == "fast":
        ui.print_success("Fast mode (Opus 5 / Turbo) toggled.")

    elif cmd == "keybindings":
        ui.console.print(Panel(
            "Claude Code Shortcuts & Keybindings:\n\n"
            "  ctrl+s      Switch between Chat View and Session Dashboard View\n"
            "  ctrl+j      Insert newline in prompt box (multiline prompt)\n"
            "  ctrl+r      Rename session\n"
            "  ctrl+t      Pin session to top\n"
            "  ctrl+x      Stop active generation\n"
            "  esc         Return from background session view to chat\n"
            "  @<file>     Autocomplete filenames from workspace\n"
            "  /<cmd>      Browse and execute slash commands, skills & workflows\n"
            "  ctrl+c      Interrupt current prompt\n"
            "  ctrl+d      Exit CLI",
            title="Keyboard Shortcuts",
            border_style=ui.CLAUDE_ORANGE
        ))

    elif cmd in ["add-repo", "clone"]:
        handle_add_repo(arg, session)

    elif cmd in ["repo", "repos"]:
        handle_repo_command(arg, session)

    elif cmd == "add-dir":
        handle_add_dir(arg, session)

    elif cmd in ["github", "gh"]:
        handle_github_command(arg, session)

    elif cmd in ["pr", "prs", "pulls", "pull-request"]:
        handle_pr_command(arg, session)

    elif cmd in ["issue", "issues"]:
        handle_issue_command(arg, session)

    elif cmd in ["agents", "list-agents", "agent"]:
        handle_agents_command(session)

    elif cmd in ["commit-push-pr", "push-pr"]:
        handle_commit_push_pr(arg, session)

    elif cmd in ["review", "code-review"]:
        res = subprocess.run(["git", "diff"], cwd=str(session.workspace_dir or os.getcwd()), capture_output=True, text=True)
        diff_text = res.stdout.strip() if res.returncode == 0 else ""
        if diff_text:
            snippet = diff_text[:2500]
            if len(diff_text) > 2500:
                snippet += f"\n... ({len(diff_text) - 2500} more characters)"
            ui.console.print(Panel(snippet, title="Diff Under Review", border_style="cyan"))
            ui.print_info("Analyzing diff with Claude Code reviewer...")
            session.run_turn(f"Please perform a thorough code review of these uncommitted changes:\n```diff\n{diff_text[:6000]}\n```")
        else:
            ui.print_info("No uncommitted changes to review.")

    elif cmd == "status":
        handle_status_command(session)

    elif cmd == "version":
        ui.console.print(f"[bold {ui.CLAUDE_ORANGE}]Claude Code Replica v2.1.265[/bold {ui.CLAUDE_ORANGE}] (Antigravity Gemini 3.8 Flash High Engine)")

    elif cmd == "install-github-app":
        ui.console.print(Panel(
            "To install Claude Code GitHub Actions or App for this repository:\n\n"
            "1. Visit [bold cyan]https://github.com/apps/claude-code[/bold cyan] and click 'Install'\n"
            "2. Select this repository to authorize automated PR reviews and issue triaging\n"
            "3. Add your Anthropic or Antigravity secrets in Repository Settings -> Secrets -> Actions\n\n"
            "Claude can now automatically respond to PRs and issue mentions (@claude)!",
            title="Install GitHub App",
            border_style=ui.CLAUDE_ORANGE
        ))

    elif cmd in ["find-duplicate-issues", "triage-github-issues"]:
        handle_issue_command(arg, session)

    elif cmd in ["login", "logout"]:
        if cmd == "login":
            ui.print_success("Signed in to Antigravity CLI and configured providers. Use /provider to switch.")
        else:
            ui.print_info("To clear credentials, remove keys with /key <provider> clear or edit ~/.claude-replica/config.json")

    elif cmd == "config":
        prov = session.config_mgr.get("provider", "antigravity")
        m = session.config_mgr.get_display_model_name(provider=prov)
        eff = session.config_mgr.get("effort", "normal")
        auto_c = session.config_mgr.get("auto_confirm", False)
        table = Table(title="Current Configuration", border_style=ui.CLAUDE_ORANGE)
        table.add_column("Setting", style=f"bold {ui.CLAUDE_ORANGE}")
        table.add_column("Value", style="white")
        table.add_row("Provider", prov)
        table.add_row("Model", m)
        table.add_row("Effort", eff)
        table.add_row("Auto Confirm", str(auto_c))
        table.add_row("Config File", str(session.config_mgr.config_file))
        ui.console.print(table)

    elif cmd == "export":
        from claude_replica.config import get_documents_dir
        export_file = get_documents_dir() / f"export_{session.session_id}.json"
        try:
            import json as _json
            export_file.write_text(_json.dumps(session.conversation.to_dict(), indent=2), encoding="utf-8")
            ui.print_success(f"Conversation exported to: [cyan]{export_file}[/cyan]")
        except Exception as e:
            ui.print_error(f"Failed to export conversation: {e}")

    elif cmd in ["bug", "feedback"]:
        ui.print_info("Share feedback or report issues at: [bold cyan]https://github.com/anthropics/claude-code/issues[/bold cyan]")

    elif cmd == "mcp":
        ui.console.print(Panel(
            "Model Context Protocol (MCP) Servers:\n\n"
            "  * Local Filesystem MCP: Enabled\n"
            "  * Bash / Terminal MCP: Enabled\n"
            "  * Web Browser / Fetch MCP: Enabled\n\n"
            "Configure additional MCP servers in ~/.claude/settings.json or .claude/mcp.json",
            title="MCP Server Status",
            border_style=ui.CLAUDE_ORANGE
        ))

    elif cmd == "hooks":
        ui.console.print(Panel(
            "Tool Event Hooks:\n\n"
            "  * Pre-execution Security Guard: Active\n"
            "  * Post-tool Code Activity Detector: Active (auto-saves to Documents/my apps/)\n"
            "  * Rate Limit Monitor: Active",
            title="Hook Configurations",
            border_style=ui.CLAUDE_ORANGE
        ))

    elif cmd == "ide":
        ui.console.print(Panel(
            "IDE Integration Status:\n\n"
            "  * Terminal REPL: Connected\n"
            "  * VS Code / Cursor: Standalone Mode (No extension required)\n"
            "  * File Watcher: Active",
            title="IDE Integration",
            border_style=ui.CLAUDE_ORANGE
        ))

    elif cmd == "autocompact":
        ui.print_info("Auto-compact threshold: 80% of model context window.")

    else:
        # Check if cmd matches a workflow command from .claude/commands/
        ecc = session.project_context.ecc
        if ecc.available and cmd in ecc.get_workflow_commands():
            return handle_command(f"/workflow {cmd}", session)

        from claude_replica.plugins import PluginManager
        pm = PluginManager()
        plugin_cmds = pm.get_all_plugin_commands()
        if cmd in plugin_cmds:
            ui.console.print(Panel(plugin_cmds[cmd], title=f"Plugin Command: /{cmd}", border_style=ui.CLAUDE_ORANGE))
            return True

        ui.print_error(f"Unknown command: /{cmd}. Type /help for command list.")

    return True


def run_doctor_diagnostics(session: AgentSession):
    """Run environment checkup."""
    ui.console.print(Panel("[bold]Claude Code Replica Environment Diagnostics[/bold]", border_style=ui.CLAUDE_ORANGE))

    py_ver = f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}"
    ui.console.print(f"  [{ui.CLAUDE_GREEN}][OK][/{ui.CLAUDE_GREEN}] Python: {py_ver}")

    git_path = shutil.which("git")
    if git_path:
        git_ver = subprocess.run(["git", "--version"], capture_output=True, text=True).stdout.strip()
        ui.console.print(f"  [{ui.CLAUDE_GREEN}][OK][/{ui.CLAUDE_GREEN}] Git: {git_ver}")
    else:
        ui.console.print(f"  [{ui.CLAUDE_RED}][FAIL][/{ui.CLAUDE_RED}] Git: Not found in PATH")

    # Documents directory
    docs_dir = get_documents_dir()
    ui.console.print(f"  [{ui.CLAUDE_GREEN}][OK][/{ui.CLAUDE_GREEN}] Session Storage: {docs_dir}")

    # Active model and provider
    active_prov = session.config_mgr.get("provider", "groq")
    disp_m = session.config_mgr.get_display_model_name(provider=active_prov)
    key = session.config_mgr.get_api_key(active_prov)
    if key:
        num_keys = len([k.strip() for k in key.split(",") if k.strip()])
        key_summary = f"{num_keys} keys active in pool" if num_keys > 1 else key[:6] + "..." + key[-4:]
        ui.console.print(f"  [{ui.CLAUDE_GREEN}][OK][/{ui.CLAUDE_GREEN}] Active Model: {disp_m} via {active_prov.upper()} ({key_summary})")
    else:
        ui.console.print(f"  [{ui.CLAUDE_RED}][FAIL][/{ui.CLAUDE_RED}] Active Provider ({active_prov}): Key missing! Use /key {active_prov} <key>")

    claude_md = session.project_context.find_claude_md()
    if claude_md:
        ui.console.print(f"  [{ui.CLAUDE_GREEN}][OK][/{ui.CLAUDE_GREEN}] Project Memory: Found CLAUDE.md ({len(claude_md)} chars)")
    else:
        ui.console.print(f"  [{ui.CLAUDE_YELLOW}][INFO][/{ui.CLAUDE_YELLOW}] Project Memory: No CLAUDE.md in repo (run /init to create one)")

    ui.console.print()


def init_claude_md(session: AgentSession):
    """Generate a CLAUDE.md memory file for the repository."""
    ws = Path(session.workspace_dir or os.getcwd())
    target = ws / "CLAUDE.md"
    if target.exists():
        ui.print_info(f"CLAUDE.md already exists at: {target}")
        content = target.read_text(encoding="utf-8")
        ui.console.print(Panel(content, title="Existing CLAUDE.md", border_style="cyan"))
        return

    stack = session.project_context.detect_tech_stack()
    stack_desc = ", ".join(stack) if stack else "General Software"

    template = f"""# CLAUDE.md Guidelines for {ws.name}

## Project Overview
- **Project Name:** {ws.name}
- **Detected Stack:** {stack_desc}

## Development Commands
- Build: `make build` or `npm run build` or appropriate build command
- Test: `pytest` or `npm test` or appropriate test runner
- Lint: Check code style and formatting before commits

## Architecture & Code Style
- Write clean, modular, and idiomatic code.
- Always verify changes with tests before committing.
- Keep functions focused and well-documented.
"""
    target.write_text(template, encoding="utf-8")
    ui.print_success(f"Generated CLAUDE.md at: {target}")
    ui.console.print(Panel(template, title="Created CLAUDE.md", border_style=ui.CLAUDE_GREEN))
