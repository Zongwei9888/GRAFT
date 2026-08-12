from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping

from graft.registry import GraftConfig
from graft.schema import (
    Behavior,
    FailureMode,
    FeedbackGraph,
    Lineage,
    ProducerEvidenceSummary,
    PromotionRequirement,
    SharedBlindSpot,
    StageCost,
    VerifierSpec,
    VerifierValueEstimate,
    tuple_of_strings,
)
from graft.selection import OriginalHypergraphSelector, ValueAwareSelector


class ReplayInputError(ValueError):
    pass


def replay_selection(
    report_path: Path,
    config: GraftConfig,
    *,
    budget: float | None = None,
):
    """Re-run only the selector over a previously materialized feedback graph."""

    graph = load_report_graph(report_path)
    selected_budget = config.budget if budget is None else budget
    if selected_budget < 0:
        raise ReplayInputError("replay budget must be non-negative")
    selector = (
        ValueAwareSelector()
        if config.selection.strategy == "value-aware"
        else OriginalHypergraphSelector()
    )
    return selector.select(
        graph,
        budget=selected_budget,
        policy=config.selection,
    )


def load_report_graph(path: Path) -> FeedbackGraph:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ReplayInputError(f"could not read replay report: {exc}") from exc
    raw = payload.get("graph") if isinstance(payload, Mapping) else None
    if not isinstance(raw, Mapping):
        raise ReplayInputError("report does not contain a feedback graph")
    try:
        return _feedback_graph(raw)
    except (KeyError, TypeError, ValueError) as exc:
        raise ReplayInputError(f"invalid feedback graph in report: {exc}") from exc


def _feedback_graph(raw: Mapping[str, Any]) -> FeedbackGraph:
    behaviors = tuple(
        Behavior(
            behavior_id=str(item["behavior_id"]),
            description=str(item["description"]),
            source_refs=tuple_of_strings(item.get("source_refs")),
            observables=tuple_of_strings(item.get("observables")),
            severity=float(item["severity"]),
            likelihood=float(item["likelihood"]),
            reach=float(item["reach"]),
        )
        for item in _objects(raw.get("behaviors"), "behaviors")
    )
    failure_modes = tuple(
        FailureMode(
            failure_mode_id=str(item["failure_mode_id"]),
            behavior_id=str(item["behavior_id"]),
            description=str(item["description"]),
            category=str(item["category"]),
            observable_signals=tuple_of_strings(item.get("observable_signals")),
            required_capabilities=tuple_of_strings(item.get("required_capabilities")),
            risk=float(item["risk"]),
        )
        for item in _objects(raw.get("failure_modes"), "failure_modes")
    )
    verifiers = tuple(
        _verifier(item) for item in _objects(raw.get("verifiers"), "verifiers")
    )
    shared = tuple(
        SharedBlindSpot(
            scenario_id=str(item["scenario_id"]),
            description=str(item["description"]),
            weight=float(item["weight"]),
            affected_verifiers=tuple_of_strings(item.get("affected_verifiers")),
            failure_modes=tuple_of_strings(item.get("failure_modes")),
            residual_detection=float(item["residual_detection"]),
            sources=tuple_of_strings(item.get("sources")),
        )
        for item in _objects(raw.get("shared_blind_spots"), "shared_blind_spots")
    )
    return FeedbackGraph(
        source_hash=str(raw["source_hash"]),
        behaviors=behaviors,
        failure_modes=failure_modes,
        verifiers=verifiers,
        shared_blind_spots=shared,
        uncertainties=tuple_of_strings(raw.get("uncertainties")),
        producer_evidence=_producer_evidence(raw.get("producer_evidence")),
        stage_costs=tuple(
            _stage_cost(item)
            for item in _objects(raw.get("stage_costs", []), "stage_costs")
        ),
        promotion=_promotion(raw.get("promotion")),
        method=str(raw.get("method", "graft-original")),
    )


