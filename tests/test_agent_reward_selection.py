from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from experiments.agent_reward_bench.analyze_selection import analyze
from experiments.agent_reward_bench.run_crossfit import run_crossfit
from experiments.agent_reward_bench.run_selection import (
    MatrixRow,
    SelectionModel,
    load_matrix,
    run_protocol,
    split_rows,
)


PROTOCOL_PATH = (
    Path(__file__).resolve().parents[1]
    / "experiments"
    / "agent_reward_bench"
    / "protocol.json"
)


def row(
    identifier: str,
    label: int,
    predictions: dict[str, int],
    *,
    task_id: str | None = None,
) -> MatrixRow:
    metadata = {
        judge: {
            "model": "shared-model" if judge in {"a", "b", "c"} else judge,
            "provider": "provider",
            "system_prompt_hash": "shared-prompt",
            "inputs": {"screenshot": False, "axtree": True},
            "oracle_family": "model_judge",
        }
        for judge in predictions
    }
    return MatrixRow(
        trajectory_id=identifier,
        benchmark="bench",
        task_id=task_id or identifier,
        agent="test-agent",
        label=label,
        predictions=predictions,
        metadata=metadata,
    )


class AgentRewardSelectionTests(unittest.TestCase):
    def test_split_keeps_all_agents_for_one_task_together(self) -> None:
        protocol = json.loads(PROTOCOL_PATH.read_text(encoding="utf-8"))
        rows = tuple(
            row(
                f"trajectory-{index}",
                index % 2,
                {"a": 0, "b": 1},
                task_id=f"task-{index // 2}",
            )
            for index in range(80)
        )
        development, test = split_rows(rows, protocol)
        dev_groups = {item.group_id for item in development}
        test_groups = {item.group_id for item in test}
        self.assertFalse(dev_groups & test_groups)
        self.assertEqual(len(development) + len(test), len(rows))

    def test_lineage_connected_triple_receives_high_order_correction(self) -> None:
        failures = (
            row("f1", 0, {"a": 0, "b": 0, "c": 1}),
            row("f2", 0, {"a": 0, "b": 1, "c": 0}),
            row("f3", 0, {"a": 1, "b": 0, "c": 0}),
            row("f4", 0, {"a": 1, "b": 1, "c": 1}),
        )
        model = SelectionModel(
            failures,
            ("a", "b", "c"),
            prior_strength=2,
            lineage_overlap_threshold=2,
            pair_residual_threshold=1,
        )
        portfolio = ("a", "b", "c")
        self.assertTrue(model.is_high_order_nominated(portfolio))
        self.assertNotEqual(
            model.high_order_recall(portfolio), model.pairwise_recall(portfolio)
        )

    def test_deployable_portfolios_do_not_depend_on_test_outcomes(self) -> None:
        protocol = json.loads(PROTOCOL_PATH.read_text(encoding="utf-8"))
        protocol["primary_constraints"]["cardinality_budgets"] = [1, 2]
        protocol["primary_constraints"]["set_fpr"] = 1.0
        protocol["inference"]["paired_group_bootstrap_samples"] = 20
        development = tuple(
            row(
                f"dev-{index}",
                index % 2,
                {
                    "a": 0 if index % 3 else 1,
                    "b": 0 if index % 4 else 1,
                    "c": 0 if index % 5 else 1,
                },
            )
            for index in range(20)
        )
        test_a = tuple(
            row(
                f"test-{index}",
                index % 2,
                {"a": index % 2, "b": (index + 1) % 2, "c": 1},
            )
            for index in range(20)
        )
        test_b = tuple(
            row(
                item.trajectory_id,
                item.label,
                {judge: 1 - prediction for judge, prediction in item.predictions.items()},
            )
            for item in test_a
        )
        first = run_protocol(development, test_a, protocol)
        second = run_protocol(development, test_b, protocol)
        for budget in ("1", "2"):
            first_portfolios = first["budgets"][budget]["portfolios"]
            second_portfolios = second["budgets"][budget]["portfolios"]
            for method in (
                "best_single",
                "topk_individual",
                "independent",
                "pairwise",
                "greedy_mutual_information",
                "lineage_nominated_shrunk_high_order",
            ):
                self.assertEqual(
                    first_portfolios[method]["portfolio"],
                    second_portfolios[method]["portfolio"],
                )

    def test_missing_test_oracle_is_recorded_without_aborting_protocol(self) -> None:
        protocol = json.loads(PROTOCOL_PATH.read_text(encoding="utf-8"))
        protocol["primary_constraints"]["cardinality_budgets"] = [1]
        protocol["primary_constraints"]["set_fpr"] = 0.5
        protocol["inference"]["paired_group_bootstrap_samples"] = 5
        development = (
            row("dev-failure", 0, {"a": 0}),
            row("dev-success", 1, {"a": 1}),
        )
        test = (
            row("test-failure", 0, {"a": 0}),
            row("test-success", 1, {"a": 0}),
        )

        result = run_protocol(development, test, protocol)

        oracle = result["budgets"]["1"]["portfolios"]["exhaustive_test_oracle"]
        self.assertEqual(oracle["status"], "no_feasible_portfolio")
        self.assertTrue(oracle["evaluator_only"])

    def test_posthoc_diagnostics_are_labeled_and_report_graph_density(self) -> None:
        protocol = json.loads(PROTOCOL_PATH.read_text(encoding="utf-8"))
        predictions = (
            {"a": 0, "b": 0, "c": 1, "d": 1},
            {"a": 0, "b": 1, "c": 0, "d": 1},
            {"a": 1, "b": 0, "c": 1, "d": 0},
            {"a": 1, "b": 1, "c": 0, "d": 0},
        )
        development = tuple(
            row(
                f"dev-{index}",
                0 if index < 4 else 1,
                predictions[index % len(predictions)],
            )
            for index in range(8)
        )
        test = tuple(
            row(
                f"test-{index}",
                0 if index < 4 else 1,
                predictions[(index + 1) % len(predictions)],
            )
            for index in range(8)
        )

        diagnostics = analyze(development, test, protocol)

        self.assertEqual(diagnostics["edge_diagnostics"]["pair_count"], 6)
        self.assertEqual(len(diagnostics["calibration_by_cardinality"]), 4)
        self.assertTrue(
            diagnostics["consensus_rule_exploration"][
                "not_part_of_frozen_primary_method"
            ]
        )

    def test_crossfit_keeps_task_groups_held_out_and_never_claims_method(self) -> None:
        primary = json.loads(PROTOCOL_PATH.read_text(encoding="utf-8"))
        primary["inference"]["paired_group_bootstrap_samples"] = 2
        crossfit = json.loads(
            (PROTOCOL_PATH.parent / "crossfit_protocol.json").read_text(
                encoding="utf-8"
            )
        )
        crossfit["folds"]["count"] = 2
        crossfit["cardinalities"] = [1, 2]
        crossfit["selector"]["cardinality_budgets"] = [1, 2]
        crossfit["selector"]["set_fpr"] = 1.0
        crossfit["selector"]["paired_group_bootstrap_samples"] = 2
        rows = tuple(
            row(
                f"trajectory-{index}",
                index % 2,
                {
                    "a": index % 2,
                    "b": (index // 2) % 2,
                    "c": (index // 3) % 2,
                    "d": (index // 5) % 2,
                },
                task_id=f"task-{index}",
            )
            for index in range(40)
        )

        result = run_crossfit(rows, crossfit, primary)

        self.assertEqual(result["summary"]["group_overlap"], 0)
        self.assertFalse(result["summary"]["positive_method_claim"])
        self.assertEqual(len(result["folds"]), 2)
        self.assertFalse(
            result["summary"]["high_order_decision_rule_structurally_valid"]
        )
        self.assertIsNone(result["summary"]["high_order_candidate_supported"])

    def test_incomplete_matrix_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "matrix.jsonl"
            lines = [
                {
                    "trajectory_id": "one",
                    "benchmark": "b",
                    "task_id": "1",
                    "label": 0,
                    "judgments": {"a": {"prediction": 0}},
                },
                {
                    "trajectory_id": "two",
                    "benchmark": "b",
                    "task_id": "2",
                    "label": 1,
                    "judgments": {"a": {"prediction": 1}, "b": {"prediction": 1}},
                },
            ]
            path.write_text(
                "".join(json.dumps(item) + "\n" for item in lines), encoding="utf-8"
            )
            with self.assertRaises(ValueError):
                load_matrix(path)

    def test_expert_unsure_row_is_excluded_before_selection(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "matrix.jsonl"
            rows = [
                {
                    "trajectory_id": "unsure",
                    "benchmark": "b",
                    "task_id": "1",
                    "label": None,
                    "judgments": {"a": {"prediction": 1}},
                },
                {
                    "trajectory_id": "binary",
                    "benchmark": "b",
                    "task_id": "2",
                    "label": 0,
                    "judgments": {"a": {"prediction": 0}},
                },
            ]
            path.write_text(
                "".join(json.dumps(item) + "\n" for item in rows), encoding="utf-8"
            )
            loaded = load_matrix(path)
            self.assertEqual([item.trajectory_id for item in loaded], ["binary"])


if __name__ == "__main__":
    unittest.main()
