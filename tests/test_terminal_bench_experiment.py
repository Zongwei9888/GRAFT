from __future__ import annotations

import json
import unittest
from pathlib import Path

from graft.registry import load_config

from experiments.terminal_bench.profile_loader import load_public_profile


PROJECT_ROOT = Path(__file__).resolve().parents[1]
EXPERIMENT_ROOT = PROJECT_ROOT / "experiments" / "terminal_bench"


class TerminalBenchExperimentTests(unittest.TestCase):
    def test_v1_public_profile_is_frozen_as_a_negative_historical_artifact(self) -> None:
        config_path = (
            EXPERIMENT_ROOT
            / "profiles"
            / "session-window-debug"
            / "config.json"
        )
        raw = json.loads(config_path.read_text(encoding="utf-8"))
        self.assertEqual(raw["version"], 1)
        with self.assertRaisesRegex(ValueError, "historical experiment artifacts"):
            load_config(config_path)

    def test_profile_loader_rejects_path_traversal(self) -> None:
        with self.assertRaises(ValueError):
            load_public_profile("../session-window-debug")

    def test_historical_fixture_is_not_referenced_by_product_runtime(self) -> None:
        product_text = "\n".join(
            path.read_text(encoding="utf-8", errors="ignore")
            for path in (PROJECT_ROOT / "src" / "graft").rglob("*.py")
        )
        self.assertNotIn("session-window-debug", product_text)
        self.assertNotIn("public-session-contract", product_text)

    def test_pair_config_remains_pinned_for_reproducibility(self) -> None:
        path = (
            EXPERIMENT_ROOT
            / "configs"
            / "session-window-debug-pair-r10.json"
        )
        raw = json.loads(path.read_text(encoding="utf-8"))
        dataset = raw["datasets"][0]
        self.assertTrue(dataset["ref"].startswith("sha256:"))
        self.assertEqual(
            dataset["task_names"], ["terminal-bench/session-window-debug"]
        )


if __name__ == "__main__":
    unittest.main()
