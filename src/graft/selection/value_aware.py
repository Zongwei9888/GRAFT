from __future__ import annotations

from dataclasses import replace

from graft.registry import SelectionPolicy
from graft.schema import FeedbackGraph, Selection, VerifierSpec

from .objective import InvalidFeedbackGraph, expected_detection_utility, set_cost


class ValueAwareSelector:
    """Select evidence only when its conservative marginal net value beats No-Op."""

    def select(
        self,
        graph: FeedbackGraph,
        *,
        budget: float,
        policy: SelectionPolicy,
        available_wall_time_s: float | None = None,
        available_model_cost_usd: float | None = None,
    ) -> Selection:
        if budget < 0:
            raise ValueError("budget must be non-negative")
        if policy.strategy != "value-aware":
            raise ValueError("ValueAwareSelector requires a value-aware policy")
        candidates = tuple(sorted(graph.verifiers, key=lambda item: item.verifier_id))
        selected: tuple[VerifierSpec, ...] = ()
        remaining = list(candidates)
        evaluated = 0
        marginal_values: dict[str, float] = {}
        feasible_singletons = tuple(
            item
            for item in candidates
            if item.cost <= budget + 1e-12
            and _fits_resource_budget(
                item,
                available_wall_time_s=available_wall_time_s,
                available_model_cost_usd=available_model_cost_usd,
            )
        )

        if graph.promotion is not None:
            required = tuple(item for item in candidates if item.revalidates_feedback)
            feasible_required = tuple(
                item
                for item in required
                if item in feasible_singletons
            )
            if not feasible_required:
                return Selection(
                    verifier_ids=(),
                    expected_utility=0.0,
                    expected_coverage=0.0,
                    residual_risk=1.0,
                    total_cost=0.0,
                    feasible=False,
                    evaluated_candidates=len(required),
                    policy="value-aware",
                    net_value=0.0,
                    no_op=False,
                )
            scored = []
            for candidate in feasible_required:
                net, _, _ = expected_net_value(
                    graph, (candidate,), budget, policy
                )
                evaluated += 1
                scored.append((net, candidate.verifier_id, candidate))
            chosen_net, _, chosen = max(scored, key=lambda item: item[0])
            selected = (chosen,)
            remaining.remove(chosen)
            marginal_values[chosen.verifier_id] = chosen_net

        if not feasible_singletons:
            return Selection(
                verifier_ids=(),
                expected_utility=0.0,
                expected_coverage=0.0,
                residual_risk=1.0,
                total_cost=0.0,
                feasible=False,
                evaluated_candidates=0,
                policy="value-aware",
                net_value=0.0,
                no_op=False,
            )

        while remaining and len(selected) < policy.max_verifiers:
            current_net, _, _ = expected_net_value(graph, selected, budget, policy)
            best: VerifierSpec | None = None
            best_gain = float("-inf")
            for candidate in remaining:
                proposed = (*selected, candidate)
                if set_cost(proposed) > budget + 1e-12:
                    continue
                if not _set_fits_resource_budget(
                    proposed,
                    available_wall_time_s=available_wall_time_s,
                    available_model_cost_usd=available_model_cost_usd,
                ):
                    continue
                net, _, _ = expected_net_value(graph, proposed, budget, policy)
                evaluated += 1
                gain = net - current_net
                if (
                    gain > best_gain + 1e-12
                    or (
                        abs(gain - best_gain) <= 1e-12
                        and best is not None
                        and candidate.verifier_id < best.verifier_id
                    )
                ):
                    best = candidate
                    best_gain = gain
            if best is None or best_gain <= policy.min_net_value + 1e-12:
                break
            selected = (*selected, best)
            remaining.remove(best)
            marginal_values[best.verifier_id] = best_gain

        best_singleton: tuple[VerifierSpec, ...] = ()
        best_singleton_net = 0.0
        for candidate in feasible_singletons:
            net, _, _ = expected_net_value(graph, (candidate,), budget, policy)
            evaluated += 1
            if (
                net > best_singleton_net + 1e-12
                or (
                    abs(net - best_singleton_net) <= 1e-12
                    and best_singleton
                    and candidate.verifier_id < best_singleton[0].verifier_id
                )
            ):
                best_singleton = (candidate,)
                best_singleton_net = net

        greedy_net, _, _ = expected_net_value(graph, selected, budget, policy)
        if best_singleton_net > greedy_net + 1e-12:
            selected = best_singleton
            marginal_values = {
                best_singleton[0].verifier_id: best_singleton_net
            }

        net_value, coverage, residual = expected_net_value(
            graph, selected, budget, policy
        )
        if (
            net_value <= policy.min_net_value + 1e-12
            and graph.promotion is None
        ):
            selected = ()
            marginal_values = {}
            net_value, coverage, residual = 0.0, 0.0, 1.0
        return Selection(
            verifier_ids=tuple(item.verifier_id for item in selected),
            expected_utility=coverage,
            expected_coverage=coverage,
            residual_risk=residual,
            total_cost=set_cost(selected),
            feasible=True,
            evaluated_candidates=evaluated,
            policy="value-aware",
            net_value=net_value,
            no_op=not selected,
            marginal_values=marginal_values,
        )


