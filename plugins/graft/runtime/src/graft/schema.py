from __future__ import annotations

from dataclasses import asdict, dataclass, field, is_dataclass
from enum import Enum
from pathlib import Path
from typing import Any, Mapping, Sequence


class Verdict(str, Enum):
    PASS = "pass"
    FAIL = "fail"
    ABSTAIN = "abstain"
    ERROR = "error"


class DecisionKind(str, Enum):
    ALLOW = "allow"
    CONTINUE_WITH_EVIDENCE = "continue_with_evidence"
    UNRESOLVED = "unresolved"
    SYSTEM_ERROR = "system_error"


class CompletionState(str, Enum):
    CANDIDATE_COMPLETE = "candidate_complete"
    INTERMEDIATE = "intermediate"
    QUESTION = "question"
    EXPLANATION = "explanation"
    BLOCKED = "blocked"
    ABSTAIN = "abstain"


class PromotionOutcome(str, Enum):
    FIXED_AND_PRESERVED = "fixed_and_preserved"
    NOT_FIXED = "not_fixed"
    REGRESSED = "regressed"
    UNRESOLVED = "unresolved"


class EvidenceRouteAvailability(str, Enum):
    AVAILABLE = "available"
    UNCERTAIN = "uncertain"
    UNAVAILABLE = "unavailable"


class EvidenceCapabilityDisposition(str, Enum):
    ELIGIBLE = "eligible"
    ADVISORY = "advisory"
    UNAVAILABLE = "unavailable"
    INVALID = "invalid"


@dataclass(frozen=True)
class StageCost:
    stage_id: str
    kind: str
    duration_s: float
    input_tokens: int | None = None
    cached_input_tokens: int | None = None
    output_tokens: int | None = None
    estimated_cost_usd: float | None = None

    @property
    def usage_known(self) -> bool:
        return any(
            value is not None
            for value in (
                self.input_tokens,
                self.cached_input_tokens,
                self.output_tokens,
                self.estimated_cost_usd,
            )
        )


@dataclass(frozen=True)
class ProducerEvidenceRecord:
    timestamp: str
    session_id: str
    turn_id: str | None
    task_epoch: int
    tool_name: str
    family: str
    outcome: str
    input_hash: str
    response_hash: str
    command_preview: str | None = None
    result_preview: str | None = None
    changed_paths: tuple[str, ...] = ()
    exit_code: int | None = None
    duration_s: float | None = None


@dataclass(frozen=True)
class ProducerEvidenceSummary:
    task_epoch: int
    event_count: int
    succeeded: int
    failed: int
    unknown: int
    total_duration_s: float | None
    command_previews: tuple[str, ...] = ()
    failure_previews: tuple[str, ...] = ()
    changed_paths: tuple[str, ...] = ()


@dataclass(frozen=True)
class CompletionAssessment:
    state: CompletionState
    confidence: float
    reason: str
    stage_cost: StageCost | None = None


@dataclass(frozen=True)
class PromotionRequirement:
    feedback_checkpoint_key: str
    report_path: str | None
    behavior_descriptions: tuple[str, ...]
    failure_descriptions: tuple[str, ...]
    evidence_observations: tuple[str, ...]
    reproduction_commands: tuple[tuple[str, ...], ...]


@dataclass(frozen=True)
class VerifierValueEstimate:
    actionability: float = 0.0
    repair_success: float = 0.0
    regression_risk: float = 1.0
    producer_evidence_overlap: float = 1.0
    confidence: float = 0.0
    predicted_duration_s: float | None = None
    predicted_model_cost_usd: float | None = None


@dataclass(frozen=True)
class PlannedEvidenceRoute:
    route_id: str
    availability: EvidenceRouteAvailability
    oracle_origin: str
    transport: str
    dependency_origins: tuple[str, ...]
    reason: str


@dataclass(frozen=True)
class EvidenceCapability:
    verifier_id: str
    routes: tuple[PlannedEvidenceRoute, ...]
    limitations: tuple[str, ...] = ()


@dataclass(frozen=True)
class EvidenceCapabilityAssessment:
    verifier_id: str
    disposition: EvidenceCapabilityDisposition
    eligible_route_ids: tuple[str, ...] = ()
    reasons: tuple[str, ...] = ()


