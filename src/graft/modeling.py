from __future__ import annotations

import json
from dataclasses import replace
from importlib.resources import files
from pathlib import Path
from typing import Any, Mapping, Protocol

from graft.codex.cli_runner import CliCodexRunner, CodexExecutionError
from graft.evidence.snapshot import freeze_source
from graft.registry import GraftConfig, ModelStageConfig
from graft.schema import (
    Behavior,
    FailureMode,
    FeedbackGraph,
    Lineage,
    RunConfig,
    SharedBlindSpot,
    SourceSnapshot,
    TaskAnalysis,
    VerifierSpec,
    VerifierTemplate,
    to_jsonable,
    tuple_of_strings,
)


class FeedbackGraphBuildError(RuntimeError):
    pass


class FeedbackGraphBuilder(Protocol):
    def build(
        self,
        snapshot: SourceSnapshot,
        requirements: tuple[str, ...],
        config: GraftConfig,
        *,
        config_path: Path,
    ) -> FeedbackGraph: ...


class CodexFeedbackGraphBuilder:
    """Build the DOCX Behavior–Failure–Verifier–Lineage graph with structured LLM calls."""

    def __init__(self, *, codex_runner: CliCodexRunner | None = None) -> None:
        self.codex_runner = codex_runner or CliCodexRunner()

    def build(
        self,
        snapshot: SourceSnapshot,
        requirements: tuple[str, ...],
        config: GraftConfig,
        *,
        config_path: Path,
    ) -> FeedbackGraph:
        analysis_raw = self._run_structured(
            _behavior_prompt(snapshot, requirements),
            snapshot,
            requirements,
            config_path,
            config,
            stage=config.behavior_modeler,
            schema_name="task_analysis.schema.json",
        )
        analysis = _parse_task_analysis(analysis_raw)

        plan_raw = self._run_structured(
            _planner_prompt(snapshot, requirements, analysis, config.verifier_templates),
            snapshot,
            requirements,
            config_path,
            config,
            stage=config.verifier_planner,
            schema_name="verifier_plan.schema.json",
        )
        verifiers, blind_spots, gaps = _parse_verifier_plan(
            plan_raw, analysis, config.verifier_templates
        )
        uncertainties = tuple(
            dict.fromkeys(
                (
                    *(f"analysis_uncertainty: {item}" for item in analysis.uncertainties),
                    *(f"coverage_gap: {item}" for item in gaps),
                )
            )
        )
        if len(verifiers) > 1 and not blind_spots:
            shared = _all_shared_sources(verifiers)
            if shared:
                raise FeedbackGraphBuildError(
                    "verifier planner omitted the required high-order blind-spot model for "
                    f"shared lineage: {', '.join(shared)}"
                )
        return FeedbackGraph(
            source_hash=snapshot.checkpoint_key,
            behaviors=analysis.behaviors,
            failure_modes=analysis.failure_modes,
            verifiers=verifiers,
            shared_blind_spots=blind_spots,
            uncertainties=uncertainties,
        )

    def _run_structured(
        self,
        prompt: str,
        snapshot: SourceSnapshot,
        requirements: tuple[str, ...],
        config_path: Path,
        config: GraftConfig,
        *,
        stage: ModelStageConfig,
        schema_name: str,
    ) -> Mapping[str, Any]:
        schema = Path(str(files("graft").joinpath("resources", schema_name)))
        try:
            turn = self.codex_runner.start_thread(
                prompt,
                Path(snapshot.root),
                RunConfig(
                    sandbox="read-only",
                    model=stage.model,
                    timeout_s=stage.timeout_s,
                    ephemeral=True,
                    output_schema=schema,
                    isolate_config=True,
                    disable_hooks=True,
                    skip_git_repo_check=True,
                ),
            )
        except CodexExecutionError as exc:
            raise FeedbackGraphBuildError(str(exc)) from exc
        if turn.return_code != 0:
            raise FeedbackGraphBuildError(
                f"{stage.prompt_family} exited with {turn.return_code}: "
                f"{_turn_error(turn.events) or turn.stderr.strip() or 'unknown error'}"
            )
        try:
            raw = json.loads(turn.final_response)
        except json.JSONDecodeError as exc:
            raise FeedbackGraphBuildError(
                f"{stage.prompt_family} returned invalid JSON: {exc}"
            ) from exc
        if not isinstance(raw, Mapping):
            raise FeedbackGraphBuildError(
                f"{stage.prompt_family} returned a non-object result"
            )
        after = freeze_source(
            Path(snapshot.root),
            requirements=requirements,
            config_path=config_path,
            environment_fingerprint=config.environment_fingerprint,
        )
        if after.tree_hash != snapshot.tree_hash:
            raise FeedbackGraphBuildError(
                f"Read-only model stage {stage.prompt_family} changed the producer workspace"
            )
        return raw


