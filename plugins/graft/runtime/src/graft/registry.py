from __future__ import annotations

import json
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from graft.schema import Lineage, VerifierTemplate, tuple_of_strings


ORIGINAL_METHOD_ID = "graft-original"
VALUE_AWARE_METHOD_ID = "graft-value-aware"


@dataclass(frozen=True)
class ModelStageConfig:
    model: str | None
    timeout_s: float
    prompt_family: str


@dataclass(frozen=True)
class CompletionGateConfig:
    stage: ModelStageConfig
    min_confidence: float


@dataclass(frozen=True)
class SelectionPolicy:
    algorithm: str
    max_verifiers: int
    min_marginal_gain_per_cost: float
    residual_risk_threshold: float
    strategy: str = "original"
    min_net_value: float = 0.0
    uncertainty_penalty: float = 0.25
    repair_value: float = 1.0
    regression_cost: float = 1.0
    wall_time_budget_s: float = 120.0
    model_cost_budget_usd: float = 1.0
    wall_time_weight: float = 0.25
    model_cost_weight: float = 0.25
    nominal_cost_weight: float = 0.10


@dataclass(frozen=True)
class GraftConfig:
    version: int
    method: str
    enabled: bool
    budget: float
    checkpoint_mode: str
    max_feedback_rounds: int
    failure_policy: str
    environment_fingerprint: str
    behavior_modeler: ModelStageConfig
    verifier_planner: ModelStageConfig
    completion_gate: CompletionGateConfig | None
    verifier_templates: tuple[VerifierTemplate, ...]
    selection: SelectionPolicy


def _optional_string(value: Any) -> str | None:
    return None if value is None else str(value)


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


def _model_stage(raw: Mapping[str, Any], *, label: str) -> ModelStageConfig:
    timeout_s = float(raw.get("timeout_s", 240.0))
    if timeout_s <= 0:
        raise ValueError(f"{label}.timeout_s must be positive")
    prompt_family = str(raw.get("prompt_family", "")).strip()
    if not prompt_family:
        raise ValueError(f"{label}.prompt_family is required")
    return ModelStageConfig(
        model=_optional_string(raw.get("model")),
        timeout_s=timeout_s,
        prompt_family=prompt_family,
    )


def _template(raw: Mapping[str, Any]) -> VerifierTemplate:
    template_id = str(raw.get("id", "")).strip()
    if not template_id:
        raise ValueError("verifier template id is required")
    kind = str(raw.get("kind", ""))
    if kind not in {"codex_review", "codex_agent", "command"}:
        raise ValueError(f"Unsupported verifier template kind for {template_id}: {kind}")
    cost = float(raw.get("cost", 1.0))
    timeout_s = float(raw.get("timeout_s", 240.0))
    max_instances = int(raw.get("max_instances", 1))
    sandbox = str(raw.get("sandbox", "read-only"))
    network_access = bool(raw.get("network_access", False))
    isolation = str(raw.get("isolation", "ephemeral"))
    command = tuple_of_strings(raw.get("command"))
    if cost < 0:
        raise ValueError(f"Verifier template {template_id} cost must be non-negative")
    if timeout_s <= 0 or max_instances <= 0:
        raise ValueError(
            f"Verifier template {template_id} timeout and max_instances must be positive"
        )
    if sandbox not in {"read-only", "workspace-write"}:
        raise ValueError(
            f"Verifier template {template_id} sandbox must be read-only or workspace-write"
        )
    if isolation not in {"ephemeral", "temporary-copy"}:
        raise ValueError(
            f"Verifier template {template_id} isolation must be ephemeral or temporary-copy"
        )
    if sandbox == "workspace-write" and isolation != "temporary-copy":
        raise ValueError(
            f"Writable verifier template {template_id} must use temporary-copy isolation"
        )
    if kind == "command" and not command:
        raise ValueError(f"Command verifier template {template_id} requires command")
    role = str(raw.get("role", "")).strip()
    instructions = str(raw.get("instructions", "")).strip()
    if not role or not instructions:
        raise ValueError(f"Verifier template {template_id} requires role and instructions")
    return VerifierTemplate(
        template_id=template_id,
        kind=kind,
        role=role,
        instructions=instructions,
        capabilities=tuple_of_strings(raw.get("capabilities")),
        cost=cost,
        timeout_s=timeout_s,
        sandbox=sandbox,
        network_access=network_access,
        isolation=isolation,
        max_instances=max_instances,
        blocking=bool(raw.get("blocking", True)),
        model=_optional_string(raw.get("model")),
        command=command,
        failure_exit_codes=tuple(
            int(item) for item in raw.get("failure_exit_codes", [1])
        ),
        working_directory=_optional_string(raw.get("working_directory")),
        lineage=_lineage(raw.get("lineage")),
    )


