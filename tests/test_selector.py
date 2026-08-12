from __future__ import annotations

import unittest
from dataclasses import replace

from graft.registry import SelectionPolicy
from graft.schema import (
    Behavior,
    FailureMode,
    FeedbackGraph,
    PromotionRequirement,
    SharedBlindSpot,
    VerifierSpec,
    VerifierValueEstimate,
)
from graft.selection import (
    InvalidFeedbackGraph,
    OriginalHypergraphSelector,
    ValueAwareSelector,
)


def verifier(
    identifier: str,
    probability: float,
    cost: float = 1.0,
    *,
    blocking: bool = True,
) -> VerifierSpec:
    return VerifierSpec(
        verifier_id=identifier,
        kind="codex_review",
        cost=cost,
        blocking=blocking,
        failure_modes=("f",),
        estimated_detection={"f": probability},
    )


def graph(*, blind_spots: tuple[SharedBlindSpot, ...] = ()) -> FeedbackGraph:
    return FeedbackGraph(
        source_hash="checkpoint",
        behaviors=(Behavior("b", "required behavior", (), ("result",), 1, 1, 0),),
        failure_modes=(
            FailureMode("f", "b", "behavior fails", "dynamic", (), (), 1),
        ),
        verifiers=(verifier("a", 0.7), verifier("b", 0.7), verifier("c", 0.7)),
        shared_blind_spots=blind_spots,
    )


class OriginalSelectorTests(unittest.TestCase):
    def test_nonblocking_detection_does_not_consume_stop_gating_budget(self) -> None:
        custom = FeedbackGraph(
            source_hash="checkpoint",
            behaviors=(Behavior("b", "required behavior", (), ("result",), 1, 1, 0),),
            failure_modes=(
                FailureMode("f", "b", "behavior fails", "dynamic", (), (), 1),
            ),
            verifiers=(
                verifier("advisory", 0.99, blocking=False),
                verifier("actionable", 0.4, blocking=True),
            ),
            shared_blind_spots=(),
        )
        selected = OriginalHypergraphSelector().select(
            custom,
            budget=1,
            policy=SelectionPolicy("lazy-greedy-hypergraph", 1, 0, 1),
        )
        self.assertEqual(selected.verifier_ids, ("actionable",))

    def test_high_order_common_failure_changes_the_selected_pair(self) -> None:
        shared = SharedBlindSpot(
            "same-interpretation",
            "a and b can share the same task misreading",
            0.8,
            ("a", "b"),
            ("f",),
            0.0,
            ("shared task interpretation",),
        )
        selected = OriginalHypergraphSelector().select(
            graph(blind_spots=(shared,)),
            budget=2,
            policy=SelectionPolicy("lazy-greedy-hypergraph", 2, 0, 1),
        )
        self.assertEqual(set(selected.verifier_ids), {"a", "c"})
        self.assertNotIn("b", selected.verifier_ids)
        self.assertLessEqual(selected.total_cost, 2)

    def test_overlapping_blind_spots_do_not_need_to_be_mutually_exclusive(self) -> None:
        scenarios = (
            SharedBlindSpot("s1", "one", 0.6, ("a", "b"), ("f",), 0, ("x",)),
            SharedBlindSpot("s2", "two", 0.6, ("a", "c"), ("f",), 0, ("y",)),
        )
        selected = OriginalHypergraphSelector().select(
            graph(blind_spots=scenarios),
            budget=2,
            policy=SelectionPolicy("lazy-greedy-hypergraph", 2, 0, 1),
        )
        self.assertTrue(selected.feasible)

    def test_rejects_a_blind_spot_weight_outside_probability_range(self) -> None:
        invalid = SharedBlindSpot(
            "invalid", "bad weight", 1.2, ("a", "b"), ("f",), 0, ("x",)
        )
        with self.assertRaises(InvalidFeedbackGraph):
            OriginalHypergraphSelector().select(
                graph(blind_spots=(invalid,)),
                budget=2,
                policy=SelectionPolicy("lazy-greedy-hypergraph", 2, 0, 1),
            )