def _behavior_prompt(
    snapshot: SourceSnapshot, requirements: tuple[str, ...]
) -> str:
    requirements_text = "\n".join(f"- {item}" for item in requirements) or "- <missing>"
    changed = _candidate_changed_files(snapshot)
    changed_text = (
        "\n".join(f"- {item}" for item in changed)
        if changed is not None
        else "- <baseline manifest unavailable>"
    )
    if changed == ():
        changed_text = "- <none>"
    return f"""You are the structured Behavior and Failure-Mode constructor for GRAFT Original.

Model the current task, not a generic programming checklist. Read the raw user requirements below,
inspect the observable repository state and current working-tree changes, and identify what must be
true for this particular result to be correct. Do not trust or reconstruct the producer agent's
private reasoning. Raw user requirements are the authoritative task contract. Baseline repository
tests, documentation, schemas, and rules may clarify that contract, but candidate-added or
candidate-modified files are implementation evidence only: they must never introduce a new
requirement, precondition, protocol invariant, or oracle. In particular, do not turn an
implementation assumption into a Behavior by demanding stricter input ordering or lifecycle
semantics than the raw task or baseline repository establishes. Candidate source and diff may
suggest Failure Modes, but every Behavior must remain traceable to the raw requirements or baseline
contract. When the contract is ambiguous, record an uncertainty instead of choosing the stricter
interpretation. If an important behavior cannot be verified with available repository state,
record that uncertainty explicitly.

Raw requirements:
{requirements_text}

GRAFT content checkpoint hash (not a Git commit or ref): {snapshot.checkpoint_key}
Repository root: {snapshot.root}
Visible source entries: {len(snapshot.files)}
Baseline tree hash: {snapshot.baseline_tree_hash or '<unavailable>'}
Files added or modified after the task baseline (non-authoritative as contract sources):
{changed_text}

Use stable short identifiers. Scores are risk factors in [0,1]; Failure Mode risk may be in [0,2].
Return only the schema-conforming object.
"""


def _candidate_changed_files(snapshot: SourceSnapshot) -> tuple[str, ...] | None:
    if not snapshot.baseline_tree_hash or not snapshot.baseline_file_hashes:
        return None
    baseline = snapshot.baseline_file_hashes
    changed = {
        path
        for path, digest in snapshot.file_hashes.items()
        if baseline.get(path) != digest
    }
    changed.update(
        f"{path} (deleted)" for path in baseline if path not in snapshot.file_hashes
    )
    return tuple(sorted(changed))


def _planner_prompt(
    snapshot: SourceSnapshot,
    requirements: tuple[str, ...],
    analysis: TaskAnalysis,
    templates: tuple[VerifierTemplate, ...],
) -> str:
    requirements_text = "\n".join(f"- {item}" for item in requirements) or "- <missing>"
    template_payload = [
        {
            "id": item.template_id,
            "kind": item.kind,
            "role": item.role,
            "instructions": item.instructions,
            "capabilities": list(item.capabilities),
            "max_instances": item.max_instances,
            "lineage": to_jsonable(item.lineage),
        }
        for item in templates
    ]
    return f"""You are the verifier-registry retriever for GRAFT Original.

Create a rich task-specific candidate pool by instantiating only the general verifier templates
provided below. Do not choose the final budgeted subset; GRAFT will do that. Each candidate must
target concrete Failure Mode identifiers and receive a task-specific objective and prompt. Estimate
detection probabilities conservatively. Explicitly identify higher-order shared blind spots caused
by shared model, prompt, context, modality, test author, oracle, or task interpretation. Different
candidate names or fresh threads are not evidence of independence. Report capability gaps instead
of inventing unavailable tools. If two or more candidates share any material lineage source, the
plan MUST contain at least one shared_blind_spots entry covering the affected group; a zero-edge
plan with shared model/context lineage is invalid. The repository-evidence agent discovers
applicable project-owned commands later inside its sandboxed disposable copy; the planner must not
emit commands or task-specific hardcoded fixtures.

Raw requirements:
{requirements_text}

GRAFT content checkpoint hash (not a Git commit or ref): {snapshot.checkpoint_key}

Task model:
{json.dumps(to_jsonable(analysis), ensure_ascii=False, indent=2)}

General verifier templates:
{json.dumps(template_payload, ensure_ascii=False, indent=2)}

Return only the schema-conforming object.
"""


