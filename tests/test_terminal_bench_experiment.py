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

        retry_path = (
            EXPERIMENT_ROOT
            / "configs"
            / "risk-scorer-replay-graft-grounded-r10-retry1.json"
        )
        retry = json.loads(retry_path.read_text(encoding="utf-8"))
        self.assertEqual(retry["datasets"], graft["datasets"])
        self.assertEqual(retry["agents"], graft["agents"])
        self.assertNotEqual(retry["job_name"], graft["job_name"])

        retry2_path = (
            EXPERIMENT_ROOT
            / "configs"
            / "risk-scorer-replay-graft-grounded-r10-retry2.json"
        )
        retry2 = json.loads(retry2_path.read_text(encoding="utf-8"))
        self.assertEqual(retry2["datasets"], graft["datasets"])
        self.assertEqual(retry2["agents"], graft["agents"])
        self.assertNotIn(retry2["job_name"], {graft["job_name"], retry["job_name"]})

        retry3_path = (
            EXPERIMENT_ROOT
            / "configs"
            / "risk-scorer-replay-graft-grounded-r10-retry3.json"
        )
        retry3 = json.loads(retry3_path.read_text(encoding="utf-8"))
        self.assertEqual(retry3["datasets"], graft["datasets"])
        self.assertEqual(retry3["agents"], graft["agents"])
        self.assertNotIn(
            retry3["job_name"],
            {graft["job_name"], retry["job_name"], retry2["job_name"]},
        )

        retry4_path = (
            EXPERIMENT_ROOT
            / "configs"
            / "risk-scorer-replay-graft-grounded-r10-retry4.json"
        )
        retry4 = json.loads(retry4_path.read_text(encoding="utf-8"))
        self.assertEqual(retry4["datasets"], graft["datasets"])
        self.assertEqual(retry4["agents"], graft["agents"])
        self.assertNotIn(
            retry4["job_name"],
            {
                graft["job_name"],
                retry["job_name"],
                retry2["job_name"],
                retry3["job_name"],
            },
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
        self.assertIn("safe.directory", source)
        self.assertIn("graft-original-default", source)
        self.assertNotIn("cli init --repo", source)
        self.assertNotIn("terminal-bench/html-js-filter", source)
        self.assertNotIn("test_outputs.py", source)

    def test_value_aware_adapter_uses_only_an_external_domain_neutral_profile(self) -> None:
        source = (
            EXPERIMENT_ROOT / "graft_value_aware_codex_agent.py"
        ).read_text(encoding="utf-8")
        self.assertIn('return "graft-value-aware-codex"', source)
        self.assertIn("--selection-policy value-aware", source)
        self.assertIn("--path-regex", source)
        self.assertIn("^/app$", source)
        self.assertIn("graft-value-aware", source)
        self.assertNotIn("terminal-bench/", source)
        self.assertNotIn("test_outputs.py", source)
        self.assertNotIn("hidden", source.lower())
        self.assertNotIn("/app/.graft", source)

    def test_auth_ownership_fix_is_shared_by_native_and_treatment(self) -> None:
        native_source = (EXPERIMENT_ROOT / "graft_codex_agent.py").read_text(
            encoding="utf-8"
        )
        treatment_source = (
            EXPERIMENT_ROOT / "graft_original_codex_agent.py"
        ).read_text(encoding="utf-8")
        self.assertIn("environment.default_user = agent_uid", native_source)
        self.assertIn(
            "await super().run(instruction, environment, context)", native_source
        )
        self.assertNotIn("environment.default_user = agent_uid", treatment_source)

    def test_causal_replay_adapter_is_evaluator_only(self) -> None:
        source = (
            EXPERIMENT_ROOT / "checkpoint_replay_agent.py"
        ).read_text(encoding="utf-8")
        self.assertIn('return "graft-checkpoint-replay"', source)
        self.assertIn("expected_checkpoint_key", source)
        self.assertIn("checkpoint_sha256", source)
        self.assertIn("context.metadata", source)
        self.assertNotIn("codex", source.lower())

    def test_distributed_dedup_causal_pair_is_frozen_and_matched(self) -> None:
        graft_path = (
            EXPERIMENT_ROOT / "configs" / "distributed-dedup-graft-causal-r10.json"
        )
        native_path = (
            EXPERIMENT_ROOT / "configs" / "distributed-dedup-native-r10.json"
        )
        graft = json.loads(graft_path.read_text(encoding="utf-8"))
        native = json.loads(native_path.read_text(encoding="utf-8"))
        self.assertEqual(graft["datasets"], native["datasets"])
        self.assertEqual(
            graft["datasets"][0]["task_names"],
            ["terminal-bench/distributed-dedup"],
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
            "8a0465f5cd1b6b9735a900ceb9c6ec676cabf00d",
        )
        self.assertEqual(
            graft["agents"][0]["env"]["GRAFT_CHECKPOINT_ARCHIVE_HOME"],
            "/logs/agent/graft-checkpoints",
        )

    def test_oracle_fallback_causal_pair_is_frozen_and_matched(self) -> None:
        graft_path = (
            EXPERIMENT_ROOT
            / "configs"
            / "embedding-drift-monitor-graft-causal-r10.json"
        )
        native_path = (
            EXPERIMENT_ROOT / "configs" / "embedding-drift-monitor-native-r10.json"
        )
        graft = json.loads(graft_path.read_text(encoding="utf-8"))
        native = json.loads(native_path.read_text(encoding="utf-8"))
        self.assertEqual(graft["datasets"], native["datasets"])
        self.assertEqual(
            graft["datasets"][0]["task_names"],
            ["terminal-bench/embedding-drift-monitor"],
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
            "8a0465f5cd1b6b9735a900ceb9c6ec676cabf00d",
        )

    def test_second_causal_pair_is_frozen_and_matched(self) -> None:
        graft_path = (
            EXPERIMENT_ROOT
            / "configs"
            / "live-database-cutover-graft-causal-r10.json"
        )
        native_path = (
            EXPERIMENT_ROOT / "configs" / "live-database-cutover-native-r10.json"
        )
        graft = json.loads(graft_path.read_text(encoding="utf-8"))
        native = json.loads(native_path.read_text(encoding="utf-8"))
        self.assertEqual(graft["datasets"], native["datasets"])
        self.assertEqual(
            graft["datasets"][0]["task_names"],
            ["terminal-bench/live-database-cutover"],
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
            "4c8041c8d397dc5318d557a3e5ca41b5013a4af6",
        )
        self.assertEqual(
            graft["agents"][0]["env"]["GRAFT_CHECKPOINT_ARCHIVE_HOME"],
            "/logs/agent/graft-checkpoints",
        )

    def test_second_causal_fallback_pair_is_frozen_and_matched(self) -> None:
        graft_path = (
            EXPERIMENT_ROOT / "configs" / "shadow-relay-graft-causal-r10.json"
        )
        native_path = EXPERIMENT_ROOT / "configs" / "shadow-relay-native-r10.json"
        graft = json.loads(graft_path.read_text(encoding="utf-8"))
        native = json.loads(native_path.read_text(encoding="utf-8"))
        self.assertEqual(graft["datasets"], native["datasets"])
        self.assertEqual(
            graft["datasets"][0]["task_names"],
            ["terminal-bench/shadow-relay"],
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
            "4c8041c8d397dc5318d557a3e5ca41b5013a4af6",
        )
        self.assertEqual(
            graft["agents"][0]["env"]["GRAFT_CHECKPOINT_ARCHIVE_HOME"],
            "/logs/agent/graft-checkpoints",
        )


if __name__ == "__main__":
    unittest.main()