class ValueAwareSelectorTests(unittest.TestCase):
    @staticmethod
    def policy(**overrides) -> SelectionPolicy:
        values = {
            "algorithm": "value-aware-hypergraph",
            "max_verifiers": 2,
            "min_marginal_gain_per_cost": 0,
            "residual_risk_threshold": 1,
            "strategy": "value-aware",
            "min_net_value": 0,
            "uncertainty_penalty": 0,
            "repair_value": 1,
            "regression_cost": 0,
            "wall_time_weight": 0,
            "model_cost_weight": 0,
            "nominal_cost_weight": 0,
        }
        values.update(overrides)
        return SelectionPolicy(**values)

    @staticmethod
    def value_graph(estimate: VerifierValueEstimate, *, promotion=None) -> FeedbackGraph:
        candidate = verifier("dynamic", 0.9)
        candidate = replace(
            candidate,
            value_estimate=estimate,
            revalidates_feedback=promotion is not None,
            isolation="temporary-copy" if promotion is not None else "ephemeral",
        )
        return FeedbackGraph(
            source_hash="checkpoint",
            behaviors=(Behavior("b", "required", (), ("result",), 1, 1, 0),),
            failure_modes=(FailureMode("f", "b", "fails", "dynamic", (), (), 1),),
            verifiers=(candidate,),
            shared_blind_spots=(),
            promotion=promotion,
        )

    def test_positive_detection_is_not_enough_without_repair_value(self) -> None:
        selected = ValueAwareSelector().select(
            self.value_graph(
                VerifierValueEstimate(
                    actionability=0,
                    repair_success=1,
                    regression_risk=0,
                    producer_evidence_overlap=0,
                    confidence=1,
                    predicted_duration_s=1,
                )
            ),
            budget=2,
            policy=self.policy(),
        )
        self.assertTrue(selected.no_op)
        self.assertEqual(selected.verifier_ids, ())
        self.assertEqual(selected.net_value, 0)

    def test_high_overlap_with_producer_evidence_selects_noop(self) -> None:
        selected = ValueAwareSelector().select(
            self.value_graph(
                VerifierValueEstimate(
                    actionability=1,
                    repair_success=1,
                    regression_risk=0,
                    producer_evidence_overlap=1,
                    confidence=1,
                    predicted_duration_s=1,
                )
            ),
            budget=2,
            policy=self.policy(),
        )
        self.assertTrue(selected.no_op)

    def test_positive_conservative_net_value_beats_noop(self) -> None:
        selected = ValueAwareSelector().select(
            self.value_graph(
                VerifierValueEstimate(
                    actionability=1,
                    repair_success=1,
                    regression_risk=0,
                    producer_evidence_overlap=0,
                    confidence=1,
                    predicted_duration_s=1,
                )
            ),
            budget=2,
            policy=self.policy(),
        )
        self.assertEqual(selected.verifier_ids, ("dynamic",))
        self.assertFalse(selected.no_op)
        self.assertGreater(selected.net_value, 0)

    def test_promotion_revalidation_is_required_even_when_discovery_value_is_low(self) -> None:
        promotion = PromotionRequirement("old", None, (), ("failure",), (), ())
        selected = ValueAwareSelector().select(
            self.value_graph(VerifierValueEstimate(), promotion=promotion),
            budget=2,
            policy=self.policy(),
        )
        self.assertEqual(selected.verifier_ids, ("dynamic",))
        self.assertFalse(selected.no_op)

    def test_higher_value_discovery_singleton_cannot_replace_promotion(self) -> None:
        promotion = PromotionRequirement("old", None, (), ("failure",), (), ())
        base = self.value_graph(
            VerifierValueEstimate(
                actionability=0.1,
                repair_success=0.1,
                regression_risk=0,
                producer_evidence_overlap=0,
                confidence=1,
                predicted_duration_s=1,
            ),
            promotion=promotion,
        )
        required = replace(base.verifiers[0], verifier_id="promotion")
        discovery = replace(
            base.verifiers[0],
            verifier_id="discovery",
            revalidates_feedback=False,
            value_estimate=VerifierValueEstimate(
                actionability=1,
                repair_success=1,
                regression_risk=0,
                producer_evidence_overlap=0,
                confidence=1,
                predicted_duration_s=1,
            ),
        )
        selected = ValueAwareSelector().select(
            replace(base, verifiers=(required, discovery)),
            budget=1,
            policy=self.policy(max_verifiers=1),
        )
        self.assertEqual(selected.verifier_ids, ("promotion",))
        self.assertFalse(selected.no_op)

    def test_resource_infeasibility_is_not_reported_as_noop_value(self) -> None:
        selected = ValueAwareSelector().select(
            self.value_graph(
                VerifierValueEstimate(
                    actionability=1,
                    repair_success=1,
                    regression_risk=0,
                    producer_evidence_overlap=0,
                    confidence=1,
                    predicted_duration_s=30,
                    predicted_model_cost_usd=0,
                )
            ),
            budget=2,
            policy=self.policy(),
            available_wall_time_s=10,
        )
        self.assertFalse(selected.no_op)
        self.assertFalse(selected.feasible)
        self.assertEqual(selected.evaluated_candidates, 0)
        self.assertEqual(selected.verifier_ids, ())


if __name__ == "__main__":
    unittest.main()
