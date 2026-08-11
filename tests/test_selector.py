from __future__ import annotations

import unittest

from graft.registry import SelectionPolicy
from graft.schema import (
    Behavior,
    FailureMode,
    FeedbackGraph,
    SharedBlindSpot,
    VerifierSpec,
)
from graft.selection import InvalidFeedbackGraph, OriginalHypergraphSelector


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


if __name__ == "__main__":
    unittest.main()
