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


@dataclass(frozen=True)
class EvidenceItem:
    kind: str
    observation: str
    path: str | None = None
    line: int | None = None
    command: tuple[str, ...] = ()
    failure_modes: tuple[str, ...] = ()
    oracle_origin: str = "unspecified"


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


@dataclass(frozen=True)
class Selection:
    verifier_ids: tuple[str, ...]
    expected_utility: float
    expected_coverage: float
    residual_risk: float
    total_cost: float
    feasible: bool
    evaluated_candidates: int


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


@dataclass(frozen=True)
class Decision:
    kind: DecisionKind
    reason: str
    snapshot: SourceSnapshot
    graph: FeedbackGraph | None = None
    selection: Selection | None = None
    results: tuple[VerifierResult, ...] = ()
    report_path: str | None = None


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
