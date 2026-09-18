import os
import shutil
import tempfile
import unittest
from pathlib import Path

from claude_replica.tools import ToolManager


class TestToolManager(unittest.TestCase):
    def setUp(self):
        self.test_dir = tempfile.mkdtemp()
        self.tm = ToolManager(workspace_dir=self.test_dir)

    def tearDown(self):
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_write_and_view_file(self):
        res = self.tm.write_file("hello.txt", "line1\nline2\nline3\n")
        self.assertTrue(res.success)
        self.assertTrue(Path(self.test_dir, "hello.txt").exists())

        view_res = self.tm.view_file("hello.txt", offset=1, limit=10)
        self.assertTrue(view_res.success)
        self.assertIn("line1", view_res.output)
        self.assertIn("line2", view_res.output)

    def test_edit_file(self):
        self.tm.write_file("code.py", "def foo():\n    return 42\n")
        edit_res = self.tm.edit_file("code.py", "return 42", "return 100")
        self.assertTrue(edit_res.success)
        
        with open(Path(self.test_dir, "code.py")) as f:
            content = f.read()
        self.assertEqual(content, "def foo():\n    return 100\n")

    def test_glob_and_grep(self):
        self.tm.write_file("src/main.py", "print('hello world')")
        self.tm.write_file("src/util.py", "def add(a, b): return a + b")

        glob_res = self.tm.glob_files("src/*.py")
        self.assertTrue(glob_res.success)
        self.assertIn("src/main.py", glob_res.output)

        grep_res = self.tm.grep_search("hello world")
        self.assertTrue(grep_res.success)
        self.assertIn("src/main.py:1:", grep_res.output)

    def test_bash(self):
        bash_res = self.tm.run_bash("echo 'claude-code-replica-test'")
        self.assertTrue(bash_res.success)
        self.assertIn("claude-code-replica-test", bash_res.output)

    def test_scaffold_website(self):
        site_dir = Path(self.test_dir, "my_web_site")
        res = self.tm.scaffold_website(
            project_name="my_web_site",
            title="Awesome App",
            description="Built with Fable 5",
            target_dir=str(site_dir)
        )
        self.assertTrue(res.success)
        self.assertTrue((site_dir / "index.html").exists())
        self.assertTrue((site_dir / "styles.css").exists())
        self.assertTrue((site_dir / "app.js").exists())
        
        index_content = (site_dir / "index.html").read_text()
        self.assertIn("Awesome App", index_content)
        self.assertIn("Built with Fable 5", index_content)

    def test_dispatch_scaffold_website(self):
        agency_dir = Path(self.test_dir) / "agency_site"
        res = self.tm.dispatch("scaffold_website", {
            "project_name": "agency_site",
            "title": "AI Agency",
            "description": "Next Gen AI",
            "target_dir": str(agency_dir)
        })
        self.assertTrue(res.success)
        self.assertTrue((agency_dir / "index.html").exists())

    def test_dispatch_npx_and_npm(self):
        # Test npx command dispatch with --version
        res_npx = self.tm.dispatch("run_npx", {"command": "--version"})
        self.assertIsNotNone(res_npx)

        res_npm = self.tm.dispatch("run_npm", {"command": "--version"})
        self.assertIsNotNone(res_npm)


if __name__ == "__main__":
    unittest.main()
