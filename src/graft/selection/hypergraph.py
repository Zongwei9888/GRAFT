from __future__ import annotations

from graft.registry import SelectionPolicy
from graft.schema import FeedbackGraph, Selection, VerifierSpec

from .objective import expected_detection_utility, set_cost


class OriginalHypergraphSelector:
    """DOCX cost-aware greedy retrieval with a best-singleton fallback."""

    def select(
        self,
        graph: FeedbackGraph,
        *,
        budget: float,
        policy: SelectionPolicy,
    ) -> Selection:
        if budget < 0:
            raise ValueError("budget must be non-negative")
        candidates = tuple(sorted(graph.verifiers, key=lambda item: item.verifier_id))
        selected: tuple[VerifierSpec, ...] = ()
        remaining = list(candidates)
        evaluated = 0

        while remaining and len(selected) < policy.max_verifiers:
            current_utility, _, _ = expected_detection_utility(
                graph, (item.verifier_id for item in selected)
            )
            best: VerifierSpec | None = None
            best_ratio = float("-inf")
            best_gain = 0.0
            for candidate in remaining:
                if set_cost((*selected, candidate)) > budget + 1e-12:
                    continue
                utility, _, _ = expected_detection_utility(
                    graph,
                    (item.verifier_id for item in (*selected, candidate)),
                )
                evaluated += 1
                gain = utility - current_utility
                ratio = float("inf") if candidate.cost == 0 and gain > 0 else (
                    gain / candidate.cost if candidate.cost > 0 else 0.0
                )
                if (
                    ratio > best_ratio + 1e-12
                    or (
                        abs(ratio - best_ratio) <= 1e-12
                        and best is not None
                        and candidate.verifier_id < best.verifier_id
                    )
                ):
                    best = candidate
                    best_ratio = ratio
                    best_gain = gain
            if best is None or best_gain <= 0:
                break
            if best.cost > 0 and best_ratio < policy.min_marginal_gain_per_cost:
                break
            selected = (*selected, best)
            remaining.remove(best)

        singleton: tuple[VerifierSpec, ...] = ()
        singleton_utility = 0.0
        for candidate in candidates:
            if candidate.cost > budget + 1e-12:
                continue
            utility, _, _ = expected_detection_utility(
                graph, (candidate.verifier_id,)
            )
            evaluated += 1
            if (
                utility > singleton_utility + 1e-12
                or (
                    abs(utility - singleton_utility) <= 1e-12
                    and singleton
                    and candidate.verifier_id < singleton[0].verifier_id
                )
            ):
                singleton = (candidate,)
                singleton_utility = utility

        greedy_utility, _, _ = expected_detection_utility(
            graph, (item.verifier_id for item in selected)
        )
        if singleton_utility > greedy_utility + 1e-12:
            selected = singleton

        utility, coverage, residual = expected_detection_utility(
            graph, (item.verifier_id for item in selected)
        )
        return Selection(
            verifier_ids=tuple(item.verifier_id for item in selected),
            expected_utility=utility,
            expected_coverage=coverage,
            residual_risk=residual,
            total_cost=set_cost(selected),
            feasible=bool(selected),
            evaluated_candidates=evaluated,
        )
