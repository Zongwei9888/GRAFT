from __future__ import annotations

import json
import subprocess
import tempfile
import unittest
from pathlib import Path

from graft.project_config import initialize_project, set_project_enabled
from graft.registry import load_config


class ProjectConfigTests(unittest.TestCase):
    def test_python_project_discovers_safe_verifiers(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            subprocess.run(["git", "init", "-q", str(root)], check=True)
            (root / "pyproject.toml").write_text("[project]\nname='demo'\n", encoding="utf-8")
            source = root / "src" / "demo"
            tests = root / "tests"
            source.mkdir(parents=True)
            tests.mkdir()
            (source / "__init__.py").write_text("VALUE = 1\n", encoding="utf-8")
            (tests / "test_demo.py").write_text(
                "import unittest\n\nclass Demo(unittest.TestCase):\n    pass\n",
                encoding="utf-8",
            )
            result = initialize_project(root)
            self.assertEqual(
                result.verifier_ids,
                ("git-diff-check", "python-compile", "python-tests"),
            )
            config = load_config(result.path)
            self.assertTrue(config.enabled)
            self.assertEqual(config.budget, 2.25)
            raw = json.loads(result.path.read_text(encoding="utf-8"))
            self.assertIn("not paper calibration evidence", raw["_note"])
            with self.assertRaises(FileExistsError):
                initialize_project(root)

    def test_project_off_switch_shadows_fallback_when_trusted_by_cli(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            subprocess.run(["git", "init", "-q", str(root)], check=True)
            disabled = set_project_enabled(root, False)
            self.assertTrue(disabled.created)
            self.assertFalse(load_config(disabled.path).enabled)
            enabled = set_project_enabled(root, True)
            self.assertFalse(enabled.created)
            self.assertTrue(load_config(enabled.path).enabled)


if __name__ == "__main__":
    unittest.main()
