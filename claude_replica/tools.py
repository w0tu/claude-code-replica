"""
Built-in Tools for Claude Code Replica.
Implements Bash, View, Edit, Write, Glob, Grep, LS, and WebFetch.
Provides schemas for both Anthropic and OpenAI/Groq function calling.
"""

import os
import re
import glob
import subprocess
import urllib.request
import urllib.parse
from pathlib import Path
from typing import Dict, Any, List, Tuple, Optional, Callable

from claude_replica.ui import print_diff


DANGEROUS_COMMANDS = [
    r"\brm\s+-[a-zA-Z]*r",
    r"\bmkfs\b",
    r"\bdd\s+if=",
    r">\s*/dev/sd",
    r"\bshutdown\b",
    r"\breboot\b",
    r"\bkillall\b",
    r"\bgit\s+reset\s+--hard\b",
    r"\bgit\s+clean\s+-[a-zA-Z]*f\b",
    r"\bgit\s+push\s+-[a-zA-Z]*f\b",
    r":\(\)\s*\{\s*:\s*\|\s*:\s*&\s*\}\s*;\s*:"
]


class ToolResult:
    def __init__(self, success: bool, output: str, data: Any = None):
        self.success = success
        self.output = output
        self.data = data

    def to_dict(self) -> Dict[str, Any]:
        return {
            "success": self.success,
            "output": self.output
        }


