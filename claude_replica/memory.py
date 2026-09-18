"""
Project Context, Persistent Memory System, Environment Detection & Session Saver.
Saves session transcripts directly to project folder sessions/ and tracks user environment/IP.
"""

import os
import re
import time
import json
import socket
import datetime
import platform
import subprocess
import urllib.request
from pathlib import Path
from typing import List, Dict, Any, Optional

from claude_replica.config import get_documents_dir

_CACHED_IP: Dict[str, Any] = {"public_ip": None, "timestamp": 0, "npx_version": None}


def get_network_environment(workspace: Optional[Path] = None) -> Dict[str, Any]:
    """Detect user local IP, public IP, hostname, and runtime environment."""
    local_ip = "127.0.0.1"
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("8.8.8.8", 80))
            local_ip = s.getsockname()[0]
    except Exception:
        try:
            local_ip = socket.gethostbyname(socket.gethostname())
        except Exception:
            pass

    now = time.time()
    public_ip = _CACHED_IP.get("public_ip")
    if not public_ip or (now - _CACHED_IP.get("timestamp", 0) > 600):
        try:
            req = urllib.request.Request(
                "https://api.ipify.org",
                headers={"User-Agent": "Claude-Code-Replica/2.1"}
            )
            with urllib.request.urlopen(req, timeout=1.0) as resp:
                public_ip = resp.read().decode("utf-8").strip()
                _CACHED_IP["public_ip"] = public_ip
                _CACHED_IP["timestamp"] = now
        except Exception:
            public_ip = "127.0.0.1 (Localhost)"

    npx_version = _CACHED_IP.get("npx_version")
    if npx_version is None:
        try:
            res = subprocess.run(["npx", "--version"], capture_output=True, text=True, timeout=1)
            if res.returncode == 0:
                npx_version = res.stdout.strip()
            else:
                npx_version = ""
        except Exception:
            npx_version = ""
        _CACHED_IP["npx_version"] = npx_version

    return {
        "local_ip": local_ip,
        "public_ip": public_ip or "127.0.0.1",
        "hostname": platform.node(),
        "os_name": platform.system(),
        "os_release": platform.release(),
        "architecture": platform.machine(),
        "python_version": platform.python_version(),
        "npx_version": npx_version,
        "workspace": str(workspace or Path.cwd())
    }


