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


@dataclass(frozen=True)
class VerifierSpec:
    verifier_id: str
    kind: str
    cost: float
    blocking: bool
    failure_modes: tuple[str, ...]
    timeout_s: float = 120.0
    command: tuple[str, ...] = ()
    failure_exit_codes: tuple[int, ...] = (1,)
    working_directory: str | None = None
    prompt_template: str | None = None
    model: str | None = None
    sandbox: str = "read-only"
    lineage: Lineage = field(default_factory=Lineage)


@dataclass(frozen=True)
class EmpiricalScenario:
    scenario_id: str
    weight: float
    outcomes: Mapping[str, float]


@dataclass(frozen=True)
class CalibrationData:
    failure_scenarios: tuple[EmpiricalScenario, ...] = ()
    clean_scenarios: tuple[EmpiricalScenario, ...] = ()


@dataclass(frozen=True)
class SourceSnapshot:
    root: str
    tree_hash: str
    requirement_hash: str
    config_hash: str
    checkpoint_key: str
    files: tuple[str, ...]
    created_at: str


@dataclass(frozen=True)
class Selection:
    verifier_ids: tuple[str, ...]
    expected_coverage: float
    expected_false_alarm: float
    total_cost: float
    feasible: bool
    evaluated_subsets: int


@dataclass(frozen=True)
class VerifierResult:
    verifier_id: str
    verdict: Verdict
    summary: str
    source_hash: str
    blocking: bool
    reproducible: bool
    duration_s: float
    evidence: tuple[EvidenceItem, ...] = ()
    command: tuple[str, ...] = ()
    stdout: str = ""
    stderr: str = ""
    exit_code: int | None = None
    error: str | None = None
    lineage: Lineage = field(default_factory=Lineage)


@dataclass(frozen=True)
class Decision:
    kind: DecisionKind
    reason: str
    snapshot: SourceSnapshot
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
    model: str | None = None
    timeout_s: float = 900.0
    ephemeral: bool = False
    output_schema: Path | None = None
    isolate_config: bool = False
    disable_hooks: bool = False


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