class ToolManager:
    def __init__(self, workspace_dir: Optional[str] = None):
        self.workspace_dir = Path(workspace_dir or os.getcwd()).resolve()

    def resolve_path(self, path_str: str) -> Path:
        """Resolve a relative or absolute path against workspace."""
        p = Path(path_str).expanduser()
        if not p.is_absolute():
            p = (self.workspace_dir / p).resolve()
        return p

    def is_dangerous_bash(self, cmd: str) -> bool:
        """Check if a shell command might be destructive."""
        for pattern in DANGEROUS_COMMANDS:
            if re.search(pattern, cmd, re.IGNORECASE):
                return True
        return False

    # 1. Bash Tool
    def run_bash(self, command: str, timeout: int = 60) -> ToolResult:
        try:
            cmd_to_run = command
            # Auto-handle non-interactive npx if user/model runs e.g. "npx create-vite"
            if re.search(r"\bnpx\s+", cmd_to_run) and not re.search(r"\b(-y|--yes)\b", cmd_to_run):
                cmd_to_run = re.sub(r"\bnpx\s+", "npx --yes ", cmd_to_run, count=1)

            env = os.environ.copy()
            extra_paths = [
                "/usr/local/bin",
                "/usr/bin",
                "/bin",
                str(Path.home() / ".local" / "bin"),
                str(Path.home() / ".npm-global" / "bin"),
                str(self.workspace_dir / "node_modules" / ".bin")
            ]
            current_path = env.get("PATH", "")
            for ep in extra_paths:
                if ep not in current_path:
                    current_path = f"{ep}:{current_path}"
            env["PATH"] = current_path
            env["CI"] = "true"  # Avoid interactive prompts in background/CLI

            res = subprocess.run(
                cmd_to_run,
                shell=True,
                cwd=str(self.workspace_dir),
                capture_output=True,
                text=True,
                timeout=timeout,
                env=env
            )
            out = res.stdout
            err = res.stderr
            combined = ""
            if out:
                combined += out
            if err:
                if combined:
                    combined += "\n[stderr]:\n" + err
                else:
                    combined += err
            if not combined.strip():
                combined = f"Command finished with exit code {res.returncode} (no output)."
            
            return ToolResult(
                success=(res.returncode == 0),
                output=combined.strip(),
                data={"returncode": res.returncode}
            )
        except subprocess.TimeoutExpired:
            return ToolResult(
                success=False,
                output=f"Command timed out after {timeout} seconds."
            )
        except Exception as e:
            return ToolResult(
                success=False,
                output=f"Error executing command: {str(e)}"
            )

    # 1a. Dedicated NPX Tool (Directly run npx tools/packages without VS Code)
    def run_npx(self, command: str, package: Optional[str] = None, args: Optional[Any] = None, timeout: int = 120) -> ToolResult:
        """Execute npx packages/utilities directly without requiring VS Code."""
        npx_cmd = command.strip() if command else ""
        if package:
            args_str = " ".join(args) if isinstance(args, list) else (str(args) if args else "")
            npx_cmd = f"{package} {args_str}".strip()

        if npx_cmd.startswith("npx "):
            npx_cmd = npx_cmd[4:].strip()

        if not re.search(r"\b(-y|--yes)\b", npx_cmd):
            npx_cmd = f"--yes {npx_cmd}"

        full_command = f"npx {npx_cmd}"
        return self.run_bash(command=full_command, timeout=timeout)

    # 1b. Dedicated NPM Tool (Directly run npm commands without VS Code)
    def run_npm(self, command: str, args: Optional[Any] = None, timeout: int = 180) -> ToolResult:
        """Execute npm commands directly in the workspace without requiring VS Code."""
        npm_cmd = command.strip() if command else ""
        if npm_cmd.startswith("npm "):
            npm_cmd = npm_cmd[4:].strip()

        args_str = " ".join(args) if isinstance(args, list) else (str(args) if args else "")
        full_command = f"npm {npm_cmd} {args_str}".strip()
        return self.run_bash(command=full_command, timeout=timeout)

    # 2. View Tool
    def view_file(self, path: str, offset: int = 1, limit: int = 250) -> ToolResult:
        p = self.resolve_path(path)
        if not p.exists():
            return ToolResult(False, f"File not found: {path}")
        if p.is_dir():
            return ToolResult(False, f"Path is a directory, not a file: {path}. Use list_directory instead.")

        try:
            with open(p, "r", encoding="utf-8", errors="replace") as f:
                lines = f.readlines()
            
            total_lines = len(lines)
            start = max(1, offset)
            end = min(total_lines, start + limit - 1)
            
            if start > total_lines:
                return ToolResult(True, f"File has {total_lines} lines. Offset {start} is beyond end of file.")

            sliced = lines[start - 1 : end]
            formatted_lines = []
            for i, line in enumerate(sliced, start=start):
                formatted_lines.append(f"{i:5d} | {line.rstrip()}")

            header = f"--- {p.name} ({total_lines} total lines, showing {start}-{end}) ---"
            return ToolResult(True, f"{header}\n" + "\n".join(formatted_lines))
        except Exception as e:
            return ToolResult(False, f"Failed to read file {path}: {str(e)}")

    # 3. Edit Tool
    def edit_file(self, path: str, old_str: str, new_str: str) -> ToolResult:
        p = self.resolve_path(path)
        if not p.exists():
            return ToolResult(False, f"File not found: {path}. Use write_file to create a new file.")
        if p.is_dir():
            return ToolResult(False, f"Path is a directory: {path}")

        try:
            with open(p, "r", encoding="utf-8") as f:
                content = f.read()

            if old_str not in content:
                # Provide helpful context snippet if possible
                return ToolResult(
                    False,
                    f"Target string not found in {path}. Make sure the target lines match the file exactly, including whitespace."
                )

            occurrences = content.count(old_str)
            if occurrences > 1:
                return ToolResult(
                    False,
                    f"Target string occurs {occurrences} times in {path}. Please provide a larger unique surrounding block of code."
                )

            new_content = content.replace(old_str, new_str, 1)

            # Show diff in console
            try:
                rel_path = str(p.relative_to(self.workspace_dir))
            except ValueError:
                rel_path = str(p)
            print_diff(rel_path, content, new_content)

            with open(p, "w", encoding="utf-8") as f:
                f.write(new_content)

            return ToolResult(True, f"Successfully edited {rel_path}.")
        except Exception as e:
            return ToolResult(False, f"Failed to edit file {path}: {str(e)}")

    # 4. Write Tool
    def write_file(self, path: str, content: str) -> ToolResult:
        p = self.resolve_path(path)
        try:
            p.parent.mkdir(parents=True, exist_ok=True)
            existed = p.exists()
            old_text = ""
            if existed:
                try:
                    with open(p, "r", encoding="utf-8", errors="replace") as f:
                        old_text = f.read()
                except Exception:
                    pass

            with open(p, "w", encoding="utf-8") as f:
                f.write(content)
            
            try:
                rel_path = str(p.relative_to(self.workspace_dir))
            except ValueError:
                rel_path = str(p)

            if existed and old_text:
                print_diff(rel_path, old_text, content)

            action_desc = "Overwrote" if existed else "Created"
            line_count = len(content.splitlines())
            return ToolResult(True, f"{action_desc} {rel_path} ({line_count} lines, {len(content)} bytes).")
        except Exception as e:
            return ToolResult(False, f"Failed to write to {path}: {str(e)}")

    # 5. Glob Tool
    def glob_files(self, pattern: str, path: str = ".") -> ToolResult:
        base = self.resolve_path(path)
        if not base.exists():
            return ToolResult(False, f"Path not found: {path}")

        ignore_dirs = {".git", "node_modules", "__pycache__", ".venv", "venv", ".next", "dist", "build"}
        matches = []
        
        try:
            full_pattern = str(base / pattern) if not Path(pattern).is_absolute() else pattern
            raw_matches = glob.glob(full_pattern, recursive=True)
            
            for m in raw_matches:
                p = Path(m)
                # Filter ignored paths
                parts = set(p.parts)
                if any(ign in parts for ign in ignore_dirs):
                    continue
                try:
                    rel = str(p.relative_to(self.workspace_dir))
                except ValueError:
                    rel = str(p)
                matches.append(rel)

            matches.sort()
            if not matches:
                return ToolResult(True, f"No files matched pattern '{pattern}' in '{path}'.")
            
            summary = f"Found {len(matches)} matching paths:\n" + "\n".join(matches[:100])
            if len(matches) > 100:
                summary += f"\n... and {len(matches) - 100} more."
            return ToolResult(True, summary)
        except Exception as e:
            return ToolResult(False, f"Glob search failed: {str(e)}")

    # 6. Grep Tool
    def grep_search(self, pattern: str, path: str = ".", case_sensitive: bool = False) -> ToolResult:
        base = self.resolve_path(path)
        if not base.exists():
            return ToolResult(False, f"Search path not found: {path}")

        flags = 0 if case_sensitive else re.IGNORECASE
        try:
            regex = re.compile(pattern, flags)
        except re.error as e:
            return ToolResult(False, f"Invalid regular expression '{pattern}': {str(e)}")

        ignore_dirs = {".git", "node_modules", "__pycache__", ".venv", "venv", "dist", "build"}
        matches = []

        def search_file(fp: Path):
            try:
                with open(fp, "r", encoding="utf-8", errors="ignore") as f:
                    for idx, line in enumerate(f, start=1):
                        if regex.search(line):
                            try:
                                rel = str(fp.relative_to(self.workspace_dir))
                            except ValueError:
                                rel = str(fp)
                            matches.append(f"{rel}:{idx}: {line.strip()}")
                            if len(matches) >= 80:
                                return
            except Exception:
                pass

        if base.is_file():
            search_file(base)
        else:
            for root, dirs, files in os.walk(base):
                dirs[:] = [d for d in dirs if d not in ignore_dirs]
                for file in files:
                    fp = Path(root) / file
                    search_file(fp)
                    if len(matches) >= 80:
                        break
                if len(matches) >= 80:
                    break

        if not matches:
            return ToolResult(True, f"No matches found for '{pattern}' in {path}.")

        result_text = f"Found {len(matches)} match(es):\n" + "\n".join(matches)
        if len(matches) >= 80:
            result_text += "\n[Search capped at 80 matches]"
        return ToolResult(True, result_text)

    # 7. List Directory Tool
    def list_directory(self, path: str = ".") -> ToolResult:
        base = self.resolve_path(path)
        if not base.exists():
            return ToolResult(False, f"Directory not found: {path}")
        if not base.is_dir():
            return ToolResult(False, f"Path is a file, not a directory: {path}")

        try:
            entries = []
            for item in sorted(base.iterdir(), key=lambda x: (not x.is_dir(), x.name.lower())):
                if item.name.startswith(".") and item.name not in [".env", ".gitignore", ".claude"]:
                    continue
                if item.is_dir():
                    entries.append(f"📁 {item.name}/")
                else:
                    size = item.stat().st_size
                    if size < 1024:
                        size_str = f"{size} B"
                    elif size < 1024 * 1024:
                        size_str = f"{size / 1024:.1f} KB"
                    else:
                        size_str = f"{size / (1024 * 1024):.1f} MB"
                    entries.append(f"📄 {item.name} ({size_str})")

            try:
                rel = str(base.relative_to(self.workspace_dir))
                display_dir = "." if rel == "." else rel
            except ValueError:
                display_dir = str(base)

            if not entries:
                return ToolResult(True, f"Directory '{display_dir}' is empty.")

            return ToolResult(True, f"Contents of '{display_dir}' ({len(entries)} items):\n" + "\n".join(entries))
        except Exception as e:
            return ToolResult(False, f"Failed to list directory: {str(e)}")

    # 8. WebFetch Tool
    def fetch_url(self, url: str) -> ToolResult:
        try:
            req = urllib.request.Request(
                url,
                headers={"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 ClaudeCodeReplica/2.1"}
            )
            with urllib.request.urlopen(req, timeout=15) as resp:
                raw_html = resp.read().decode("utf-8", errors="replace")
                
            # Strip tags, scripts, styles
            text = re.sub(r"<script[\s\S]*?</script>", "", raw_html, flags=re.IGNORECASE)
            text = re.sub(r"<style[\s\S]*?</style>", "", text, flags=re.IGNORECASE)
            text = re.sub(r"<[^>]+>", " ", text)
            text = re.sub(r"\s+", " ", text).strip()
            
            if len(text) > 4000:
                text = text[:4000] + "\n... [Content truncated at 4000 chars]"
            return ToolResult(True, text)
        except Exception as e:
            return ToolResult(False, f"Failed to fetch URL {url}: {str(e)}")

    def dispatch(
        self,
        name: str,
        args: Dict[str, Any],
        permission_checker: Optional[Callable[[str, str], bool]] = None
    ) -> ToolResult:
        """Route tool call to appropriate implementation with permission checking."""
        if name == "bash" or name == "run_command":
            cmd = args.get("command", "")
            if permission_checker:
                is_danger = self.is_dangerous_bash(cmd)
                allowed = permission_checker("bash", cmd if is_danger else f"run shell command: {cmd}")
                if not allowed:
                    return ToolResult(False, "Action was denied by user permission.")
            return self.run_bash(command=cmd, timeout=args.get("timeout", 60))

        elif name == "view_file" or name == "read_file":
            return self.view_file(
                path=args.get("path", ""),
                offset=args.get("offset", 1),
                limit=args.get("limit", 250)
            )

        elif name == "edit_file" or name == "replace_content":
            path = args.get("path", "")
            if permission_checker:
                allowed = permission_checker("edit_file", f"Modify file: {path}")
                if not allowed:
                    return ToolResult(False, "Action was denied by user permission.")
            return self.edit_file(
                path=path,
                old_str=args.get("old_str", ""),
                new_str=args.get("new_str", "")
            )

        elif name == "write_file" or name == "create_file":
            path = args.get("path", "")
            if permission_checker:
                allowed = permission_checker("write_file", f"Write to file: {path}")
                if not allowed:
                    return ToolResult(False, "Action was denied by user permission.")
            return self.write_file(
                path=path,
                content=args.get("content", "")
            )

        elif name == "glob" or name == "find_files":
            return self.glob_files(
                pattern=args.get("pattern", "*"),
                path=args.get("path", ".")
            )

        elif name == "grep_search" or name == "grep":
            return self.grep_search(
                pattern=args.get("pattern", ""),
                path=args.get("path", "."),
                case_sensitive=args.get("case_sensitive", False)
            )

        elif name == "list_dir" or name == "list_directory":
            return self.list_directory(path=args.get("path", "."))

        elif name == "fetch_url" or name == "web_fetch":
            return self.fetch_url(url=args.get("url", ""))

        elif name == "scaffold_website" or name == "create_website":
            return self.scaffold_website(
                project_name=args.get("project_name", "my_website"),
                template=args.get("template", "modern-html5"),
                title=args.get("title", "Modern Web Application"),
                description=args.get("description", "Crafted with Fable 5 & Claude Code"),
                target_dir=args.get("target_dir")
            )

        elif name == "serve_website" or name == "preview_website":
            return self.serve_website(
                directory=args.get("directory", "."),
                port=args.get("port", 8000)
            )

        elif name == "run_npx" or name == "npx":
            cmd = args.get("command", "")
            if permission_checker:
                allowed = permission_checker("run_npx", f"Execute npx: {cmd}")
                if not allowed:
                    return ToolResult(False, "Action was denied by user permission.")
            return self.run_npx(
                command=cmd,
                package=args.get("package"),
                args=args.get("args"),
                timeout=args.get("timeout", 120)
            )

        elif name == "run_npm" or name == "npm":
            cmd = args.get("command", "")
            if permission_checker:
                allowed = permission_checker("run_npm", f"Execute npm: {cmd}")
                if not allowed:
                    return ToolResult(False, "Action was denied by user permission.")
            return self.run_npm(
                command=cmd,
                args=args.get("args"),
                timeout=args.get("timeout", 180)
            )

        else:
            return ToolResult(False, f"Unknown tool: {name}")

    # 9. Website Scaffolding Tool (Fable 5 / Web Skills)
    def scaffold_website(
        self,
        project_name: str = "my_website",
        template: str = "modern-html5",
        title: str = "Modern Web Application",
        description: str = "Crafted with Fable 5 & Claude Code",
        target_dir: Optional[str] = None
    ) -> ToolResult:
        """Scaffold a modern, responsive web application structure under ~/Documents/my apps or target_dir."""
        from claude_replica.config import get_documents_dir
        if target_dir:
            out_dir = Path(target_dir)
        else:
            p = Path(project_name)
            if p.is_absolute():
                out_dir = p
            else:
                out_dir = get_documents_dir() / project_name
        out_dir.mkdir(parents=True, exist_ok=True)
        
        index_html = f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <meta name="description" content="{description}" />
  <title>{title}</title>
  <link rel="preconnect" href="https://fonts.googleapis.com">
  <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
  <link href="https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700;800&display=swap" rel="stylesheet">
  <link rel="stylesheet" href="styles.css" />