def _parse_task_analysis(raw: Mapping[str, Any]) -> TaskAnalysis:
    behaviors: list[Behavior] = []
    behavior_ids: set[str] = set()
    for item in raw.get("behaviors", []):
        if not isinstance(item, Mapping):
            raise FeedbackGraphBuildError("behavior entry is not an object")
        identifier = str(item["id"])
        if identifier in behavior_ids:
            raise FeedbackGraphBuildError(f"duplicate behavior id: {identifier}")
        behavior_ids.add(identifier)
        behaviors.append(
            Behavior(
                behavior_id=identifier,
                description=str(item["description"]),
                source_refs=tuple_of_strings(item.get("source_refs")),
                observables=tuple_of_strings(item.get("observables")),
                severity=_probability(item["severity"], f"{identifier}.severity"),
                likelihood=_probability(item["likelihood"], f"{identifier}.likelihood"),
                reach=_probability(item["reach"], f"{identifier}.reach"),
            )
        )
    if not behaviors:
        raise FeedbackGraphBuildError("task model contains no behaviors")

    failures: list[FailureMode] = []
    failure_ids: set[str] = set()
    for item in raw.get("failure_modes", []):
        if not isinstance(item, Mapping):
            raise FeedbackGraphBuildError("failure-mode entry is not an object")
        identifier = str(item["id"])
        behavior_id = str(item["behavior_id"])
        if identifier in failure_ids:
            raise FeedbackGraphBuildError(f"duplicate failure-mode id: {identifier}")
        if behavior_id not in behavior_ids:
            raise FeedbackGraphBuildError(
                f"failure mode {identifier} references unknown behavior {behavior_id}"
            )
        failure_ids.add(identifier)
        risk = float(item["risk"])
        if not 0 <= risk <= 2:
            raise FeedbackGraphBuildError(f"{identifier}.risk is outside [0,2]")
        failures.append(
            FailureMode(
                failure_mode_id=identifier,
                behavior_id=behavior_id,
                description=str(item["description"]),
                category=str(item["category"]),
                observable_signals=tuple_of_strings(item.get("observable_signals")),
                required_capabilities=tuple_of_strings(
                    item.get("required_capabilities")
                ),
                risk=risk,
            )
        )
    if not failures:
        raise FeedbackGraphBuildError("task model contains no failure modes")
    return TaskAnalysis(
        behaviors=tuple(behaviors),
        failure_modes=tuple(failures),
        uncertainties=tuple_of_strings(raw.get("uncertainties")),
    )


