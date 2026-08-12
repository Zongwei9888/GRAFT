from __future__ import annotations

from typing import Any, Mapping

from graft.schema import StageCost, TurnResult, VerifierResult


def stage_cost_from_turn(stage_id: str, kind: str, turn: TurnResult) -> StageCost:
    usage = turn.usage
    return StageCost(
        stage_id=stage_id,
        kind=kind,
        duration_s=max(0.0, float(turn.duration_s)),
        input_tokens=_optional_nonnegative_int(
            _first(usage, "input_tokens", "input_token_count")
        ),
        cached_input_tokens=_optional_nonnegative_int(
            _first(usage, "cached_input_tokens", "cached_input_token_count")
        ),
        output_tokens=_optional_nonnegative_int(
            _first(usage, "output_tokens", "output_token_count")
        ),
        estimated_cost_usd=_optional_nonnegative_float(
            _first(usage, "estimated_cost_usd", "cost_usd")
        ),
    )


def usage_total_tokens(usage: Mapping[str, Any]) -> int | None:
    input_tokens = _optional_nonnegative_int(
        _first(usage, "input_tokens", "input_token_count")
    )
    output_tokens = _optional_nonnegative_int(
        _first(usage, "output_tokens", "output_token_count")
    )
    if input_tokens is None and output_tokens is None:
        return None
    return (input_tokens or 0) + (output_tokens or 0)


def stage_cost_from_result(result: VerifierResult) -> StageCost:
    usage = result.usage
    return StageCost(
        stage_id=result.verifier_id,
        kind="verifier",
        duration_s=max(0.0, result.duration_s),
        input_tokens=_optional_nonnegative_int(
            _first(usage, "input_tokens", "input_token_count")
        ),
        cached_input_tokens=_optional_nonnegative_int(
            _first(usage, "cached_input_tokens", "cached_input_token_count")
        ),
        output_tokens=_optional_nonnegative_int(
            _first(usage, "output_tokens", "output_token_count")
        ),
        estimated_cost_usd=result.estimated_cost_usd,
    )


def _first(values: Mapping[str, Any], *keys: str) -> Any:
    for key in keys:
        if key in values:
            return values[key]
    return None


def _optional_nonnegative_int(value: Any) -> int | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    parsed = int(value)
    return parsed if parsed >= 0 else None


def _optional_nonnegative_float(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    parsed = float(value)
    return parsed if parsed >= 0 else None
