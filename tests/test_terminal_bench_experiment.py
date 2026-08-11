from __future__ import annotations

import json
import re
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

    def test_original_method_html_pilot_has_a_matched_native_control(self) -> None:
        graft_path = (
            EXPERIMENT_ROOT / "configs" / "html-js-filter-graft-original-r10.json"
        )
        native_path = (
            EXPERIMENT_ROOT / "configs" / "html-js-filter-native-r10.json"
        )
        graft = json.loads(graft_path.read_text(encoding="utf-8"))
        native = json.loads(native_path.read_text(encoding="utf-8"))

        self.assertEqual(graft["datasets"], native["datasets"])
        self.assertTrue(graft["datasets"][0]["ref"].startswith("sha256:"))
        self.assertEqual(
            graft["datasets"][0]["task_names"],
            ["terminal-bench/html-js-filter"],
        )
        self.assertEqual(
            graft["agents"][0]["model_name"], native["agents"][0]["model_name"]
        )
        for field in ("version", "reasoning_effort"):
            self.assertEqual(
                graft["agents"][0]["kwargs"][field],
                native["agents"][0]["kwargs"][field],
            )
        self.assertEqual(
            graft["agents"][0]["kwargs"]["graft_commit"],
            "2ecea330faa0fce9b4e86688d831f22d73d80ace",
        )

    def test_postfix_payments_pilot_has_a_matched_native_control(self) -> None:
        graft_path = (
            EXPERIMENT_ROOT
            / "configs"
            / "payments-pipeline-fix-graft-original-r10.json"
        )
        native_path = (
            EXPERIMENT_ROOT / "configs" / "payments-pipeline-fix-native-r10.json"
        )
        graft = json.loads(graft_path.read_text(encoding="utf-8"))
        native = json.loads(native_path.read_text(encoding="utf-8"))

        self.assertEqual(graft["datasets"], native["datasets"])
        self.assertEqual(
            graft["datasets"][0]["task_names"],
            ["terminal-bench/payments-pipeline-fix"],
        )
        self.assertEqual(
            graft["agents"][0]["model_name"], native["agents"][0]["model_name"]
        )
        for field in ("version", "reasoning_effort"):
            self.assertEqual(
                graft["agents"][0]["kwargs"][field],
                native["agents"][0]["kwargs"][field],
            )
        self.assertRegex(
            graft["agents"][0]["kwargs"]["graft_commit"], r"^[0-9a-f]{40}$"
        )

    def test_authority_fix_bun_pilot_is_frozen_and_matched(self) -> None:
        graft_path = (
            EXPERIMENT_ROOT
            / "configs"
            / "bun-sourcemap-leak-graft-authority-r10.json"
        )
        native_path = (
            EXPERIMENT_ROOT / "configs" / "bun-sourcemap-leak-native-r10.json"
        )
        graft = json.loads(graft_path.read_text(encoding="utf-8"))
        native = json.loads(native_path.read_text(encoding="utf-8"))
        self.assertEqual(graft["datasets"], native["datasets"])
        self.assertEqual(
            graft["datasets"][0]["task_names"],
            ["terminal-bench/bun-sourcemap-leak"],
        )
        self.assertEqual(
            graft["agents"][0]["model_name"], native["agents"][0]["model_name"]
        )
        for field in ("version", "reasoning_effort"):
            self.assertEqual(
                graft["agents"][0]["kwargs"][field],
                native["agents"][0]["kwargs"][field],
            )
        self.assertEqual(
            graft["agents"][0]["kwargs"]["graft_commit"],
            "69ea03a1004efd5fdb36625ad9a7e4aef17d62eb",
        )

    def test_grounded_runtime_risk_scorer_pair_is_frozen_and_matched(self) -> None:
        graft_path = (
            EXPERIMENT_ROOT
            / "configs"
            / "risk-scorer-replay-graft-grounded-r10.json"
        )
        native_path = (
            EXPERIMENT_ROOT / "configs" / "risk-scorer-replay-native-r10.json"
        )
        graft = json.loads(graft_path.read_text(encoding="utf-8"))
        native = json.loads(native_path.read_text(encoding="utf-8"))
        self.assertEqual(graft["datasets"], native["datasets"])
        self.assertEqual(
            graft["datasets"][0]["task_names"],
            ["terminal-bench/risk-scorer-replay"],
        )
        self.assertEqual(
            graft["agents"][0]["model_name"], native["agents"][0]["model_name"]
        )
        for field in ("version", "reasoning_effort"):
            self.assertEqual(
                graft["agents"][0]["kwargs"][field],
                native["agents"][0]["kwargs"][field],
            )
        self.assertEqual(
            graft["agents"][0]["kwargs"]["graft_commit"],
            "b0cde37f1eb7b484d35e1bf41934d24bd003272b",
        )

    def test_original_method_adapter_is_source_pinned_and_profile_free(self) -> None:
        source = (
            EXPERIMENT_ROOT / "graft_original_codex_agent.py"
        ).read_text(encoding="utf-8")
        commit = re.search(r'GRAFT_COMMIT = "([0-9a-f]+)"', source)
        self.assertIsNotNone(commit)
        self.assertRegex(commit.group(1), r"^[0-9a-f]{40}$")
        self.assertIn("test ! -d", source)
        self.assertIn("/profiles", source)
        self.assertNotIn("terminal-bench/html-js-filter", source)
        self.assertNotIn("test_outputs.py", source)


if __name__ == "__main__":
    unittest.main()