class PersistentMemory:
    """
    Persistent Memory Engine for Coder / Claude Code Replica.
    Saves and loads memories from project_memory.json in the project root.
    Scans project structure, remembers user preferences, lessons learned, and facts.
    """
    def __init__(self, workspace: Path):
        self.workspace = Path(workspace).resolve()
        self.memory_file = self.workspace / "project_memory.json"
        self.data = self._load()
        if not self.data.get("indexed_files"):
            self.scan_workspace()

    def _load(self) -> Dict[str, Any]:
        default_data = {
            "version": "1.0",
            "workspace": str(self.workspace),
            "user_preferences": [
                "Always ask before modifying or writing files unless auto-confirm is enabled.",
                "Work strictly inside the projects folder (/home/feds/Projects/claude-code-replica). Never use higgsfield.",
                "Support npx and npm tools directly without VS Code.",
                "Provide deep research and extended reasoning for complex prompts.",
                "Use clean Antigravity CLI-inspired visual design and typography."
            ],
            "project_facts": [],
            "learned_insights": [],
            "recent_turns_summary": [],
            "last_scanned": None,
            "indexed_files": []
        }
        if self.memory_file.exists():
            try:
                with open(self.memory_file, "r", encoding="utf-8") as f:
                    content = json.load(f)
                    if isinstance(content, dict):
                        default_data.update(content)
            except Exception:
                pass
        return default_data

    def save(self):
        try:
            with open(self.memory_file, "w", encoding="utf-8") as f:
                json.dump(self.data, f, indent=2)
        except Exception:
            pass

    def scan_workspace(self) -> Dict[str, Any]:
        """Scan project files and structure to build codebase memory."""
        files = []
        try:
            for item in self.workspace.glob("**/*"):
                if item.is_file() and not any(p in item.parts for p in [".git", "node_modules", "__pycache__", ".venv"]):
                    rel = str(item.relative_to(self.workspace))
                    if len(files) < 40:
                        files.append(rel)
        except Exception:
            pass
        self.data["last_scanned"] = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        self.data["indexed_files"] = files
        self.save()
        return {"files_count": len(files), "indexed_files": files}

    def add_preference(self, pref: str):
        if pref and pref not in self.data.get("user_preferences", []):
            self.data.setdefault("user_preferences", []).append(pref)
            self.save()

    def add_fact(self, fact: str):
        if fact and fact not in self.data.get("project_facts", []):
            self.data.setdefault("project_facts", []).append(fact)
            self.save()

    def learn_from_turn(self, prompt: str, response_text: str, tools_called: List[str]):
        """Extract key learnings and append to persistent memory."""
        p_lower = prompt.lower()
        if any(w in p_lower for w in ["don't", "dont", "never", "always", "remember", "prefer", "require"]):
            self.add_preference(prompt.strip()[:140])

        tools_str = ", ".join(tools_called) if tools_called else "Synthesized Answer"
        summary = f"Prompt: {prompt[:70]} | Action: {tools_str}"
        recents = self.data.setdefault("recent_turns_summary", [])
        recents.append({
            "timestamp": datetime.datetime.now().strftime("%H:%M:%S"),
            "summary": summary
        })
        self.data["recent_turns_summary"] = recents[-8:]
        self.save()

    def attach_repo(self, repo_name: str, repo_url: str, repo_path: str, files: List[str]) -> Dict[str, Any]:
        """Attach an external repository into memory."""
        repos = self.data.setdefault("attached_repos", [])
        existing = next((r for r in repos if r.get("name") == repo_name), None)
        repo_info = {
            "name": repo_name,
            "url": repo_url,
            "path": repo_path,
            "files_count": len(files),
            "attached_at": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        }
        if existing:
            existing.update(repo_info)
        else:
            repos.append(repo_info)

        current_files = self.data.setdefault("indexed_files", [])
        for f in files[:50]:
            if f not in current_files:
                current_files.append(f)

        self.add_fact(f"Repository attached: {repo_name} at {repo_path} ({len(files)} files)")
        self.save()
        return repo_info

    def remove_attached_repo(self, repo_name: str) -> bool:
        """Detach repository from persistent memory."""
        repos = self.data.get("attached_repos", [])
        new_repos = [r for r in repos if r.get("name") != repo_name and r.get("path") != repo_name]
        if len(new_repos) != len(repos):
            self.data["attached_repos"] = new_repos
            self.save()
            return True
        return False

    def attach_dir(self, dir_path: str, files: List[str]) -> Dict[str, Any]:
        """Attach an external directory into memory."""
        dirs = self.data.setdefault("attached_dirs", [])
        existing = next((d for d in dirs if d.get("path") == dir_path), None)
        dir_info = {
            "path": dir_path,
            "files_count": len(files),
            "attached_at": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        }
        if existing:
            existing.update(dir_info)
        else:
            dirs.append(dir_info)

        current_files = self.data.setdefault("indexed_files", [])
        for f in files[:50]:
            if f not in current_files:
                current_files.append(f)

        self.add_fact(f"Directory attached: {dir_path} ({len(files)} files)")
        self.save()
        return dir_info

    def get_memory_prompt_block(self) -> str:
        lines = [
            "# Persistent Memory & Project Knowledge Base",
            f"- Project Path: `{self.workspace}`",
            "- Known User Directives & Preferences:"
        ]
        for pref in self.data.get("user_preferences", []):
            lines.append(f"  * {pref}")
        if self.data.get("project_facts"):
            lines.append("- Project Architecture Facts:")
            for fact in self.data["project_facts"][:6]:
                lines.append(f"  * {fact}")
        if self.data.get("attached_repos"):
            lines.append("- Attached External Repositories:")
            for r in self.data["attached_repos"]:
                lines.append(f"  * {r['name']} at `{r['path']}` ({r.get('files_count', 0)} files) - Remote: {r.get('url', '')}")
        if self.data.get("attached_dirs"):
            lines.append("- Attached External Directories:")
            for d in self.data["attached_dirs"]:
                lines.append(f"  * `{d['path']}` ({d.get('files_count', 0)} files)")
        if self.data.get("recent_turns_summary"):
            lines.append("- Recent Interactions & Context:")
            for r in self.data["recent_turns_summary"][-4:]:
                lines.append(f"  * [{r['timestamp']}] {r['summary']}")
        return "\n".join(lines)