def _verifier(raw: Mapping[str, Any]) -> VerifierSpec:
    value = raw.get("value_estimate")
    value_map = value if isinstance(value, Mapping) else {}
    return VerifierSpec(
        verifier_id=str(raw["verifier_id"]),
        kind=str(raw["kind"]),
        cost=float(raw["cost"]),
        blocking=bool(raw["blocking"]),
        failure_modes=tuple_of_strings(raw.get("failure_modes")),
        template_id=_optional_string(raw.get("template_id")),
        objective=str(raw.get("objective", "")),
        prompt=str(raw.get("prompt", "")),
        estimated_detection={
            str(key): float(probability)
            for key, probability in _mapping(
                raw.get("estimated_detection", {}), "estimated_detection"
            ).items()
        },
        timeout_s=float(raw.get("timeout_s", 240.0)),
        sandbox=str(raw.get("sandbox", "read-only")),
        network_access=bool(raw.get("network_access", False)),
        isolation=str(raw.get("isolation", "ephemeral")),
        model=_optional_string(raw.get("model")),
        command=tuple_of_strings(raw.get("command")),
        failure_exit_codes=tuple(int(item) for item in raw.get("failure_exit_codes", [1])),
        working_directory=_optional_string(raw.get("working_directory")),
        lineage=_lineage(raw.get("lineage")),
        value_estimate=VerifierValueEstimate(
            actionability=float(value_map.get("actionability", 0.0)),
            repair_success=float(value_map.get("repair_success", 0.0)),
            regression_risk=float(value_map.get("regression_risk", 1.0)),
            producer_evidence_overlap=float(
                value_map.get("producer_evidence_overlap", 1.0)
            ),
            confidence=float(value_map.get("confidence", 0.0)),
            predicted_duration_s=_optional_float(
                value_map.get("predicted_duration_s")
            ),
            predicted_model_cost_usd=_optional_float(
                value_map.get("predicted_model_cost_usd")
            ),
        ),
        revalidates_feedback=bool(raw.get("revalidates_feedback", False)),
    )


def _lineage(value: Any) -> Lineage:
    raw = value if isinstance(value, Mapping) else {}
    known = {
        "provider",
        "model",
        "thread_policy",
        "prompt_family",
        "context_sources",
        "modality",
        "oracle",
        "test_author",
        "metadata",
    }
    metadata = raw.get("metadata")
    extra = dict(metadata) if isinstance(metadata, Mapping) else {}
    extra.update({key: item for key, item in raw.items() if key not in known})
    return Lineage(
        provider=str(raw.get("provider", "local")),
        model=_optional_string(raw.get("model")),
        thread_policy=_optional_string(raw.get("thread_policy")),
        prompt_family=_optional_string(raw.get("prompt_family")),
        context_sources=tuple_of_strings(raw.get("context_sources")),
        modality=tuple_of_strings(raw.get("modality")),
        oracle=_optional_string(raw.get("oracle")),
        test_author=_optional_string(raw.get("test_author")),
        metadata=extra,
    )


def _producer_evidence(value: Any) -> ProducerEvidenceSummary | None:
    if value is None:
        return None
    raw = _mapping(value, "producer_evidence")
    return ProducerEvidenceSummary(
        task_epoch=int(raw["task_epoch"]),
        event_count=int(raw["event_count"]),
        succeeded=int(raw["succeeded"]),
        failed=int(raw["failed"]),
        unknown=int(raw["unknown"]),
        total_duration_s=_optional_float(raw.get("total_duration_s")),
        command_previews=tuple_of_strings(raw.get("command_previews")),
        failure_previews=tuple_of_strings(raw.get("failure_previews")),
        changed_paths=tuple_of_strings(raw.get("changed_paths")),
    )


def _stage_cost(raw: Mapping[str, Any]) -> StageCost:
    return StageCost(
        stage_id=str(raw["stage_id"]),
        kind=str(raw["kind"]),
        duration_s=float(raw["duration_s"]),
        input_tokens=_optional_int(raw.get("input_tokens")),
        cached_input_tokens=_optional_int(raw.get("cached_input_tokens")),
        output_tokens=_optional_int(raw.get("output_tokens")),
        estimated_cost_usd=_optional_float(raw.get("estimated_cost_usd")),
    )


def _promotion(value: Any) -> PromotionRequirement | None:
    if value is None:
        return None
    raw = _mapping(value, "promotion")
    return PromotionRequirement(
        feedback_checkpoint_key=str(raw["feedback_checkpoint_key"]),
        report_path=_optional_string(raw.get("report_path")),
        behavior_descriptions=tuple_of_strings(raw.get("behavior_descriptions")),
        failure_descriptions=tuple_of_strings(raw.get("failure_descriptions")),
        evidence_observations=tuple_of_strings(raw.get("evidence_observations")),
        reproduction_commands=tuple(
            tuple_of_strings(item) for item in raw.get("reproduction_commands", [])
        ),
    )


def _objects(value: Any, label: str) -> tuple[Mapping[str, Any], ...]:
    if not isinstance(value, list) or not all(isinstance(item, Mapping) for item in value):
        raise ReplayInputError(f"{label} must be an array of objects")
    return tuple(value)


def _mapping(value: Any, label: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ReplayInputError(f"{label} must be an object")
    return value


def _optional_string(value: Any) -> str | None:
    return None if value is None else str(value)


def _optional_float(value: Any) -> float | None:
    return None if value is None else float(value)


def _optional_int(value: Any) -> int | None:
    return None if value is None else int(value)