</head>
<body class="theme-dark">
  <header class="navbar">
    <div class="nav-container">
      <a href="#" class="brand-logo">
        <span class="logo-icon">◈</span>
        <span class="logo-text">{title}</span>
      </a>
      <nav class="nav-links" id="navMenu">
        <a href="#features">Features</a>
        <a href="#showcase">Showcase</a>
        <a href="#contact">Contact</a>
        <button id="themeToggle" class="btn-icon" aria-label="Toggle Theme">🌙</button>
      </nav>
      <button class="menu-toggle" id="menuToggle" aria-label="Toggle Menu">☰</button>
    </div>
  </header>

  <main>
    <section class="hero-section">
      <div class="container hero-content">
        <div class="badge">⚡ Fable 5 Autonomous Web Engine</div>
        <h1 class="hero-title">{title}</h1>
        <p class="hero-subtitle">{description}</p>
        <div class="cta-group">
          <a href="#features" class="btn btn-primary">Explore Features</a>
          <a href="#contact" class="btn btn-secondary">Get In Touch</a>
        </div>
      </div>
    </section>

    <section id="features" class="features-section">
      <div class="container">
        <div class="section-header">
          <h2>Core Capabilities</h2>
          <p>Engineered for high performance, accessibility, and modern aesthetics.</p>
        </div>
        <div class="grid grid-3">
          <div class="card feature-card">
            <div class="card-icon">🚀</div>
            <h3>Blazing Fast</h3>
            <p>Optimized asset delivery, zero runtime overhead, and responsive CSS grid layout.</p>
          </div>
          <div class="card feature-card">
            <div class="card-icon">🎨</div>
            <h3>Modern Design</h3>
            <p>Fluid typography, dark/light theme switching, and smooth micro-interactions.</p>
          </div>
          <div class="card feature-card">
            <div class="card-icon">📱</div>
            <h3>Mobile First</h3>
            <p>Engineered to look gorgeous on phones, tablets, laptops, and wide displays.</p>
          </div>
        </div>
      </div>
    </section>

    <section id="contact" class="contact-section">
      <div class="container">
        <div class="contact-card">
          <h2>Stay Connected</h2>
          <p>Built autonomously with Fable 5 & Claude Code.</p>
          <form id="contactForm" class="contact-form">
            <input type="email" placeholder="Enter your email" required />
            <button type="submit" class="btn btn-primary">Subscribe</button>
          </form>
          <div id="formFeedback" class="form-feedback"></div>
        </div>
      </div>
    </section>
  </main>

  <footer class="footer">
    <div class="container footer-content">
      <p>&copy; 2026 {title}. Generated with Fable 5 & Claude Code Replica.</p>
    </div>
  </footer>

  <script src="app.js"></script>