@dataclass(frozen=True)
class EvidenceItem:
    kind: str
    observation: str
    path: str | None = None
    line: int | None = None
    command: tuple[str, ...] = ()
    failure_modes: tuple[str, ...] = ()
    requirement_refs: tuple[str, ...] = ()
    oracle_origin: str = "unspecified"


@dataclass(frozen=True)
class EvidenceAwareEvidenceItem(EvidenceItem):
    expected: str | None = None
    actual: str | None = None


@dataclass(frozen=True)
class Lineage:
    provider: str = "local"
    model: str | None = None
    thread_policy: str | None = None
    prompt_family: str | None = None
    context_sources: tuple[str, ...] = ()
    modality: tuple[str, ...] = ()
    oracle: str | None = None
    test_author: str | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def shared_sources(self, other: "Lineage") -> tuple[str, ...]:
        shared: list[str] = []
        for label, left, right in (
            ("provider", self.provider, other.provider),
            ("model", self.model, other.model),
            ("thread_policy", self.thread_policy, other.thread_policy),
            ("prompt_family", self.prompt_family, other.prompt_family),
            ("oracle", self.oracle, other.oracle),
            ("test_author", self.test_author, other.test_author),
        ):
            if left is not None and left == right:
                shared.append(f"{label}:{left}")
        shared.extend(
            f"context:{item}"
            for item in sorted(set(self.context_sources) & set(other.context_sources))
        )
        shared.extend(
            f"modality:{item}"
            for item in sorted(set(self.modality) & set(other.modality))
        )
        return tuple(shared)


@dataclass(frozen=True)
class ReproductionBundle:
    bundle_id: str
    checkpoint_key: str
    verifier_id: str
    failure_modes: tuple[str, ...]
    oracle_origin: str
    evidence_kind: str
    transport: str
    observation: str
    expected: str | None
    actual: str | None
    command: tuple[str, ...]
    artifact_path: str | None
    requirement_refs: tuple[str, ...]
    route_id: str
    dependency_origins: tuple[str, ...]
    lineage: Lineage


@dataclass(frozen=True)
class Behavior:
    behavior_id: str
    description: str
    source_refs: tuple[str, ...]
    observables: tuple[str, ...]
    severity: float
    likelihood: float
    reach: float

    @property
    def risk(self) -> float:
        return self.severity * self.likelihood * (1.0 + self.reach)


@dataclass(frozen=True)
class FailureMode:
    failure_mode_id: str
    behavior_id: str
    description: str
    category: str
    observable_signals: tuple[str, ...]
    required_capabilities: tuple[str, ...]
    risk: float


@dataclass(frozen=True)
class TaskAnalysis:
    behaviors: tuple[Behavior, ...]
    failure_modes: tuple[FailureMode, ...]
    uncertainties: tuple[str, ...] = ()


@dataclass(frozen=True)
class VerifierTemplate:
    template_id: str
    kind: str
    role: str
    instructions: str
    capabilities: tuple[str, ...]
    cost: float
    timeout_s: float = 240.0
    sandbox: str = "read-only"
    network_access: bool = False
    isolation: str = "ephemeral"
    max_instances: int = 1
    blocking: bool = True
    model: str | None = None
    command: tuple[str, ...] = ()
    failure_exit_codes: tuple[int, ...] = (1,)
    working_directory: str | None = None
    lineage: Lineage = field(default_factory=Lineage)


@dataclass(frozen=True)
class VerifierSpec:
    verifier_id: str
    kind: str
    cost: float
    blocking: bool
    failure_modes: tuple[str, ...]
    template_id: str | None = None
    objective: str = ""
    prompt: str = ""
    estimated_detection: Mapping[str, float] = field(default_factory=dict)
    timeout_s: float = 240.0
    sandbox: str = "read-only"
    network_access: bool = False
    isolation: str = "ephemeral"
    model: str | None = None
    command: tuple[str, ...] = ()
    failure_exit_codes: tuple[int, ...] = (1,)
    working_directory: str | None = None
    lineage: Lineage = field(default_factory=Lineage)
    value_estimate: VerifierValueEstimate = field(default_factory=VerifierValueEstimate)
    revalidates_feedback: bool = False