def expected_net_value(
    graph: FeedbackGraph,
    selected: tuple[VerifierSpec, ...],
    budget: float,
    policy: SelectionPolicy,
) -> tuple[float, float, float]:
    adjusted = tuple(_effective_verifier(item, policy) for item in selected)
    adjusted_graph = replace(graph, verifiers=adjusted)
    _, coverage, residual = expected_detection_utility(
        adjusted_graph, (item.verifier_id for item in adjusted)
    )
    benefit = policy.repair_value * coverage
    regression = sum(
        _regression_penalty(graph, item, policy) for item in selected
    )
    execution_cost = sum(
        _execution_cost(item, budget, policy) for item in selected
    )
    return benefit - regression - execution_cost, coverage, residual


def _effective_verifier(
    verifier: VerifierSpec, policy: SelectionPolicy
) -> VerifierSpec:
    estimate = verifier.value_estimate
    for label, value in (
        ("actionability", estimate.actionability),
        ("repair_success", estimate.repair_success),
        ("regression_risk", estimate.regression_risk),
        ("producer_evidence_overlap", estimate.producer_evidence_overlap),
        ("confidence", estimate.confidence),
    ):
        if not 0 <= value <= 1:
            raise InvalidFeedbackGraph(
                f"Value estimate {verifier.verifier_id}.{label} is outside [0,1]"
            )
    raw_factor = (
        estimate.actionability
        * estimate.repair_success
        * (1.0 - estimate.producer_evidence_overlap)
    )
    conservative_factor = max(
        0.0,
        raw_factor - policy.uncertainty_penalty * (1.0 - estimate.confidence),
    )
    detection = {
        failure_id: probability * conservative_factor
        for failure_id, probability in verifier.estimated_detection.items()
    }
    return replace(verifier, estimated_detection=detection)


def _regression_penalty(
    graph: FeedbackGraph,
    verifier: VerifierSpec,
    policy: SelectionPolicy,
) -> float:
    total_risk = sum(item.risk for item in graph.failure_modes)
    if total_risk <= 0:
        return 0.0
    by_failure = {item.failure_mode_id: item for item in graph.failure_modes}
    finding_probability = sum(
        by_failure[failure_id].risk * probability
        for failure_id, probability in verifier.estimated_detection.items()
        if failure_id in by_failure
    ) / total_risk
    estimate = verifier.value_estimate
    return (
        policy.regression_cost
        * finding_probability
        * estimate.actionability
        * estimate.regression_risk
    )


def _execution_cost(
    verifier: VerifierSpec,
    budget: float,
    policy: SelectionPolicy,
) -> float:
    estimate = verifier.value_estimate
    duration = (
        estimate.predicted_duration_s
        if estimate.predicted_duration_s is not None
        else verifier.timeout_s
    )
    wall_cost = policy.wall_time_weight * min(
        1.0, max(0.0, duration) / policy.wall_time_budget_s
    )
    if estimate.predicted_model_cost_usd is None:
        model_cost = 0.0
    else:
        model_cost = policy.model_cost_weight * min(
            1.0,
            estimate.predicted_model_cost_usd / policy.model_cost_budget_usd,
        )
    nominal_cost = policy.nominal_cost_weight * (
        verifier.cost / budget if budget > 0 else (1.0 if verifier.cost > 0 else 0.0)
    )
    return wall_cost + model_cost + nominal_cost


def _fits_resource_budget(
    verifier: VerifierSpec,
    *,
    available_wall_time_s: float | None,
    available_model_cost_usd: float | None,
) -> bool:
    return _set_fits_resource_budget(
        (verifier,),
        available_wall_time_s=available_wall_time_s,
        available_model_cost_usd=available_model_cost_usd,
    )


def _set_fits_resource_budget(
    verifiers: tuple[VerifierSpec, ...],
    *,
    available_wall_time_s: float | None,
    available_model_cost_usd: float | None,
) -> bool:
    predicted_wall = sum(
        item.value_estimate.predicted_duration_s
        if item.value_estimate.predicted_duration_s is not None
        else item.timeout_s
        for item in verifiers
    )
    if (
        available_wall_time_s is not None
        and predicted_wall > max(0.0, available_wall_time_s) + 1e-12
    ):
        return False
    if available_model_cost_usd is not None:
        model_costs: list[float] = []
        for item in verifiers:
            predicted = item.value_estimate.predicted_model_cost_usd
            if predicted is None:
                if item.kind == "command":
                    predicted = 0.0
                else:
                    # An unknown model cost is not silently converted to zero.
                    return False
            model_costs.append(predicted)
        if sum(model_costs) > max(0.0, available_model_cost_usd) + 1e-12:
            return False
    return True
