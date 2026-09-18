"""
Plugin Manager for Claude Code Replica.
Loads and manages official plugins declared in marketplace.json.
Integrates skills, commands, rules, and hooks from installed plugins.
"""

import os
import json
from pathlib import Path
from typing import Dict, Any, List, Optional

MARKETPLACE_CANDIDATES = [
    Path("/home/feds/marketplace.json"),
    Path(__file__).parent.parent / "marketplace.json",
    Path.home() / ".claude" / "plugins" / "marketplaces" / "claude-plugins-official" / ".claude-plugin" / "marketplace.json"
]

PLUGIN_DIRS = [
    Path.home() / ".claude" / "plugins" / "marketplaces" / "claude-plugins-official" / "plugins",
    Path("/home/feds/claude-code/src/plugins")
]


class PluginItem:
    def __init__(self, name: str, description: str, version: str = "1.0.0", category: str = "development", source: str = "", path: Optional[Path] = None):
        self.name = name
        self.description = description
        self.version = version
        self.category = category
        self.source = source
        self.path = path
        self.is_installed = path is not None and path.exists()

    def get_commands(self) -> Dict[str, str]:
        """Extract markdown commands provided by this plugin."""
        commands = {}
        if not self.path or not self.path.exists():
            return commands
        cmd_dir = self.path / "commands"
        if cmd_dir.exists():
            for f in cmd_dir.glob("*.md"):
                try:
                    commands[f.stem] = f.read_text(encoding="utf-8").strip()
                except Exception:
                    pass
        return commands

    def get_skills(self) -> Dict[str, str]:
        """Extract skills provided by this plugin."""
        skills = {}
        if not self.path or not self.path.exists():
            return skills
        skills_dir = self.path / "skills"
        if skills_dir.exists():
            for f in skills_dir.glob("**/SKILL.md"):
                try:
                    skills[f.parent.name] = f.read_text(encoding="utf-8").strip()
                except Exception:
                    pass
        return skills


class PluginManager:
    def __init__(self):
        self.plugins: Dict[str, PluginItem] = {}
        self.load_marketplace()

    def _find_marketplace_file(self) -> Optional[Path]:
        for c in MARKETPLACE_CANDIDATES:
            if c.exists() and c.is_file():
                return c
        return None

    def _find_plugin_path(self, name: str) -> Optional[Path]:
        for pdir in PLUGIN_DIRS:
            candidate = pdir / name
            if candidate.exists() and candidate.is_dir():
                return candidate
        return None

    def load_marketplace(self) -> Dict[str, PluginItem]:
        self.plugins.clear()
        m_file = self._find_marketplace_file()
        if m_file:
            try:
                with open(m_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    for item in data.get("plugins", []):
                        name = item.get("name")
                        if not name:
                            continue
                        p_path = self._find_plugin_path(name)
                        plugin = PluginItem(
                            name=name,
                            description=item.get("description", ""),
                            version=item.get("version", "1.0.0"),
                            category=item.get("category", "general"),
                            source=item.get("source", ""),
                            path=p_path
                        )
                        self.plugins[name] = plugin
            except Exception:
                pass

        # Also discover any unlisted local plugins in official directory
        for pdir in PLUGIN_DIRS:
            if pdir.exists():
                for sub in pdir.iterdir():
                    if sub.is_dir() and sub.name not in self.plugins:
                        desc = f"Claude Code plugin: {sub.name}"
                        # Try reading package.json or plugin manifest
                        manifest = sub / ".claude-plugin" / "plugin.json"
                        if manifest.exists():
                            try:
                                m_data = json.loads(manifest.read_text(encoding="utf-8"))
                                desc = m_data.get("description", desc)
                            except Exception:
                                pass
                        self.plugins[sub.name] = PluginItem(
                            name=sub.name,
                            description=desc,
                            path=sub
                        )

        return self.plugins

    def get_plugin(self, name: str) -> Optional[PluginItem]:
        return self.plugins.get(name)

    def list_plugins(self) -> List[PluginItem]:
        return sorted(self.plugins.values(), key=lambda p: p.name)

    def get_all_plugin_commands(self) -> Dict[str, str]:
        """Aggregate all commands from installed plugins."""
        all_cmds = {}
        for plugin in self.plugins.values():
            if plugin.is_installed:
                all_cmds.update(plugin.get_commands())
        return all_cmds
