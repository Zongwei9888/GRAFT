from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from graft.project_config import initialize_project, set_project_enabled
from graft.registry import ORIGINAL_METHOD_ID, load_config


class ProjectConfigTests(unittest.TestCase):
    def test_initialization_is_domain_neutral(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "an_arbitrary_asset.unknown").write_text("state", encoding="utf-8")
            result = initialize_project(root)
            self.assertEqual(
                result.verifier_ids,
                (
                    "repository-evidence-agent",
                    "semantic-reviewer",
                    "agentic-evidence-reviewer",
                    "test-agent",
                ),
            )
            config = load_config(result.path)
            self.assertEqual(config.method, ORIGINAL_METHOD_ID)
            self.assertTrue(config.enabled)
            raw = json.loads(result.path.read_text(encoding="utf-8"))
            encoded = json.dumps(raw, ensure_ascii=False).lower()
            for task_specific_token in ("pytest", "npm", "cargo", "golang", "terminal-bench"):
                self.assertNotIn(task_specific_token, encoded)
            with self.assertRaises(FileExistsError):
                initialize_project(root)

    def test_project_off_switch_preserves_dynamic_templates(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            disabled = set_project_enabled(root, False)
            self.assertTrue(disabled.created)
            self.assertFalse(load_config(disabled.path).enabled)
            enabled = set_project_enabled(root, True)
            self.assertFalse(enabled.created)
            self.assertTrue(load_config(enabled.path).enabled)
            self.assertTrue(enabled.verifier_ids)


if __name__ == "__main__":
    unittest.main()
