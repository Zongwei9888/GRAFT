from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from graft.schema import (
    CalibrationData,
    EmpiricalScenario,
    Lineage,
    VerifierSpec,
    tuple_of_strings,
)


@dataclass(frozen=True)
class GraftConfig:
    version: int
    enabled: bool
    budget: float
    max_set_fpr: float
    checkpoint_mode: str
    max_feedback_rounds: int
    failure_policy: str
    environment_fingerprint: str
    verifiers: tuple[VerifierSpec, ...]
    calibration: CalibrationData


def _lineage(raw: Mapping[str, Any] | None) -> Lineage:
    value = raw or {}
    known = {
        "provider",
        "model",
        "thread_policy",
        "prompt_family",
        "context_sources",
        "modality",
        "oracle",
        "test_author",
    }
    return Lineage(
        provider=str(value.get("provider", "local")),
        model=_optional_string(value.get("model")),
        thread_policy=_optional_string(value.get("thread_policy")),
        prompt_family=_optional_string(value.get("prompt_family")),
        context_sources=tuple_of_strings(value.get("context_sources")),
        modality=tuple_of_strings(value.get("modality")),
        oracle=_optional_string(value.get("oracle")),
        test_author=_optional_string(value.get("test_author")),
        metadata={key: item for key, item in value.items() if key not in known},
    )


def _optional_string(value: Any) -> str | None:
    return None if value is None else str(value)


def _verifier(raw: Mapping[str, Any]) -> VerifierSpec:
    verifier_id = str(raw["id"])
    kind = str(raw["kind"])
    if kind not in {"command", "codex_review"}:
        raise ValueError(f"Unsupported verifier kind for {verifier_id}: {kind}")
    command = tuple_of_strings(raw.get("command"))
    if kind == "command" and not command:
        raise ValueError(f"Command verifier {verifier_id} has no command")
    cost = float(raw.get("cost", 1.0))
    timeout_s = float(raw.get("timeout_s", 120.0))
    sandbox = str(raw.get("sandbox", "read-only"))
    if cost < 0:
        raise ValueError(f"Verifier {verifier_id} cost must be non-negative")
    if timeout_s <= 0:
        raise ValueError(f"Verifier {verifier_id} timeout_s must be positive")
    if kind == "codex_review" and sandbox != "read-only":
        raise ValueError(f"Codex reviewer {verifier_id} must use the read-only sandbox")
    return VerifierSpec(
        verifier_id=verifier_id,
        kind=kind,
        cost=cost,
        blocking=bool(raw.get("blocking", kind == "command")),
        failure_modes=tuple_of_strings(raw.get("failure_modes")),
        timeout_s=timeout_s,
        command=command,
        failure_exit_codes=tuple(
            int(code) for code in raw.get("failure_exit_codes", [1])
        ),
        working_directory=_optional_string(raw.get("working_directory")),
        prompt_template=_optional_string(raw.get("prompt_template")),
        model=_optional_string(raw.get("model")),
        sandbox=sandbox,
        lineage=_lineage(raw.get("lineage")),
    )


def _scenarios(raw_items: list[Mapping[str, Any]], field: str) -> tuple[EmpiricalScenario, ...]:
    scenarios: list[EmpiricalScenario] = []
    for index, raw in enumerate(raw_items):
        if not isinstance(raw, Mapping):
            raise ValueError(f"Calibration scenario {index} must be an object")
        outcomes = raw.get(field, raw.get("outcomes", {}))
        if not isinstance(outcomes, Mapping):
            raise ValueError(f"Calibration scenario {index} outcomes must be an object")
        weight = float(raw.get("weight", 1.0))
        if weight < 0:
            raise ValueError(f"Calibration scenario {index} weight must be non-negative")
        parsed_outcomes = {str(key): float(value) for key, value in outcomes.items()}
        if any(value < 0 or value > 1 for value in parsed_outcomes.values()):
            raise ValueError(
                f"Calibration scenario {index} probabilities must be between 0 and 1"
            )
        scenarios.append(
            EmpiricalScenario(
                scenario_id=str(raw.get("id", f"scenario_{index}")),
                weight=weight,
                outcomes=parsed_outcomes,
            )
        )
    return tuple(scenarios)


def load_config(path: Path) -> GraftConfig:
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, Mapping):
        raise ValueError("GRAFT config must contain a JSON object")
    if int(raw.get("version", 0)) != 1:
        raise ValueError("Only GRAFT config version 1 is supported")
    failure_policy = str(raw.get("failure_policy", "open"))
    if failure_policy not in {"open", "closed"}:
        raise ValueError("failure_policy must be 'open' or 'closed'")
    checkpoint_mode = str(raw.get("checkpoint_mode", "completion"))
    if checkpoint_mode not in {"completion", "strict", "explicit"}:
        raise ValueError(
            "checkpoint_mode must be completion, strict, or explicit"
        )
    budget = float(raw.get("budget", 3.0))
    max_set_fpr = float(raw.get("max_set_fpr", 0.10))
    max_feedback_rounds = int(raw.get("max_feedback_rounds", 2))
    if budget < 0:
        raise ValueError("budget must be non-negative")
    if not 0 <= max_set_fpr <= 1:
        raise ValueError("max_set_fpr must be between 0 and 1")
    if max_feedback_rounds < 0:
        raise ValueError("max_feedback_rounds must be non-negative")
    calibration = raw.get("calibration", {})
    if not isinstance(calibration, Mapping):
        raise ValueError("calibration must be an object")
    raw_verifiers = raw.get("verifiers", [])
    if not isinstance(raw_verifiers, list):
        raise ValueError("verifiers must be an array")
    for index, item in enumerate(raw_verifiers):
        if not isinstance(item, Mapping):
            raise ValueError(f"verifier {index} must be an object")
    verifiers = tuple(_verifier(item) for item in raw_verifiers)
    verifier_ids = [item.verifier_id for item in verifiers]
    if len(verifier_ids) != len(set(verifier_ids)):
        raise ValueError("verifier ids must be unique")
    failure_scenarios = _scenarios(
        list(calibration.get("failure_scenarios", [])), "detections"
    )
    clean_scenarios = _scenarios(
        list(calibration.get("clean_scenarios", [])), "false_alarms"
    )
    unknown = {
        identifier
        for scenario in (*failure_scenarios, *clean_scenarios)
        for identifier in scenario.outcomes
        if identifier not in set(verifier_ids)
    }
    if unknown:
        raise ValueError(
            "calibration references unknown verifier ids: " + ", ".join(sorted(unknown))
        )
    return GraftConfig(
        version=1,
        enabled=bool(raw.get("enabled", True)),
        budget=budget,
        max_set_fpr=max_set_fpr,
        checkpoint_mode=checkpoint_mode,
        max_feedback_rounds=max_feedback_rounds,
        failure_policy=failure_policy,
        environment_fingerprint=str(raw.get("environment_fingerprint", "local")),
        verifiers=verifiers,
        calibration=CalibrationData(
            failure_scenarios=failure_scenarios,
            clean_scenarios=clean_scenarios,
        ),
    )