</body>
</html>
"""

        styles_css = """/* Fable 5 Modern Web Design System */
:root {
  --bg-primary: #0f1117;
  --bg-secondary: #181b24;
  --bg-card: #202430;
  --accent: #d97757;
  --accent-hover: #e08b6e;
  --text-primary: #f0f2f5;
  --text-secondary: #a0aec0;
  --border: #2d3748;
  --font-sans: 'Inter', system-ui, -apple-system, sans-serif;
  --transition: all 0.25s ease;
}

body.theme-light {
  --bg-primary: #f8fafc;
  --bg-secondary: #edf2f7;
  --bg-card: #ffffff;
  --accent: #d97757;
  --accent-hover: #c46445;
  --text-primary: #1a202c;
  --text-secondary: #4a5568;
  --border: #e2e8f0;
}

* { box-sizing: border-box; margin: 0; padding: 0; }
body {
  font-family: var(--font-sans);
  background-color: var(--bg-primary);
  color: var(--text-primary);
  line-height: 1.6;
  transition: var(--transition);
}

.container { max-width: 1140px; margin: 0 auto; padding: 0 1.5rem; }

.navbar {
  position: sticky;
  top: 0;
  background: rgba(15, 17, 23, 0.85);
  backdrop-filter: blur(12px);
  border-bottom: 1px solid var(--border);
  z-index: 100;
}
.nav-container {
  display: flex;
  justify-content: space-between;
  align-items: center;
  max-width: 1140px;
  margin: 0 auto;
  padding: 1rem 1.5rem;
}
.brand-logo {
  display: flex;
  align-items: center;
  gap: 0.5rem;
  font-size: 1.25rem;
  font-weight: 700;
  color: var(--accent);
  text-decoration: none;
}
.nav-links { display: flex; align-items: center; gap: 1.5rem; }
.nav-links a {
  color: var(--text-secondary);
  text-decoration: none;
  font-weight: 500;
  transition: var(--transition);
}
.nav-links a:hover { color: var(--accent); }
.btn-icon {
  background: transparent;
  border: none;
  cursor: pointer;
  font-size: 1.2rem;
}
.menu-toggle { display: none; background: none; border: none; font-size: 1.5rem; color: var(--text-primary); cursor: pointer; }