@dataclass(frozen=True)
class SharedBlindSpot:
    scenario_id: str
    description: str
    weight: float
    affected_verifiers: tuple[str, ...]
    failure_modes: tuple[str, ...]
    residual_detection: float
    sources: tuple[str, ...] = ()


@dataclass(frozen=True)
class FeedbackGraph:
    source_hash: str
    behaviors: tuple[Behavior, ...]
    failure_modes: tuple[FailureMode, ...]
    verifiers: tuple[VerifierSpec, ...]
    shared_blind_spots: tuple[SharedBlindSpot, ...]
    uncertainties: tuple[str, ...] = ()
    producer_evidence: ProducerEvidenceSummary | None = None
    stage_costs: tuple[StageCost, ...] = ()
    promotion: PromotionRequirement | None = None
    method: str = "graft-original"


@dataclass(frozen=True)
class EvidenceAwareFeedbackGraph(FeedbackGraph):
    evidence_capabilities: tuple[EvidenceCapability, ...] = ()
    evidence_capability_assessments: tuple[EvidenceCapabilityAssessment, ...] = ()


@dataclass(frozen=True)
class SourceSnapshot:
    root: str
    tree_hash: str
    requirement_hash: str
    config_hash: str
    checkpoint_key: str
    files: tuple[str, ...]
    created_at: str
    baseline_tree_hash: str | None = None
    baseline_files: tuple[str, ...] = ()
    file_hashes: Mapping[str, str] = field(default_factory=dict)
    baseline_file_hashes: Mapping[str, str] = field(default_factory=dict)
    baseline_archive_path: str | None = None


@dataclass(frozen=True)
class Selection:
    verifier_ids: tuple[str, ...]
    expected_utility: float
    expected_coverage: float
    residual_risk: float
    total_cost: float
    feasible: bool
    evaluated_candidates: int
    policy: str = "original"
    net_value: float = 0.0
    no_op: bool = False
    marginal_values: Mapping[str, float] = field(default_factory=dict)


@dataclass(frozen=True)
class VerifierResult:
    verifier_id: str
    verdict: Verdict
    summary: str
    source_hash: str
    blocking: bool
    reproducible: bool
    duration_s: float
    failure_modes: tuple[str, ...] = ()
    evidence: tuple[EvidenceItem, ...] = ()
    command: tuple[str, ...] = ()
    stdout: str = ""
    stderr: str = ""
    exit_code: int | None = None
    error: str | None = None
    confidence: float = 0.0
    lineage: Lineage = field(default_factory=Lineage)
    usage: Mapping[str, Any] = field(default_factory=dict)
    estimated_cost_usd: float | None = None
    executed_evidence: bool = False
    promotion_outcome: PromotionOutcome | None = None


@dataclass(frozen=True)
class EvidenceAwareVerifierResult(VerifierResult):
    reproduction_bundles: tuple[ReproductionBundle, ...] = ()


@dataclass(frozen=True)
class Decision:
    kind: DecisionKind
    reason: str
    snapshot: SourceSnapshot
    graph: FeedbackGraph | None = None
    selection: Selection | None = None
    results: tuple[VerifierResult, ...] = ()
    report_path: str | None = None
    promotion_outcome: PromotionOutcome | None = None


@dataclass(frozen=True)
class TurnResult:
    thread_id: str | None
    final_response: str
    events: tuple[Mapping[str, Any], ...]
    usage: Mapping[str, Any]
    return_code: int
    stderr: str
    duration_s: float


@dataclass(frozen=True)
class RunConfig:
    sandbox: str = "workspace-write"
    network_access: bool = False
    model: str | None = None
    timeout_s: float = 900.0
    ephemeral: bool = False
    output_schema: Path | None = None
    isolate_config: bool = False
    disable_hooks: bool = False
    skip_git_repo_check: bool = False


def to_jsonable(value: Any) -> Any:
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, Path):
        return str(value)
    if is_dataclass(value):
        return {key: to_jsonable(item) for key, item in asdict(value).items()}
    if isinstance(value, Mapping):
        return {str(key): to_jsonable(item) for key, item in value.items()}
    if isinstance(value, (tuple, list, set)):
        return [to_jsonable(item) for item in value]
    return value


def tuple_of_strings(value: Sequence[str] | None) -> tuple[str, ...]:
    return tuple(str(item) for item in (value or ()))