def _parse_verifier_plan(
    raw: Mapping[str, Any],
    analysis: TaskAnalysis,
    templates: tuple[VerifierTemplate, ...],
) -> tuple[tuple[VerifierSpec, ...], tuple[SharedBlindSpot, ...], tuple[str, ...]]:
    by_template = {item.template_id: item for item in templates}
    failure_ids = {item.failure_mode_id for item in analysis.failure_modes}
    counts: dict[str, int] = {}
    verifiers: list[VerifierSpec] = []
    verifier_ids: set[str] = set()
    for item in raw.get("candidates", []):
        if not isinstance(item, Mapping):
            raise FeedbackGraphBuildError("verifier candidate is not an object")
        identifier = str(item["id"])
        template_id = str(item["template_id"])
        if identifier in verifier_ids:
            raise FeedbackGraphBuildError(f"duplicate verifier id: {identifier}")
        template = by_template.get(template_id)
        if template is None:
            raise FeedbackGraphBuildError(
                f"verifier {identifier} references unknown template {template_id}"
            )
        counts[template_id] = counts.get(template_id, 0) + 1
        if counts[template_id] > template.max_instances:
            raise FeedbackGraphBuildError(
                f"verifier template {template_id} exceeds max_instances"
            )
        targets = tuple_of_strings(item.get("target_failure_modes"))
        unknown_targets = sorted(set(targets) - failure_ids)
        if not targets or unknown_targets:
            raise FeedbackGraphBuildError(
                f"verifier {identifier} has invalid failure targets: {unknown_targets}"
            )
        raw_detection = item.get("estimated_detection", [])
        if not isinstance(raw_detection, list):
            raise FeedbackGraphBuildError(
                f"verifier {identifier} estimated_detection must be an array"
            )
        detection: dict[str, float] = {}
        for estimate in raw_detection:
            if not isinstance(estimate, Mapping):
                raise FeedbackGraphBuildError(
                    f"verifier {identifier} detection estimate is not an object"
                )
            failure_id = str(estimate["failure_mode_id"])
            if failure_id in detection:
                raise FeedbackGraphBuildError(
                    f"verifier {identifier} repeats detection estimate for {failure_id}"
                )
            detection[failure_id] = _probability(
                estimate["probability"],
                f"{identifier}.estimated_detection.{failure_id}",
            )
        if set(detection) - set(targets):
            raise FeedbackGraphBuildError(
                f"verifier {identifier} estimates non-target failure modes"
            )
        for target in targets:
            detection.setdefault(target, 0.0)
        lineage = replace(
            template.lineage,
            model=template.model or template.lineage.model,
            context_sources=tuple(
                dict.fromkeys(
                    (*template.lineage.context_sources, *tuple_of_strings(item.get("additional_context_sources")))
                )
            ),
            modality=tuple(
                dict.fromkeys(
                    (*template.lineage.modality, *tuple_of_strings(item.get("additional_modalities")))
                )
            ),
            oracle=(
                str(item["oracle"])
                if item.get("oracle") is not None
                else template.lineage.oracle
            ),
        )
        verifiers.append(
            VerifierSpec(
                verifier_id=identifier,
                kind=template.kind,
                cost=template.cost,
                blocking=template.blocking,
                failure_modes=targets,
                template_id=template.template_id,
                objective=str(item["objective"]),
                prompt=str(item["prompt"]),
                estimated_detection=detection,
                timeout_s=template.timeout_s,
                sandbox=template.sandbox,
                network_access=template.network_access,
                isolation=template.isolation,
                model=template.model,
                command=template.command,
                failure_exit_codes=template.failure_exit_codes,
                working_directory=template.working_directory,
                lineage=lineage,
            )
        )
        verifier_ids.add(identifier)
    if not verifiers:
        raise FeedbackGraphBuildError("verifier planner returned no candidates")

    blind_spots: list[SharedBlindSpot] = []
    scenario_ids: set[str] = set()
    for item in raw.get("shared_blind_spots", []):
        if not isinstance(item, Mapping):
            raise FeedbackGraphBuildError("shared blind spot is not an object")
        identifier = str(item["id"])
        members = tuple_of_strings(item.get("affected_verifiers"))
        modes = tuple_of_strings(item.get("failure_modes"))
        if identifier in scenario_ids:
            raise FeedbackGraphBuildError(f"duplicate blind-spot id: {identifier}")
        if len(members) < 2 or set(members) - verifier_ids:
            raise FeedbackGraphBuildError(
                f"blind spot {identifier} references invalid verifiers"
            )
        if not modes or set(modes) - failure_ids:
            raise FeedbackGraphBuildError(
                f"blind spot {identifier} references invalid failure modes"
            )
        scenario_ids.add(identifier)
        blind_spots.append(
            SharedBlindSpot(
                scenario_id=identifier,
                description=str(item["description"]),
                weight=_probability(item["weight"], f"{identifier}.weight"),
                affected_verifiers=members,
                failure_modes=modes,
                residual_detection=_probability(
                    item["residual_detection"], f"{identifier}.residual_detection"
                ),
                sources=tuple_of_strings(item.get("sources")),
            )
        )
    return (
        tuple(verifiers),
        tuple(blind_spots),
        tuple_of_strings(raw.get("coverage_gaps")),
    )


def _all_shared_sources(verifiers: tuple[VerifierSpec, ...]) -> tuple[str, ...]:
    shared: set[str] = set()
    for index, left in enumerate(verifiers):
        for right in verifiers[index + 1 :]:
            shared.update(left.lineage.shared_sources(right.lineage))
    return tuple(sorted(shared))


def _probability(value: Any, label: str) -> float:
    parsed = float(value)
    if not 0 <= parsed <= 1:
        raise FeedbackGraphBuildError(f"{label} is outside [0,1]")
    return parsed


def _turn_error(events: tuple[Mapping[str, Any], ...]) -> str:
    for event in reversed(events):
        if event.get("type") == "turn.failed":
            error = event.get("error", {})
            if isinstance(error, Mapping) and error.get("message"):
                return str(error["message"])
        if event.get("type") == "error" and event.get("message"):
            return str(event["message"])
    return ""
