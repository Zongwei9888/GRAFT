from __future__ import annotations

from collections.abc import Iterable, Mapping

from graft.schema import EmpiricalScenario


def weighted_or_rate(
    verifier_ids: Iterable[str], scenarios: Iterable[EmpiricalScenario]
) -> float:
    selected = tuple(verifier_ids)
    weighted_sum = 0.0
    total_weight = 0.0
    for scenario in scenarios:
        if scenario.weight < 0:
            raise ValueError(f"Negative scenario weight: {scenario.scenario_id}")
        miss_probability = 1.0
        for verifier_id in selected:
            probability = float(scenario.outcomes.get(verifier_id, 0.0))
            if not 0.0 <= probability <= 1.0:
                raise ValueError(
                    f"Outcome for {verifier_id} in {scenario.scenario_id} is outside [0, 1]"
                )
            miss_probability *= 1.0 - probability
        weighted_sum += scenario.weight * (1.0 - miss_probability)
        total_weight += scenario.weight
    if total_weight == 0.0:
        return 0.0
    return weighted_sum / total_weight


def set_cost(verifier_ids: Iterable[str], costs: Mapping[str, float]) -> float:
    return sum(float(costs[verifier_id]) for verifier_id in verifier_ids)
