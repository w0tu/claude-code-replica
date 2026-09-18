import os
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch, MagicMock

from claude_replica.config import ConfigManager
from claude_replica.agent import AgentSession
from claude_replica.commands import (
    handle_command,
    handle_add_repo,
    handle_repo_command,
    handle_add_dir,
    handle_github_command,
    handle_pr_command,
    handle_issue_command,
    handle_agents_command,
    handle_status_command,
    handle_commit_push_pr
)


class TestSlashCommands(unittest.TestCase):
    def setUp(self):
        self.test_dir = tempfile.mkdtemp()
        self.config_mgr = ConfigManager()
        self.session = AgentSession(config_mgr=self.config_mgr, workspace_dir=self.test_dir)

    def tearDown(self):
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_help_command(self):
        res = handle_command("/help", self.session)
        self.assertTrue(res)

    def test_status_command(self):
        res = handle_command("/status", self.session)
        self.assertTrue(res)

    def test_version_command(self):
        res = handle_command("/version", self.session)
        self.assertTrue(res)

    def test_agents_command(self):
        res = handle_command("/agents", self.session)
        self.assertTrue(res)

    def test_github_command_empty_repo(self):
        res = handle_command("/github", self.session)
        self.assertTrue(res)

    def test_pr_command(self):
        res = handle_command("/pr", self.session)
        self.assertTrue(res)

    def test_issue_command(self):
        res = handle_command("/issue", self.session)
        self.assertTrue(res)

    def test_repo_list_empty(self):
        res = handle_command("/repo", self.session)
        self.assertTrue(res)
        res_list = handle_command("/repo list", self.session)
        self.assertTrue(res_list)

    def test_add_dir(self):
        sub_dir = Path(self.test_dir) / "my_extra_code"
        sub_dir.mkdir(parents=True, exist_ok=True)
        (sub_dir / "test_file.py").write_text("print('hello')")

        res = handle_command(f"/add-dir {sub_dir}", self.session)
        self.assertTrue(res)

        attached_dirs = self.session.project_context.memory.data.get("attached_dirs", [])
        self.assertEqual(len(attached_dirs), 1)
        self.assertEqual(attached_dirs[0]["path"], str(sub_dir.resolve()))
        self.assertIn("my_extra_code/test_file.py", self.session.project_context.memory.data.get("indexed_files", []))

    @patch("subprocess.run")
    def test_add_repo_mocked_clone(self, mock_run):
        # Mock git clone and git branch/log commands
        mock_res = MagicMock()
        mock_res.returncode = 0
        mock_res.stdout = "main"
        mock_res.stderr = ""
        mock_run.return_value = mock_res

        # Create dummy repo dir to simulate cloned repo
        dummy_repo_dir = Path(self.test_dir) / "repos" / "fastapi"
        dummy_repo_dir.mkdir(parents=True, exist_ok=True)
        (dummy_repo_dir / ".git").mkdir()
        (dummy_repo_dir / "main.py").write_text("from fastapi import FastAPI")

        res = handle_command("/add-repo tiangolo/fastapi", self.session)
        self.assertTrue(res)

        attached_repos = self.session.project_context.memory.data.get("attached_repos", [])
        self.assertEqual(len(attached_repos), 1)
        self.assertEqual(attached_repos[0]["name"], "fastapi")
        self.assertEqual(attached_repos[0]["url"], "https://github.com/tiangolo/fastapi.git")

        # Test /repo status
        res_status = handle_command("/repo status", self.session)
        self.assertTrue(res_status)

        # Test /repo remove
        res_rm = handle_command("/repo remove fastapi", self.session)
        self.assertTrue(res_rm)
        attached_after = self.session.project_context.memory.data.get("attached_repos", [])
        self.assertEqual(len(attached_after), 0)

    def test_config_command(self):
        res = handle_command("/config", self.session)
        self.assertTrue(res)

    def test_mcp_hooks_ide_commands(self):
        self.assertTrue(handle_command("/mcp", self.session))
        self.assertTrue(handle_command("/hooks", self.session))
        self.assertTrue(handle_command("/ide", self.session))
        self.assertTrue(handle_command("/autocompact", self.session))

    def test_commit_push_pr_no_changes(self):
        res = handle_command("/commit-push-pr", self.session)
        self.assertTrue(res)


if __name__ == "__main__":
    unittest.main()
