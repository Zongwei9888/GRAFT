from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from experiments.coding_verifier_matrix.verifier_matrix import (
    _changed_paths,
    capture_baseline,
    materialize_config,
)
from graft.registry import load_config


class CodingVerifierMatrixTests(unittest.TestCase):
    def test_baseline_capture_is_external_and_detects_changed_paths(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            repo = root / "repo"
            archive_root = root / "archives"
            repo.mkdir()
            (repo / "source.txt").write_text("before\n", encoding="utf-8")

            baseline = capture_baseline(repo, archive_root)
            (repo / "source.txt").write_text("after\n", encoding="utf-8")
            (repo / "added.txt").write_text("new\n", encoding="utf-8")

            self.assertEqual(baseline["status"], "captured")
            self.assertTrue(Path(baseline["archive_path"]).is_file())
            self.assertNotEqual(Path(baseline["archive_path"]).parent, repo)
            self.assertEqual(
                _changed_paths(
                    {"source.txt": "new", "added.txt": "new"},
                    baseline["file_hashes"],
                ),
                ("added.txt", "source.txt"),
            )

    def test_matrix_config_pins_every_model_stage_and_verifier(self) -> None:
        payload = materialize_config("gpt-test")
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.json"
            path.write_text(json.dumps(payload), encoding="utf-8")
            config = load_config(path)

        self.assertEqual(config.behavior_modeler.model, "gpt-test")
        self.assertEqual(config.verifier_planner.model, "gpt-test")
        self.assertTrue(config.verifier_templates)
        self.assertTrue(
            all(item.model == "gpt-test" for item in config.verifier_templates)
        )


if __name__ == "__main__":
    unittest.main()