class ECCLoader:
    """Loads ECC (Everything Claude Code) configuration from .claude/ directory.
    Integrates commands, rules, research playbooks, instincts, guardrails,
    enterprise controls, and workflow definitions.
    """
    def __init__(self, workspace: Path):
        self.workspace = Path(workspace).resolve()
        self.claude_dir = self.workspace / ".claude"
        self._cache: Dict[str, Any] = {}

    @property
    def available(self) -> bool:
        return self.claude_dir.exists() and self.claude_dir.is_dir()

    def load_identity(self) -> Dict[str, Any]:
        """Load .claude/identity.json"""
        try:
            f = self.claude_dir / "identity.json"
            if f.exists():
                return json.loads(f.read_text(encoding="utf-8"))
        except Exception:
            pass
        return {}

    def load_rules(self) -> List[str]:
        """Load all .claude/rules/*.md rule files."""
        rules = []
        rules_dir = self.claude_dir / "rules"
        if rules_dir.exists():
            for md in sorted(rules_dir.glob("*.md")):
                try:
                    rules.append(md.read_text(encoding="utf-8").strip())
                except Exception:
                    pass
        return rules

    def load_commands(self) -> Dict[str, str]:
        """Load all .claude/commands/*.md workflow commands."""
        commands = {}
        cmds_dir = self.claude_dir / "commands"
        if cmds_dir.exists():
            for md in sorted(cmds_dir.glob("*.md")):
                try:
                    commands[md.stem] = md.read_text(encoding="utf-8").strip()
                except Exception:
                    pass
        return commands

    def load_research_playbook(self) -> Optional[str]:
        """Load .claude/research/ playbook."""
        res_dir = self.claude_dir / "research"
        if res_dir.exists():
            for md in res_dir.glob("*.md"):
                try:
                    return md.read_text(encoding="utf-8").strip()
                except Exception:
                    pass
        return None

    def load_instincts(self) -> Optional[str]:
        """Load .claude/homunculus/instincts/ YAML instincts."""
        instincts_dir = self.claude_dir / "homunculus" / "instincts" / "inherited"
        if instincts_dir.exists():
            for f in instincts_dir.glob("*.yaml"):
                try:
                    return f.read_text(encoding="utf-8").strip()
                except Exception:
                    pass
        return None

    def load_enterprise_controls(self) -> Optional[str]:
        """Load .claude/enterprise/controls.md."""
        f = self.claude_dir / "enterprise" / "controls.md"
        if f.exists():
            try:
                return f.read_text(encoding="utf-8").strip()
            except Exception:
                pass
        return None

    def load_team_config(self) -> Dict[str, Any]:
        """Load .claude/team/ config."""
        for f in (self.claude_dir / "team").glob("*.json") if (self.claude_dir / "team").exists() else []:
            try:
                return json.loads(f.read_text(encoding="utf-8"))
            except Exception:
                pass
        return {}

    def load_ecc_tools(self) -> Dict[str, Any]:
        """Load .claude/ecc-tools.json manifest."""
        f = self.claude_dir / "ecc-tools.json"
        if f.exists():
            try:
                return json.loads(f.read_text(encoding="utf-8"))
            except Exception:
                pass
        return {}

    def get_workflow_commands(self) -> List[str]:
        """Return list of available ECC workflow command names."""
        return list(self.load_commands().keys())

    def load_skills(self) -> Dict[str, Dict[str, str]]:
        """Load all skills from .claude/skills/ directories.
        Returns dict of {collection_name: {skill_name: description}}.
        Supports OmniRoute skills, awesome-claude-skills, and custom skills.
        """
        skills: Dict[str, Dict[str, str]] = {}
        skills_dir = self.claude_dir / "skills"
        if not skills_dir.exists():
            return skills

        for collection_dir in sorted(skills_dir.iterdir()):
            if not collection_dir.is_dir():
                continue
            collection_name = collection_dir.name
            collection_skills: Dict[str, str] = {}

            # Check for skill subdirectories with SKILL.md
            for skill_dir in sorted(collection_dir.iterdir()):
                if not skill_dir.is_dir():
                    continue
                skill_md = skill_dir / "SKILL.md"
                if skill_md.exists():
                    try:
                        content = skill_md.read_text(encoding="utf-8")
                        # Extract description from YAML frontmatter
                        desc = skill_dir.name
                        for line in content.split("\n"):
                            if line.strip().startswith("description:"):
                                desc = line.split("description:", 1)[1].strip().strip('"').strip("'")
                                break
                        collection_skills[skill_dir.name] = desc
                    except Exception:
                        collection_skills[skill_dir.name] = skill_dir.name

            if collection_skills:
                skills[collection_name] = collection_skills

        return skills

    def get_skills_summary(self) -> str:
        """Get a compact summary of all installed skills."""
        skills = self.load_skills()
        if not skills:
            return ""
        lines = []
        for collection, skill_map in skills.items():
            lines.append(f"  - {collection}: {len(skill_map)} skills ({', '.join(list(skill_map.keys())[:6])}{'...' if len(skill_map) > 6 else ''})")
        return "\n".join(lines)

    def build_ecc_prompt_block(self) -> str:
        """Build a system prompt block from all ECC configurations."""
        if not self.available:
            return ""

        parts = ["\n# ECC (Everything Claude Code) Configuration"]

        # Identity
        identity = self.load_identity()
        if identity:
            parts.append(f"- Technical Level: {identity.get('technicalLevel', 'technical')}")
            style = identity.get('preferredStyle', {})
            parts.append(f"- Preferred Style: verbosity={style.get('verbosity', 'minimal')}, code_comments={style.get('codeComments', True)}")

        # Rules & Guardrails
        rules = self.load_rules()
        if rules:
            parts.append("\n## Project Rules & Guardrails")
            for rule_text in rules:
                # Extract just the key content, skip redundant headers
                lines = rule_text.split("\n")
                key_lines = []
                for line in lines:
                    stripped = line.strip()
                    if stripped and not stripped.startswith("# ") and stripped != "---":
                        key_lines.append(stripped)
                if key_lines:
                    parts.append("- " + "; ".join(key_lines[:5]))

        # Research Playbook
        playbook = self.load_research_playbook()
        if playbook:
            parts.append("\n## Research Playbook")
            parts.append("When doing documentation-heavy, source-sensitive, or broad-context tasks:")
            parts.append("1. Inspect local code and docs first.")
            parts.append("2. Browse only for unstable or external facts.")
            parts.append("3. Summarize findings with file paths, commands, or links.")
            parts.append("4. Include concrete dates when facts may change over time.")
            parts.append("5. Keep a short evidence trail for each recommendation.")

        # Workflow commands available
        wf_commands = self.get_workflow_commands()
        if wf_commands:
            parts.append("\n## Available ECC Workflow Commands")
            for cmd_name in wf_commands:
                parts.append(f"- /{cmd_name}: Use `/workflow {cmd_name}` to run this workflow")

        # Installed Skills
        skills_summary = self.get_skills_summary()
        if skills_summary:
            parts.append("\n## Installed Skills Collections")
            parts.append(skills_summary)

        # Enterprise controls
        controls = self.load_enterprise_controls()
        if controls:
            parts.append("\n## Enterprise Controls")
            parts.append("- Security-sensitive workflow changes require explicit reviewer acknowledgement.")
            parts.append("- Audit suppressions must include a reason and the narrowest viable matcher.")

        return "\n".join(parts)


