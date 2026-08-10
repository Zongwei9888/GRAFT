from __future__ import annotations

from itertools import combinations

from graft.schema import CalibrationData, Selection, VerifierSpec

from .objective import set_cost, weighted_or_rate


class ExactEmpiricalSelector:
    """Exact subset selection under cost and set-level false-alarm constraints."""

    def __init__(self, *, max_candidates: int = 24) -> None:
        self.max_candidates = max_candidates

    def select(
        self,
        candidates: list[VerifierSpec],
        calibration: CalibrationData,
        *,
        budget: float,
        max_set_fpr: float,
    ) -> Selection:
        if budget < 0:
            raise ValueError("budget must be non-negative")
        if not 0.0 <= max_set_fpr <= 1.0:
            raise ValueError("max_set_fpr must be in [0, 1]")
        if len(candidates) > self.max_candidates:
            raise ValueError(
                f"Exact selection is capped at {self.max_candidates} candidates; "
                f"received {len(candidates)}"
            )

        ordered = sorted(candidates, key=lambda item: item.verifier_id)
        costs = {item.verifier_id: item.cost for item in ordered}
        best = Selection((), 0.0, 0.0, 0.0, True, 0)
        evaluated = 0

        for size in range(len(ordered) + 1):
            for subset in combinations(ordered, size):
                evaluated += 1
                verifier_ids = tuple(item.verifier_id for item in subset)
                cost = set_cost(verifier_ids, costs)
                if cost > budget + 1e-12:
                    continue
                false_alarm = weighted_or_rate(
                    verifier_ids, calibration.clean_scenarios
                )
                if false_alarm > max_set_fpr + 1e-12:
                    continue
                coverage = weighted_or_rate(
                    verifier_ids, calibration.failure_scenarios
                )
                candidate = Selection(
                    verifier_ids=verifier_ids,
                    expected_coverage=coverage,
                    expected_false_alarm=false_alarm,
                    total_cost=cost,
                    feasible=True,
                    evaluated_subsets=evaluated,
                )
                if self._is_better(candidate, best):
                    best = candidate

        return Selection(
            verifier_ids=best.verifier_ids,
            expected_coverage=best.expected_coverage,
            expected_false_alarm=best.expected_false_alarm,
            total_cost=best.total_cost,
            feasible=best.feasible,
            evaluated_subsets=evaluated,
        )

    @staticmethod
    def _is_better(candidate: Selection, incumbent: Selection) -> bool:
        coverage_delta = candidate.expected_coverage - incumbent.expected_coverage
        if abs(coverage_delta) > 1e-12:
            return coverage_delta > 0
        false_alarm_delta = (
            candidate.expected_false_alarm - incumbent.expected_false_alarm
        )
        if abs(false_alarm_delta) > 1e-12:
            return false_alarm_delta < 0
        cost_delta = candidate.total_cost - incumbent.total_cost
        if abs(cost_delta) > 1e-12:
            return cost_delta < 0
        return candidate.verifier_ids < incumbent.verifier_ids