def load_config(path: Path) -> GraftConfig:
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, Mapping):
        raise ValueError("GRAFT config must contain a JSON object")
    version = int(raw.get("version", 0))
    if version != 2:
        raise ValueError(
            "GRAFT requires config version 2; version 1 empirical-fixture "
            "configs are historical experiment artifacts"
        )
    method = str(raw.get("method", ""))
    if method not in {ORIGINAL_METHOD_ID, VALUE_AWARE_METHOD_ID}:
        raise ValueError(f"Unsupported GRAFT method: {method or '<missing>'}")
    failure_policy = str(raw.get("failure_policy", "open"))
    if failure_policy not in {"open", "closed"}:
        raise ValueError("failure_policy must be 'open' or 'closed'")
    checkpoint_mode = str(raw.get("checkpoint_mode", "completion"))
    if checkpoint_mode not in {"completion", "strict", "explicit"}:
        raise ValueError("checkpoint_mode must be completion, strict, or explicit")
    budget = float(raw.get("budget", 4.0))
    max_feedback_rounds = int(raw.get("max_feedback_rounds", 2))
    if budget < 0 or max_feedback_rounds < 0:
        raise ValueError("budget and max_feedback_rounds must be non-negative")

    modeling = raw.get("modeling", {})
    if not isinstance(modeling, Mapping):
        raise ValueError("modeling must be an object")
    behavior_raw = modeling.get("behavior_modeler", {})
    planner_raw = modeling.get("verifier_planner", {})
    if not isinstance(behavior_raw, Mapping) or not isinstance(planner_raw, Mapping):
        raise ValueError("behavior_modeler and verifier_planner must be objects")
    completion_gate: CompletionGateConfig | None = None
    completion_raw = modeling.get("completion_gate")
    if completion_raw is not None:
        if not isinstance(completion_raw, Mapping):
            raise ValueError("completion_gate must be an object")
        minimum_confidence = float(completion_raw.get("min_confidence", 0.80))
        if not 0 <= minimum_confidence <= 1:
            raise ValueError("completion_gate.min_confidence must be in [0,1]")
        completion_gate = CompletionGateConfig(
            stage=_model_stage(completion_raw, label="completion_gate"),
            min_confidence=minimum_confidence,
        )

    raw_templates = raw.get("verifier_templates", [])
    if not isinstance(raw_templates, list):
        raise ValueError("verifier_templates must be an array")
    templates = tuple(_template(item) for item in raw_templates if isinstance(item, Mapping))
    if len(templates) != len(raw_templates):
        raise ValueError("every verifier template must be an object")
    template_ids = [item.template_id for item in templates]
    if len(template_ids) != len(set(template_ids)):
        raise ValueError("verifier template ids must be unique")
    if bool(raw.get("enabled", True)) and not templates:
        raise ValueError("enabled GRAFT configurations need verifier templates")

    selection_raw = raw.get("selection", {})
    if not isinstance(selection_raw, Mapping):
        raise ValueError("selection must be an object")
    strategy = str(
        selection_raw.get(
            "strategy",
            "value-aware" if method == VALUE_AWARE_METHOD_ID else "original",
        )
    )
    if strategy not in {"original", "value-aware"}:
        raise ValueError("selection.strategy must be original or value-aware")
    algorithm = str(
        selection_raw.get(
            "algorithm",
            "value-aware-hypergraph"
            if strategy == "value-aware"
            else "lazy-greedy-hypergraph",
        )
    )
    expected_algorithm = (
        "value-aware-hypergraph"
        if strategy == "value-aware"
        else "lazy-greedy-hypergraph"
    )
    if algorithm != expected_algorithm:
        raise ValueError(
            f"selection algorithm {algorithm} does not match strategy {strategy}"
        )
    if method == ORIGINAL_METHOD_ID and strategy != "original":
        raise ValueError("graft-original must use the original selection strategy")
    if method == VALUE_AWARE_METHOD_ID and strategy != "value-aware":
        raise ValueError("graft-value-aware must use the value-aware selection strategy")
    if strategy == "value-aware" and completion_gate is None:
        raise ValueError("graft-value-aware requires modeling.completion_gate")
    max_verifiers = int(selection_raw.get("max_verifiers", 4))
    minimum = float(selection_raw.get("min_marginal_gain_per_cost", 0.01))
    residual_threshold = float(selection_raw.get("residual_risk_threshold", 0.20))
    min_net_value = float(selection_raw.get("min_net_value", 0.0))
    uncertainty_penalty = float(selection_raw.get("uncertainty_penalty", 0.25))
    repair_value = float(selection_raw.get("repair_value", 1.0))
    regression_cost = float(selection_raw.get("regression_cost", 1.0))
    wall_time_budget_s = float(selection_raw.get("wall_time_budget_s", 120.0))
    model_cost_budget_usd = float(selection_raw.get("model_cost_budget_usd", 1.0))
    wall_time_weight = float(selection_raw.get("wall_time_weight", 0.25))
    model_cost_weight = float(selection_raw.get("model_cost_weight", 0.25))
    nominal_cost_weight = float(selection_raw.get("nominal_cost_weight", 0.10))
    if max_verifiers <= 0 or minimum < 0 or not 0 <= residual_threshold <= 1:
        raise ValueError("invalid selection thresholds")
    if (
        min_net_value < 0
        or uncertainty_penalty < 0
        or repair_value < 0
        or regression_cost < 0
        or wall_time_budget_s <= 0
        or model_cost_budget_usd <= 0
        or wall_time_weight < 0
        or model_cost_weight < 0
        or nominal_cost_weight < 0
    ):
        raise ValueError("invalid value-aware selection parameters")

    return GraftConfig(
        version=2,
        method=method,
        enabled=bool(raw.get("enabled", True)),
        budget=budget,
        checkpoint_mode=checkpoint_mode,
        max_feedback_rounds=max_feedback_rounds,
        failure_policy=failure_policy,
        environment_fingerprint=str(
            raw.get("environment_fingerprint", "graft-original-dynamic-v1")
        ),
        behavior_modeler=_model_stage(behavior_raw, label="behavior_modeler"),
        verifier_planner=_model_stage(planner_raw, label="verifier_planner"),
        completion_gate=completion_gate,
        verifier_templates=templates,
        selection=SelectionPolicy(
            algorithm=algorithm,
            max_verifiers=max_verifiers,
            min_marginal_gain_per_cost=minimum,
            residual_risk_threshold=residual_threshold,
            strategy=strategy,
            min_net_value=min_net_value,
            uncertainty_penalty=uncertainty_penalty,
            repair_value=repair_value,
            regression_cost=regression_cost,
            wall_time_budget_s=wall_time_budget_s,
            model_cost_budget_usd=model_cost_budget_usd,
            wall_time_weight=wall_time_weight,
            model_cost_weight=model_cost_weight,
            nominal_cost_weight=nominal_cost_weight,
        ),
    )