class ProjectContext:
    def __init__(self, workspace_dir: Optional[str] = None):
        self.workspace = Path(workspace_dir or os.getcwd()).resolve()
        self.memory = PersistentMemory(self.workspace)
        self.ecc = ECCLoader(self.workspace)

    def get_git_info(self) -> Dict[str, str]:
        """Detect git branch, status, and latest commit."""
        info = {"branch": "", "status": "", "commit": ""}
        try:
            res_branch = subprocess.run(
                ["git", "rev-parse", "--abbrev-ref", "HEAD"],
                cwd=str(self.workspace),
                capture_output=True,
                text=True,
                timeout=2
            )
            if res_branch.returncode == 0:
                info["branch"] = res_branch.stdout.strip()

            res_status = subprocess.run(
                ["git", "status", "--short"],
                cwd=str(self.workspace),
                capture_output=True,
                text=True,
                timeout=2
            )
            if res_status.returncode == 0:
                info["status"] = res_status.stdout.strip()

            res_log = subprocess.run(
                ["git", "log", "-1", "--oneline"],
                cwd=str(self.workspace),
                capture_output=True,
                text=True,
                timeout=2
            )
            if res_log.returncode == 0:
                info["commit"] = res_log.stdout.strip()
        except Exception:
            pass
        return info

    def find_claude_md(self) -> Optional[str]:
        """Search for CLAUDE.md in current directory or parent directories."""
        cur = self.workspace
        while True:
            candidate = cur / "CLAUDE.md"
            if candidate.exists() and candidate.is_file():
                try:
                    return candidate.read_text(encoding="utf-8")
                except Exception:
                    pass
            if cur.parent == cur:
                break
            cur = cur.parent
        return None

    def detect_tech_stack(self) -> List[str]:
        """Detect tech stack from project files."""
        stack = []
        if (self.workspace / "package.json").exists():
            stack.append("Node.js/TypeScript")
        if (self.workspace / "pyproject.toml").exists() or (self.workspace / "requirements.txt").exists():
            stack.append("Python")
        if (self.workspace / "Cargo.toml").exists():
            stack.append("Rust")
        if (self.workspace / "go.mod").exists():
            stack.append("Go")
        if (self.workspace / "Dockerfile").exists():
            stack.append("Docker")
        return stack

    def build_system_prompt(self) -> str:
        """
        Construct Claude Code authentic system prompt enriched with
        Network Environment, User IP, and Persistent Project Memory.
        """
        git_info = self.get_git_info()
        stack = self.detect_tech_stack()
        claude_md = self.find_claude_md()
        net_env = get_network_environment(self.workspace)
        memory_block = self.memory.get_memory_prompt_block()

        prompt_parts = []
        
        all_system_path = Path("/home/feds/all_system.md")
        if all_system_path.exists():
            try:
                with open(all_system_path, "r", encoding="utf-8") as f:
                    content = f.read()
                    
                # TRUNCATION FIX: Groq free tier has a strict 7000 Input Tokens Per Minute limit.
                # If we send 25k chars, it translates to ~6k tokens, leaving no room for chat history
                # and causing a 413 API Error. We dynamically truncate to 12k chars (~2.8k tokens)
                # to guarantee it never crashes the Groq ITPM limit.
                if len(content) > 12000:
                    content = content[:12000] + "\n... [SYSTEM PROMPT TRUNCATED TO AVOID GROQ 7000 ITPM CONTEXT LIMIT] ..."
                prompt_parts.append(f"====== SYSTEM INSTRUCTIONS OVERRIDE ======\n{content}\n==========================================\n")
            except Exception:
                pass

        prompt_parts.extend([
            "You are Claude Code (Coder), Anthropic's intelligent agentic development assistant running in the user's terminal.",
            "You are paired with the user to solve software engineering tasks with high intelligence, autonomy, and precision.",
            "",
            "# Core Operating Directives",
            "- Defer to the user's intent: Deeply inspect relevant code before formulating solutions.",
            "- Direct PC & System Access: You have direct, unconstrained access to the user's machine and filesystem like OpenCode and Claude Code. You can inspect, read, edit, and create files anywhere across the computer, and execute bash commands to run system utilities, linters, package managers, and development servers with professional precision.",
            "- In general, do not propose changes to code you haven't read. Always use `view_file` to read existing code and surrounding context before proposing edits.",
            "- Use `edit_file` for surgical code modifications.",
            "- Prefer editing existing files over creating new ones. Do not introduce speculative abstractions or unnecessary wrapper layers.",
            "- Default to writing minimal, self-documenting code.",
            "- Rigorous Verification: Before concluding a task, verify the solution works. Execute tests, run linters, or test imports via `bash` or `run_npx`.",
            "- Tools without VS Code: You can run tools like `npx` and `npm` directly using the `run_npx` and `run_npm` tools or `bash` without requiring VS Code.",
            "- App & Project Creation Location: When creating, scaffolding, or generating new apps or projects, save them under the user's Documents folder in 'my apps' (e.g., `~/Documents/my apps/<app_name>`) unless the user explicitly specifies another directory.",
            "",
            "# Professional Execution Guidelines",
            "- Direct, High-Focus Output: Maintain maximum focus and high signal-to-noise ratio. Deliver clean, professional code, diagnostics, and explanations without conversational fluff, emojis, or unnecessary commentary.",
            "- Thinking Disabled by Default: Deliver answers directly and efficiently. Only perform extended chain-of-thought reasoning if the user explicitly requests thinking.",
            "- In your thinking block: break down the goal, analyze edge cases and failure modes, inspect preconditions, outline the surgical edit plan, and specify verification tests.",
            "- Always synthesize a complete, actionable, high-quality answer.",
            "",
            "# Using Your Dedicated Tools",
            "- `run_npx`: Run any npx command or package directly (e.g. `npx create-vite`, `npx tailwindcss`, `npx tsx`) without VS Code.",
            "- `run_npm`: Run npm commands (install, run build, test) directly in the project directory.",
            "- `view_file`: Read code with line numbers.",
            "- `edit_file`: Surgically replace unique blocks of code.",
            "- `write_file`: Create or overwrite files.",
            "- `glob`: Search for files matching patterns.",
            "- `grep_search`: Fast regex or text search across codebase.",
            "- `bash`: Run shell commands with cross-platform compatibility.",
            "- `scaffold_website`: Generate full modern responsive web apps.",
            "- `serve_website`: Launch local HTTP development servers.",
            "",
            "# System & Network Environment",
            f"- User Local IP: {net_env['local_ip']}",
            f"- User Public IP: {net_env['public_ip']}",
            f"- Host Machine: {net_env['hostname']} ({net_env['os_name']} {net_env['os_release']} {net_env['architecture']})",
            f"- Python Runtime: v{net_env['python_version']}",
            f"- NPX Runtime: {net_env['npx_version'] if net_env['npx_version'] else 'Available'}",
            f"- Working Directory: {self.workspace}",
            f"- Detected Stack: {', '.join(stack) if stack else 'General Software'}",
            "",
            memory_block
        ])

        # Inject ECC (Everything Claude Code) configuration into system prompt
        ecc_block = self.ecc.build_ecc_prompt_block()
        if ecc_block:
            prompt_parts.append(ecc_block)

        if git_info.get("branch"):
            prompt_parts.append(f"- Git Branch: {git_info['branch']}")
            if git_info.get("commit"):
                prompt_parts.append(f"- Latest Commit: {git_info['commit']}")
            if git_info.get("status"):
                prompt_parts.append(f"- Git Status:\n```\n{git_info['status']}\n```")

        if claude_md:
            prompt_parts.extend([
                "",
                "# Project Guidelines (from CLAUDE.md):",
                claude_md
            ])

        return "\n".join(prompt_parts)


