from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from experiments.coding_verifier_matrix.verifier_matrix import (
    _changed_paths,
    capture_baseline,
    materialize_config,
    select_unique_workspace,
)
from graft.registry import load_config


PROJECT_ROOT = Path(__file__).resolve().parents[1]


class CodingVerifierMatrixTests(unittest.TestCase):
    def test_workspace_resolution_requires_one_unique_repository(self) -> None:
        self.assertEqual(
            select_unique_workspace(["/testbed", "/testbed", ""]),
            Path("/testbed"),
        )
        with self.assertRaisesRegex(RuntimeError, "discovered none"):
            select_unique_workspace([])
        with self.assertRaisesRegex(RuntimeError, "/app, /testbed"):
            select_unique_workspace(["/testbed", "/app"])

    def test_featurebench_smoke_is_source_and_runtime_pinned(self) -> None:
        path = (
            PROJECT_ROOT
            / "experiments"
            / "coding_verifier_matrix"
            / "configs"
            / "featurebench-wav2vec2-matrix-v1.json"
        )
        config = json.loads(path.read_text(encoding="utf-8"))
        agent = config["agents"][0]
        dataset = config["datasets"][0]

        self.assertEqual(agent["model_name"], "gpt-5.6-sol")
        self.assertEqual(agent["kwargs"]["version"], "0.147.0")
        self.assertRegex(agent["kwargs"]["graft_commit"], r"^[0-9a-f]{40}$")
        self.assertEqual(dataset["name"], "featurebench-lite")
        self.assertEqual(dataset["version"], "1.0")
        self.assertEqual(
            dataset["task_names"],
            [
                "huggingface__transformers.e2e8dbed."
                "test_processing_wav2vec2.4f660c78.lv1"
            ],
        )

    def test_featurebench_fallback_is_source_and_runtime_pinned(self) -> None:
        path = (
            PROJECT_ROOT
            / "experiments"
            / "coding_verifier_matrix"
            / "configs"
            / "featurebench-metaflow-matrix-v1.json"
        )
        config = json.loads(path.read_text(encoding="utf-8"))
        agent = config["agents"][0]
        dataset = config["datasets"][0]

        self.assertEqual(agent["model_name"], "gpt-5.6-sol")
        self.assertEqual(agent["kwargs"]["version"], "0.147.0")
        self.assertEqual(
            agent["kwargs"]["graft_commit"],
            "9c4f565af06140c0b02f752fbeeb455f9229b4f3",
        )
        self.assertEqual(dataset["name"], "featurebench-lite")
        self.assertEqual(dataset["version"], "1.0")
        self.assertEqual(
            dataset["task_names"],
            ["netflix__metaflow.b390a8d4.test_stub_generator.7bf08c98.lv1"],
        )

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