def default_original_config_payload(*, enabled: bool = True) -> dict[str, Any]:
    """Return the domain-neutral GRAFT Original registry.

    These are general verifier capabilities. No language, framework, repository layout or
    task-specific failure is encoded here; the LLM modeler and planner instantiate them at each
    checkpoint.
    """

    return {
        "version": 2,
        "method": ORIGINAL_METHOD_ID,
        "enabled": enabled,
        "budget": 4.0 if enabled else 0.0,
        "checkpoint_mode": "completion",
        "max_feedback_rounds": 2,
        "failure_policy": "open",
        "environment_fingerprint": "graft-original-dynamic-v3-grounded-runtime",
        "modeling": {
            "behavior_modeler": {
                "model": None,
                "timeout_s": 180,
                "prompt_family": "graft-original-behavior-v1",
            },
            "verifier_planner": {
                "model": None,
                "timeout_s": 120,
                "prompt_family": "graft-original-verifier-planner-v1",
            },
        },
        "verifier_templates": (
            [
                {
                    "id": "repository-evidence-agent",
                    "kind": "codex_agent",
                    "role": "repository-declared deterministic evidence agent",
                    "instructions": (
                        "Inspect repository-owned documentation and tool configuration to derive "
                        "a task-relevant direct-argv command. Run it only inside the disposable "
                        "workspace copy and report the observed deterministic result."
                    ),
                    "capabilities": [
                        "repository-tool-discovery",
                        "deterministic-execution",
                        "runtime-evidence",
                    ],
                    "cost": 1.25,
                    "timeout_s": 180,
                    "sandbox": "workspace-write",
                    "network_access": False,
                    "isolation": "temporary-copy",
                    "max_instances": 2,
                    "blocking": True,
                    "model": None,
                    "lineage": {
                        "provider": "openai-codex",
                        "thread_policy": "fresh-ephemeral-copy",
                        "prompt_family": "repository-evidence-agent-v1",
                        "context_sources": [
                            "raw-requirements",
                            "workspace-copy",
                            "repository-tool-declarations",
                        ],
                        "modality": ["text", "source", "execution"],
                        "oracle": "repository-declared-command",
                    },
                },
                {
                    "id": "semantic-reviewer",
                    "kind": "codex_review",
                    "role": "independent semantic requirements reviewer",
                    "instructions": (
                        "Inspect the raw requirements, observable repository state and candidate "
                        "result. Challenge requirement omissions and semantic mismatches."
                    ),
                    "capabilities": ["requirements", "source", "diff", "semantic-review"],
                    "cost": 1.0,
                    "timeout_s": 120,
                    "sandbox": "read-only",
                    "network_access": False,
                    "isolation": "ephemeral",
                    "max_instances": 2,
                    "blocking": False,
                    "model": None,
                    "lineage": {
                        "provider": "openai-codex",
                        "thread_policy": "fresh-ephemeral",
                        "prompt_family": "semantic-reviewer-v1",
                        "context_sources": ["raw-requirements", "workspace", "diff"],
                        "modality": ["text", "source"],
                        "oracle": "model-review",
                    },
                },
                {
                    "id": "agentic-evidence-reviewer",
                    "kind": "codex_agent",
                    "role": "agentic environment and execution reviewer",
                    "instructions": (
                        "Inspect the task and exercise relevant behavior inside the disposable "
                        "workspace copy. Prefer authoritative runtime or unchanged baseline "
                        "repository evidence over code style or generated mocks."
                    ),
                    "capabilities": ["repository-search", "tool-execution", "runtime-evidence"],
                    "cost": 1.5,
                    "timeout_s": 180,
                    "sandbox": "workspace-write",
                    "network_access": False,
                    "isolation": "temporary-copy",
                    "max_instances": 2,
                    "blocking": True,
                    "model": None,
                    "lineage": {
                        "provider": "openai-codex",
                        "thread_policy": "fresh-ephemeral",
                        "prompt_family": "agentic-evidence-reviewer-v1",
                        "context_sources": ["raw-requirements", "workspace", "runtime"],
                        "modality": ["text", "source", "execution"],
                        "oracle": "agentic-observation",
                    },
                },
                {
                    "id": "test-agent",
                    "kind": "codex_agent",
                    "role": "task-specific adversarial test agent",
                    "instructions": (
                        "Work only in the disposable workspace copy. Derive task-specific checks "
                        "from numbered raw requirements and targeted failure modes, execute them "
                        "against the actual candidate rather than a substitute mock, and report "
                        "concrete requirement-grounded counterexamples without changing the "
                        "producer workspace."
                    ),
                    "capabilities": ["test-generation", "tool-execution", "adversarial-cases"],
                    "cost": 2.0,
                    "timeout_s": 240,
                    "sandbox": "workspace-write",
                    "network_access": False,
                    "isolation": "temporary-copy",
                    "max_instances": 1,
                    "blocking": True,
                    "model": None,
                    "lineage": {
                        "provider": "openai-codex",
                        "thread_policy": "fresh-ephemeral-copy",
                        "prompt_family": "test-agent-v1",
                        "context_sources": ["raw-requirements", "workspace-copy", "generated-tests"],
                        "modality": ["text", "source", "execution"],
                        "oracle": "generated-executable-check",
                        "test_author": "independent-codex-test-agent",
                    },
                },
            ]
            if enabled
            else []
        ),
        "selection": {
            "strategy": "original",
            "algorithm": "lazy-greedy-hypergraph",
            "max_verifiers": 4,
            "min_marginal_gain_per_cost": 0.01,
            "residual_risk_threshold": 0.20,
        },
        "_method_contract": "docs/method-original-frozen.md",
    }


def default_value_aware_config_payload(*, enabled: bool = True) -> dict[str, Any]:
    """Return the domain-neutral value-aware policy without task-specific routes."""

    payload = deepcopy(default_original_config_payload(enabled=enabled))
    payload["method"] = VALUE_AWARE_METHOD_ID
    payload["environment_fingerprint"] = "graft-value-aware-v1"
    payload["modeling"]["completion_gate"] = {
        "model": None,
        "timeout_s": 45,
        "prompt_family": "graft-completion-gate-v1",
        "min_confidence": 0.80,
    }
    payload["selection"] = {
        "strategy": "value-aware",
        "algorithm": "value-aware-hypergraph",
        "max_verifiers": 4,
        "min_marginal_gain_per_cost": 0.0,
        "residual_risk_threshold": 0.20,
        "min_net_value": 0.0,
        "uncertainty_penalty": 0.25,
        "repair_value": 1.0,
        "regression_cost": 1.0,
        "wall_time_budget_s": 120.0,
        "model_cost_budget_usd": 1.0,
        "wall_time_weight": 0.25,
        "model_cost_weight": 0.25,
        "nominal_cost_weight": 0.10,
    }
    payload["_method_contract"] = "docs/graft-core-definition-zh.md"
    return payload