class ConversationManager:
    """Manages chat history and saves complete transcripts directly to project sessions/."""

    def __init__(self, system_prompt: str, workspace_dir: Optional[str] = None):
        self.system_prompt = system_prompt
        self.workspace_dir = workspace_dir or os.getcwd()
        self.messages: List[Dict[str, Any]] = []
        self.summary_prefix: Optional[str] = None
        self.session_title: Optional[str] = None
        self.session_filename: Optional[str] = None
        self.session_start_time = datetime.datetime.now()
        self.has_code_activity = False
        self.modified_files: List[str] = []

    def add_user_message(self, content: str):
        self.messages.append({"role": "user", "content": content})
        if not self.session_title:
            self.session_title = content[:60].strip()

    def add_assistant_message(self, content: str, tool_calls: Optional[List[Dict[str, Any]]] = None):
        msg: Dict[str, Any] = {"role": "assistant", "content": content or ""}
        if tool_calls:
            msg["tool_calls"] = tool_calls
        self.messages.append(msg)

    def add_tool_result(self, tool_call_id: str, tool_name: str, output: str, path_arg: Optional[str] = None):
        self.messages.append({
            "role": "tool",
            "tool_call_id": tool_call_id,
            "name": tool_name,
            "content": output
        })
        if tool_name in ["write_file", "edit_file", "create_file", "run_npx", "run_npm", "bash", "write_to_file", "replace_file_content"]:
            self.has_code_activity = True
        if path_arg and path_arg not in self.modified_files:
            self.modified_files.append(path_arg)

    def clear(self):
        self.messages.clear()
        self.summary_prefix = None
        self.session_filename = None
        self.session_title = None
        self.has_code_activity = False
        self.modified_files.clear()

    def compact(self) -> int:
        """Compress older turns into an ongoing context summary."""
        if len(self.messages) <= 6:
            return 0

        to_compact = self.messages[:-6]
        retained = self.messages[-6:]

        summary_lines = []
        for m in to_compact:
            role = m.get("role", "")
            content = m.get("content", "")
            if isinstance(content, str) and content:
                snippet = content.strip().replace("\n", " ")
                if len(snippet) > 120:
                    snippet = snippet[:117] + "..."
                summary_lines.append(f"- {role.capitalize()}: {snippet}")
            elif m.get("tool_calls"):
                for tc in m["tool_calls"]:
                    fn = tc.get("function", {}).get("name", "tool")
                    summary_lines.append(f"- Tool called: `{fn}`")

        new_summary = "Compacted conversation context:\n" + "\n".join(summary_lines)
        if self.summary_prefix:
            self.summary_prefix += "\n" + new_summary
        else:
            self.summary_prefix = new_summary

        self.messages = retained
        return len(to_compact)

    def get_messages_for_llm(self) -> List[Dict[str, Any]]:
        result = []
        if self.summary_prefix:
            result.append({
                "role": "system",
                "content": f"[Context Summary of earlier steps]:\n{self.summary_prefix}"
            })
        result.extend(self.messages)
        return result

    def save_session(self, model_display_name: str) -> Optional[Path]:
        """
        Save only code artifacts and file modifications to <workspace>/sessions/.
        Do not save the conversational transcript or user prompts.
        """
        if not self.has_code_activity:
            return None

        sessions_dir = Path(self.workspace_dir) / "sessions"
        try:
            sessions_dir.mkdir(parents=True, exist_ok=True)
        except Exception:
            sessions_dir = get_documents_dir()

        date_str = self.session_start_time.strftime("%Y-%m-%d_%H-%M-%S")
        
        if self.modified_files:
            file_names = "_".join(Path(f).name.replace(".", "_") for f in self.modified_files[:2])
            filename = f"{date_str}_{file_names}_code.md"
        else:
            filename = f"{date_str}_generated_code.md"

        target_path = sessions_dir / filename

        md_lines = [
            f"# Generated Code & Modifications",
            f"- **Timestamp:** {self.session_start_time.strftime('%Y-%m-%d %H:%M:%S')}",
            f"- **Model:** {model_display_name}",
            f"- **Workspace:** `{self.workspace_dir}`",
            f"- **Files Modified:** {', '.join(f'`{f}`' for f in self.modified_files) if self.modified_files else 'None'}",
            "",
            "---",
            ""
        ]

        # Only extract code blocks and tool file modifications
        for m in self.messages:
            role = m.get("role")
            content = m.get("content", "")
            
            if role == "assistant":
                if m.get("tool_calls"):
                    for tc in m["tool_calls"]:
                        fn = tc.get("function", {})
                        if fn.get("name") in ["replace_file_content", "write_to_file", "edit_file", "bash"]:
                            try:
                                args = json.loads(fn.get("arguments", "{}"))
                                if fn.get("name") == "write_to_file":
                                    md_lines.append(f"### 📄 Created/Overwrote `{args.get('TargetFile')}`")
                                    md_lines.append(f"```\n{args.get('CodeContent', '')}\n```\n")
                                elif fn.get("name") == "replace_file_content":
                                    md_lines.append(f"### ✏️ Edited `{args.get('TargetFile')}`")
                                    md_lines.append(f"```\n{args.get('ReplacementContent', '')}\n```\n")
                                elif fn.get("name") == "bash":
                                    cmd = args.get("CommandLine", "")
                                    if "echo" in cmd or "cat" in cmd or "sed" in cmd:
                                        md_lines.append(f"### 💻 Terminal Command")
                                        md_lines.append(f"```bash\n{cmd}\n```\n")
                            except Exception:
                                pass
                
                # Extract markdown code blocks from assistant text
                if content:
                    code_blocks = re.findall(r"```(?:\w+)?\n(.*?)\n```", content, re.DOTALL)
                    if code_blocks:
                        md_lines.append("### 💡 Generated Code Snippets\n")
                        for idx, block in enumerate(code_blocks):
                            md_lines.append(f"Snippet {idx+1}:\n```\n{block}\n```\n")

        # Only save if we actually found code to extract
        if len(md_lines) <= 8:
            return None

        try:
            target_path.write_text("\n".join(md_lines), encoding="utf-8")
            
            # Mirror to Documents for convenience
            try:
                doc_dir = get_documents_dir()
                doc_target = doc_dir / filename
                doc_target.write_text("\n".join(md_lines), encoding="utf-8")
            except Exception:
                pass

            return target_path
        except Exception:
            return None

    def save_code_to_documents(self, model_display_name: str) -> Optional[Path]:
        """Save code artifacts ONLY when actual code activity occurred."""
        if not self.has_code_activity:
            return None
        return self.save_session(model_display_name)
