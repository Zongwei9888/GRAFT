from __future__ import annotations

import csv
import json
import tempfile
import unittest
from pathlib import Path

from experiments.agent_reward_bench.build_matrix import (
    build_matrix,
    load_primary_annotations,
    parse_binary_label,
)


class AgentRewardBenchAdapterTests(unittest.TestCase):
    def test_primary_annotations_keep_first_expert_row(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "annotations.csv"
            with path.open("w", encoding="utf-8", newline="") as handle:
                writer = csv.DictWriter(
                    handle,
                    fieldnames=[
                        "annotator_name",
                        "benchmark",
                        "task_id",
                        "model_name",
                        "trajectory_success",
                    ],
                )
                writer.writeheader()
                writer.writerows(
                    [
                        {
                            "annotator_name": "A",
                            "benchmark": "web",
                            "task_id": "1",
                            "model_name": "agent",
                            "trajectory_success": "Successful",
                        },
                        {
                            "annotator_name": "B",
                            "benchmark": "web",
                            "task_id": "1",
                            "model_name": "agent",
                            "trajectory_success": "Unsuccessful",
                        },
                    ]
                )
            rows = load_primary_annotations(path)
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["annotator_name"], "A")

    def test_matrix_parses_generic_and_functional_judges_with_cost(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            annotation = {
                "benchmark": "web",
                "task_id": "task.1",
                "model_name": "agent",
                "trajectory_success": "Successful",
            }
            base = root / "web" / "agent"
            functional = base / "functional"
            generic = base / "judge-a"
            functional.mkdir(parents=True)
            generic.mkdir(parents=True)
            common = {"trajectory_info": {"summary_info": {"cum_reward": 1.0}}}
            (functional / "task.1.json").write_text(
                json.dumps({**common, "judge": "functional"}), encoding="utf-8"
            )
            (generic / "task.1.json").write_text(
                json.dumps(
                    {
                        **common,
                        "judge": "judge-a",
                        "judge_model_name": "model-a",
                        "provider": "provider-a",
                        "cost": {"total_price": 0.01},
                        "response": {
                            "choices": [
                                {"message": {"content": "<success>Successful</success>"}}
                            ],
                            "usage": {"total_tokens": 123},
                        },
                    }
                ),
                encoding="utf-8",
            )
            rows, audit = build_matrix([annotation], root, ["functional", "judge-a"])
            self.assertEqual(audit["complete_rows"], 1)
            self.assertTrue(rows[0]["judgments"]["judge-a"]["correct"])
            self.assertEqual(audit["median_total_tokens"]["judge-a"], 123)
            self.assertEqual(audit["median_total_cost_usd"]["judge-a"], 0.01)

    def test_unknown_label_fails_closed(self) -> None:
        with self.assertRaises(ValueError):
            parse_binary_label("maybe")


if __name__ == "__main__":
    unittest.main()