.hero-section {
  padding: 6rem 0 4rem;
  text-align: center;
}
.badge {
  display: inline-block;
  padding: 0.35rem 1rem;
  background: rgba(217, 119, 87, 0.15);
  color: var(--accent);
  border-radius: 999px;
  font-size: 0.85rem;
  font-weight: 600;
  margin-bottom: 1.5rem;
  border: 1px solid rgba(217, 119, 87, 0.3);
}
.hero-title {
  font-size: 3.25rem;
  font-weight: 800;
  letter-spacing: -0.02em;
  margin-bottom: 1rem;
  background: linear-gradient(135deg, var(--text-primary) 30%, var(--accent));
  -webkit-background-clip: text;
  -webkit-text-fill-color: transparent;
}
.hero-subtitle {
  font-size: 1.25rem;
  color: var(--text-secondary);
  max-width: 650px;
  margin: 0 auto 2.5rem;
}
.cta-group { display: flex; justify-content: center; gap: 1rem; }
.btn {
  padding: 0.8rem 1.75rem;
  border-radius: 8px;
  font-weight: 600;
  text-decoration: none;
  transition: var(--transition);
  display: inline-block;
  cursor: pointer;
  border: 1px solid transparent;
}
.btn-primary {
  background: var(--accent);
  color: #ffffff;
}
.btn-primary:hover { background: var(--accent-hover); transform: translateY(-2px); }
.btn-secondary {
  background: var(--bg-card);
  color: var(--text-primary);
  border: 1px solid var(--border);
}
.btn-secondary:hover { border-color: var(--accent); transform: translateY(-2px); }

