from __future__ import annotations

import unittest

from graft.schema import CalibrationData, EmpiricalScenario, VerifierSpec
from graft.selection import ExactEmpiricalSelector


def verifier(identifier: str, cost: float = 1.0) -> VerifierSpec:
    return VerifierSpec(identifier, "command", cost, True, ())


class ExactSelectorTests(unittest.TestCase):
    def test_selects_complementary_pair_within_constraints(self) -> None:
        candidates = [verifier("a"), verifier("b"), verifier("c")]
        calibration = CalibrationData(
            failure_scenarios=(
                EmpiricalScenario("f1", 1.0, {"a": 1, "b": 0, "c": 1}),
                EmpiricalScenario("f2", 1.0, {"a": 0, "b": 1, "c": 0}),
            ),
            clean_scenarios=(
                EmpiricalScenario("ok", 1.0, {"a": 0, "b": 0, "c": 0.5}),
            ),
        )
        selected = ExactEmpiricalSelector().select(
            candidates, calibration, budget=2.0, max_set_fpr=0.1
        )
        self.assertEqual(selected.verifier_ids, ("a", "b"))
        self.assertEqual(selected.expected_coverage, 1.0)
        self.assertEqual(selected.expected_false_alarm, 0.0)
        self.assertLessEqual(selected.total_cost, 2.0)
        self.assertEqual(selected.evaluated_subsets, 8)

    def test_rejects_invalid_probability(self) -> None:
        calibration = CalibrationData(
            failure_scenarios=(EmpiricalScenario("bad", 1.0, {"a": 1.2}),)
        )
        with self.assertRaises(ValueError):
            ExactEmpiricalSelector().select(
                [verifier("a")], calibration, budget=1.0, max_set_fpr=1.0
            )


if __name__ == "__main__":
    unittest.main()
