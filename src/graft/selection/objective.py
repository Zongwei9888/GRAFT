from __future__ import annotations

from collections.abc import Iterable

from graft.schema import FailureMode, FeedbackGraph, SharedBlindSpot, VerifierSpec


class InvalidFeedbackGraph(ValueError):
    pass


def expected_detection_utility(
    graph: FeedbackGraph, verifier_ids: Iterable[str]
) -> tuple[float, float, float]:
    selected_ids = tuple(verifier_ids)
    by_id = {item.verifier_id: item for item in graph.verifiers}
    try:
        selected = tuple(by_id[item] for item in selected_ids)
    except KeyError as exc:
        raise InvalidFeedbackGraph(f"Unknown verifier: {exc.args[0]}") from exc

    total_risk = sum(item.risk for item in graph.failure_modes)
    if total_risk <= 0:
        return 0.0, 0.0, 1.0
    utility = 0.0
    residual = 0.0
    for failure in graph.failure_modes:
        miss = failure_miss_probability(
            failure, selected, graph.shared_blind_spots
        )
        utility += failure.risk * (1.0 - miss)
        residual += failure.risk * miss
    return utility, utility / total_risk, residual / total_risk


def failure_miss_probability(
    failure: FailureMode,
    selected: tuple[VerifierSpec, ...],
    blind_spots: tuple[SharedBlindSpot, ...],
) -> float:
    scenarios = tuple(
        item for item in blind_spots if failure.failure_mode_id in item.failure_modes
    )
    base_miss = _conditional_miss(failure, selected, None)
    remaining_detection = 1.0 - base_miss
    for scenario in scenarios:
        if not 0 <= scenario.weight <= 1:
            raise InvalidFeedbackGraph(
                f"Blind-spot weight for {scenario.scenario_id} is outside [0,1]"
            )
        scenario_miss = _conditional_miss(failure, selected, scenario)
        if remaining_detection <= 1e-12:
            return 1.0
        relative_loss = max(0.0, scenario_miss - base_miss) / remaining_detection
        relative_loss = min(1.0, relative_loss)
        remaining_detection *= 1.0 - scenario.weight * relative_loss
    return min(1.0, max(0.0, 1.0 - remaining_detection))


def _conditional_miss(
    failure: FailureMode,
    selected: tuple[VerifierSpec, ...],
    scenario: SharedBlindSpot | None,
) -> float:
    probability = 1.0
    for verifier in selected:
        # A finding that is ineligible for Stop continuation cannot reduce the
        # modeled risk of allowing the producer to stop. Advisory verifiers stay
        # in the heterogeneous pool, but consume no feedback-gating utility.
        detection = (
            float(verifier.estimated_detection.get(failure.failure_mode_id, 0.0))
            if verifier.blocking
            else 0.0
        )
        if not 0 <= detection <= 1:
            raise InvalidFeedbackGraph(
                f"Detection estimate for {verifier.verifier_id}/"
                f"{failure.failure_mode_id} is outside [0,1]"
            )
        if (
            scenario is not None
            and verifier.verifier_id in scenario.affected_verifiers
            and failure.failure_mode_id in scenario.failure_modes
        ):
            detection *= scenario.residual_detection
        probability *= 1.0 - detection
    return probability


def set_cost(verifiers: Iterable[VerifierSpec]) -> float:
    return sum(item.cost for item in verifiers)