.features-section { padding: 5rem 0; background: var(--bg-secondary); }
.section-header { text-align: center; margin-bottom: 3rem; }
.section-header h2 { font-size: 2.25rem; margin-bottom: 0.5rem; }
.section-header p { color: var(--text-secondary); }
.grid-3 { display: grid; grid-template-columns: repeat(auto-fit, minmax(300px, 1fr)); gap: 2rem; }
.card {
  background: var(--bg-card);
  padding: 2rem;
  border-radius: 12px;
  border: 1px solid var(--border);
  transition: var(--transition);
}
.card:hover { transform: translateY(-4px); border-color: var(--accent); }
.card-icon { font-size: 2.5rem; margin-bottom: 1rem; }
.card h3 { font-size: 1.35rem; margin-bottom: 0.5rem; }
.card p { color: var(--text-secondary); font-size: 0.95rem; }

.contact-section { padding: 5rem 0; }
.contact-card {
  max-width: 600px;
  margin: 0 auto;
  background: var(--bg-card);
  padding: 3rem;
  border-radius: 16px;
  border: 1px solid var(--border);
  text-align: center;
}
.contact-card h2 { margin-bottom: 0.5rem; }
.contact-card p { color: var(--text-secondary); margin-bottom: 2rem; }
.contact-form { display: flex; gap: 0.5rem; justify-content: center; }
.contact-form input {
  flex: 1;
  padding: 0.8rem 1rem;
  border-radius: 8px;
  border: 1px solid var(--border);
  background: var(--bg-primary);
  color: var(--text-primary);
  outline: none;
}
.form-feedback { margin-top: 1rem; font-size: 0.9rem; color: #2ecc71; font-weight: 500; }

.footer { border-top: 1px solid var(--border); padding: 2rem 0; text-align: center; color: var(--text-secondary); font-size: 0.9rem; }

@media (max-width: 768px) {
  .hero-title { font-size: 2.25rem; }
  .menu-toggle { display: block; }
  .nav-links {
    display: none;
    flex-direction: column;
    position: absolute;
    top: 100%;
    left: 0;
    width: 100%;
    background: var(--bg-primary);
    padding: 1.5rem;
    border-bottom: 1px solid var(--border);
  }
  .nav-links.active { display: flex; }
  .contact-form { flex-direction: column; }
}
"""

        app_js = """// Fable 5 Interactive Web Application Logic
document.addEventListener('DOMContentLoaded', () => {
  // Theme Toggle
  const themeToggle = document.getElementById('themeToggle');
  const currentTheme = localStorage.getItem('theme') || 'dark';
  if (currentTheme === 'light') {
    document.body.classList.remove('theme-dark');
    document.body.classList.add('theme-light');
    if (themeToggle) themeToggle.textContent = '☀️';
  }

  if (themeToggle) {
    themeToggle.addEventListener('click', () => {
      if (document.body.classList.contains('theme-dark')) {
        document.body.classList.replace('theme-dark', 'theme-light');
        themeToggle.textContent = '☀️';
        localStorage.setItem('theme', 'light');
      } else {
        document.body.classList.replace('theme-light', 'theme-dark');
        themeToggle.textContent = '🌙';
        localStorage.setItem('theme', 'dark');
      }
    });
  }

  // Mobile Navigation
  const menuToggle = document.getElementById('menuToggle');
  const navMenu = document.getElementById('navMenu');
  if (menuToggle && navMenu) {
    menuToggle.addEventListener('click', () => {
      navMenu.classList.toggle('active');
    });
  }

  // Contact Form
  const contactForm = document.getElementById('contactForm');
  const formFeedback = document.getElementById('formFeedback');
  if (contactForm && formFeedback) {
    contactForm.addEventListener('submit', (e) => {
      e.preventDefault();
      formFeedback.textContent = '✔ Thank you! Powered by Fable 5.';
      contactForm.reset();
      setTimeout(() => { formFeedback.textContent = ''; }, 4000);
    });
  }
});
"""
        try:
            (out_dir / "index.html").write_text(index_html, encoding="utf-8")
            (out_dir / "styles.css").write_text(styles_css, encoding="utf-8")
            (out_dir / "app.js").write_text(app_js, encoding="utf-8")
            return ToolResult(
                True,
                f"Successfully scaffolded modern website project '{project_name}'!\n"
                f"Files created: index.html, styles.css, app.js in `{out_dir}`.\n"
                f"Use `serve_website` to launch a live browser preview."
            )
        except Exception as e:
            return ToolResult(False, f"Failed to scaffold website: {str(e)}")

    # 10. Web Server Preview Tool
    def serve_website(self, directory: str = ".", port: int = 8000) -> ToolResult:
        """Start a local background HTTP server to preview web applications."""
        import http.server
        import socketserver
        import threading
        import socket

        target_dir = self.resolve_path(directory)
        if not target_dir.exists() or not target_dir.is_dir():
            from claude_replica.config import get_documents_dir
            doc_candidate = get_documents_dir() / directory
            if doc_candidate.exists() and doc_candidate.is_dir():
                target_dir = doc_candidate
            else:
                return ToolResult(False, f"Directory does not exist: {directory}")

        actual_port = port
        for p in range(port, port + 50):
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                if s.connect_ex(('127.0.0.1', p)) != 0:
                    actual_port = p
                    break

        class Handler(http.server.SimpleHTTPRequestHandler):
            def __init__(self, *args, **kwargs):
                super().__init__(*args, directory=str(target_dir), **kwargs)
            def log_message(self, format, *args):
                pass

        try:
            httpd = socketserver.TCPServer(("127.0.0.1", actual_port), Handler)
            server_thread = threading.Thread(target=httpd.serve_forever, daemon=True)
            server_thread.start()

            url = f"http://localhost:{actual_port}"
            return ToolResult(
                True,
                f"Web preview server is live!\n"
                f"Serving directory: {target_dir}\n"
                f"Preview URL: {url}"
            )
        except Exception as e:
            return ToolResult(False, f"Failed to start web server: {str(e)}")


# Tool Schemas for Providers
OPENAI_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "bash",
            "description": "Execute a shell/bash command in the project directory. Use this to run build commands, tests, git operations, or system utilities.",
            "parameters": {
                "type": "object",
                "properties": {
                    "command": {"type": "string", "description": "The command line string to execute."},
                    "timeout": {"type": "integer", "description": "Command timeout in seconds (default: 60)."}
                },
                "required": ["command"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "run_npx",
            "description": "Execute an npx package, tool, or CLI directly in the project without requiring VS Code (e.g. 'npx create-vite my-app', 'npx tailwindcss', 'npx tsx'). Automatically runs non-interactively.",
            "parameters": {
                "type": "object",
                "properties": {
                    "command": {"type": "string", "description": "The complete npx package and args to run (e.g. 'create-vite my-app --template react-ts' or 'tailwindcss -i input.css -o output.css')."},
                    "timeout": {"type": "integer", "description": "Timeout in seconds (default: 120)."}
                },
                "required": ["command"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "run_npm",
            "description": "Execute npm commands (install, run build, test, init -y) directly in the project directory without requiring VS Code.",
            "parameters": {
                "type": "object",
                "properties": {
                    "command": {"type": "string", "description": "The npm subcommand or script to execute (e.g. 'install', 'run build', 'test')."},
                    "timeout": {"type": "integer", "description": "Timeout in seconds (default: 180)."}
                },
                "required": ["command"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "view_file",
            "description": "View the contents of a local file with line numbers. Use this to inspect code before editing.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Relative or absolute path to the file."},
                    "offset": {"type": "integer", "description": "1-indexed line number to start reading from (default: 1)."},
                    "limit": {"type": "integer", "description": "Maximum number of lines to view (default: 250)."}
                },
                "required": ["path"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "edit_file",
            "description": "Precisely replace a block of code within an existing file. Provide exact unique text in old_str to replace with new_str.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Path to the file to edit."},
                    "old_str": {"type": "string", "description": "Exact lines of code to be replaced."},
                    "new_str": {"type": "string", "description": "New replacement lines of code."}
                },
                "required": ["path", "old_str", "new_str"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "write_file",
            "description": "Create a new file or overwrite an existing file with the specified content.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Path to the file to create or overwrite."},
                    "content": {"type": "string", "description": "Full text content of the file."}
                },
                "required": ["path", "content"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "glob",
            "description": "Search for files matching a glob pattern (e.g. '**/*.py', 'src/*.ts', '*.json').",
            "parameters": {
                "type": "object",
                "properties": {
                    "pattern": {"type": "string", "description": "Glob pattern to match against."},
                    "path": {"type": "string", "description": "Root directory to search in (default: '.')."}
                },
                "required": ["pattern"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "grep_search",
            "description": "Search file contents for regex or text pattern matches across the workspace.",
            "parameters": {
                "type": "object",
                "properties": {
                    "pattern": {"type": "string", "description": "Search term or regex pattern."},
                    "path": {"type": "string", "description": "Directory or file to search in (default: '.')."},
                    "case_sensitive": {"type": "boolean", "description": "Whether search is case-sensitive (default: false)."}
                },
                "required": ["pattern"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "list_dir",
            "description": "List files and directories in the specified path with file sizes.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Directory path to list (default: '.')."}
                },
                "required": []
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "fetch_url",
            "description": "Fetch content from a web URL and convert HTML to readable plain text.",
            "parameters": {
                "type": "object",
                "properties": {
                    "url": {"type": "string", "description": "The URL to fetch."}
                },
                "required": ["url"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "scaffold_website",
            "description": "Generate a full modern responsive website or web application (index.html, styles.css, app.js) with clean aesthetics, dark/light theme, and mobile responsiveness. Automatically saved to ~/Documents/my apps/<project_name>.",
            "parameters": {
                "type": "object",
                "properties": {
                    "project_name": {"type": "string", "description": "Target folder name under ~/Documents/my apps/ (default: 'my_website')."},
                    "template": {"type": "string", "description": "Template style ('modern-html5', 'tailwind-landing', 'portfolio')."},
                    "title": {"type": "string", "description": "Title and brand name for the website."},
                    "description": {"type": "string", "description": "Hero subtitle or product description."}
                },
                "required": []
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "serve_website",
            "description": "Launch a local background development HTTP server to preview websites and web apps live in the browser (supports projects in ~/Documents/my apps/).",
            "parameters": {
                "type": "object",
                "properties": {
                    "directory": {"type": "string", "description": "Directory to serve or subfolder in ~/Documents/my apps/ (default: '.')."},
                    "port": {"type": "integer", "description": "Preferred port number (default: 8000)."}
                },
                "required": []
            }
        }
    }
]

# Convert to Anthropic Tool Format
ANTHROPIC_TOOLS = [
    {
        "name": t["function"]["name"],
        "description": t["function"]["description"],
        "input_schema": t["function"]["parameters"]
    }
    for t in OPENAI_TOOLS
]
