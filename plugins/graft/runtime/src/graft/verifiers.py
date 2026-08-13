from __future__ import annotations

import hashlib
import json
import shlex
import shutil
import subprocess
import tempfile
import time
from contextlib import contextmanager
from dataclasses import dataclass
from importlib.resources import files
from pathlib import Path
from typing import Any, Iterator, Mapping

from graft.codex.cli_runner import CliCodexRunner, CodexExecutionError
from graft.costing import stage_cost_from_turn
from graft.evidence.baseline_archive import baseline_diff_excerpt
from graft.evidence.capability import eligible_routes
from graft.evidence.reproduction import (
    canonical_reproduction_argv,
    portable_reproduction_argv,
    simple_shell_argv,
)
from graft.evidence.snapshot import freeze_source
from graft.schema import (
    EvidenceAwareFeedbackGraph,
    EvidenceAwareEvidenceItem,
    EvidenceAwareVerifierResult,
    EvidenceItem,
    FeedbackGraph,
    PromotionOutcome,
    ReproductionBundle,
    RunConfig,
    SourceSnapshot,
    Verdict,
    VerifierResult,
    VerifierSpec,
    to_jsonable,
)


class VerifierExecutor:
    def __init__(
        self,
        *,
        codex_runner: CliCodexRunner | None = None,
        protect_source_workspace: bool = True,
        disposable_environment: bool = False,
    ) -> None:
        self.codex_runner = codex_runner or CliCodexRunner()
        # Product execution treats the frozen candidate as immutable.  A benchmark
        # may instead run exactly one verifier in an expendable, whole-environment
        # branch whose workspace is allowed to change.  That research-only caller
        # must opt out explicitly; protecting the source remains the safe default.
        self.protect_source_workspace = protect_source_workspace
        self.disposable_environment = disposable_environment

    def run(
        self,
        spec: VerifierSpec,
        snapshot: SourceSnapshot,
        *,
        requirements: tuple[str, ...],
        graph: FeedbackGraph,
        config_path: Path,
        environment_fingerprint: str,
    ) -> VerifierResult:
        if spec.kind == "command":
            return self._run_command(
                spec,
                snapshot,
                requirements=requirements,
                graph=graph,
                config_path=config_path,
                environment_fingerprint=environment_fingerprint,
            )
        if spec.kind in {"codex_review", "codex_agent"}:
            return self._run_codex_verifier(
                spec,
                snapshot,
                requirements=requirements,
                graph=graph,
                config_path=config_path,
                environment_fingerprint=environment_fingerprint,
            )
        raise ValueError(f"Unsupported verifier kind: {spec.kind}")

    def _run_command(
        self,
        spec: VerifierSpec,
        snapshot: SourceSnapshot,
        *,
        requirements: tuple[str, ...],
        graph: FeedbackGraph,
        config_path: Path,
        environment_fingerprint: str,
    ) -> VerifierResult:
        with _execution_workspace(Path(snapshot.root), spec.isolation) as run_root:
            cwd = (
                (run_root / spec.working_directory).resolve()
                if spec.working_directory
                else run_root.resolve()
            )
            if cwd != run_root.resolve() and run_root.resolve() not in cwd.parents:
                return self._error_result(
                    spec,
                    snapshot,
                    0.0,
                    f"Verifier working_directory escapes its workspace: {spec.working_directory}",
                )
            command = tuple(
                part.replace("{repo}", str(run_root)) for part in spec.command
            )
            started = time.monotonic()
            try:
                completed = subprocess.run(
                    command,
                    cwd=cwd,
                    text=True,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    timeout=spec.timeout_s,
                    check=False,
                )
            except subprocess.TimeoutExpired as exc:
                return self._error_result(
                    spec,
                    snapshot,
                    time.monotonic() - started,
                    f"Timed out after {spec.timeout_s:.1f}s",
                    command=command,
                    stdout=_trim(exc.stdout or ""),
                    stderr=_trim(exc.stderr or ""),
                )
            except OSError as exc:
                return self._error_result(
                    spec,
                    snapshot,
                    time.monotonic() - started,
                    f"Could not execute verifier: {exc}",
                    command=command,
                )
            duration = time.monotonic() - started
            stdout = _trim(completed.stdout)
            stderr = _trim(completed.stderr)
            if completed.returncode == 0:
                verdict = Verdict.PASS
                summary = "Configured evidence command completed successfully."
            elif completed.returncode in spec.failure_exit_codes:
                verdict = Verdict.FAIL
                summary = _last_nonempty_line(stderr, stdout) or (
                    f"Command exited with {completed.returncode}."
                )
            else:
                verdict = Verdict.ERROR
                summary = f"Unexpected exit code {completed.returncode}."
            evidence_aware = isinstance(graph, EvidenceAwareFeedbackGraph)
            reproduction_bundles: tuple[ReproductionBundle, ...] = ()
            if evidence_aware:
                routes = eligible_routes(
                    graph,
                    spec.verifier_id,
                    transport="standalone_command",
                )
                canonical = canonical_reproduction_argv(
                    command,
                    frozen_files=frozenset(snapshot.file_hashes),
                    run_root=run_root,
                )
                if routes and canonical is not None and verdict in {Verdict.PASS, Verdict.FAIL}:
                    route = routes[0]
                    expected = "command exits with status 0"
                    actual = f"command exited with status {completed.returncode}: {summary}"
                    evidence = (
                        EvidenceAwareEvidenceItem(
                            kind="command",
                            observation=summary,
                            command=command,
                            failure_modes=spec.failure_modes,
                            oracle_origin=route.oracle_origin,
                            expected=expected,
                            actual=actual,
                        ),
                    )
                    reproduction_bundles = (
                        _make_reproduction_bundle(
                            snapshot=snapshot,
                            spec=spec,
                            route_id=route.route_id,
                            dependency_origins=route.dependency_origins,
                            oracle_origin=route.oracle_origin,
                            evidence_kind="command",
                            transport="standalone_command",
                            observation=summary,
                            expected=expected,
                            actual=actual,
                            command=canonical,
                            artifact_path=None,
                            requirement_refs=(),
                            failure_modes=spec.failure_modes,
                        ),
                    )
                else:
                    evidence = ()
            else:
                evidence = (
                    EvidenceItem(kind="command", observation=summary, command=command),
                ) if verdict == Verdict.FAIL else ()
            promotion_outcome = None
            if spec.revalidates_feedback:
                if verdict == Verdict.PASS and (
                    not evidence_aware or reproduction_bundles
                ):
                    promotion_outcome = PromotionOutcome.FIXED_AND_PRESERVED
                elif verdict == Verdict.FAIL:
                    promotion_outcome = PromotionOutcome.NOT_FIXED
                else:
                    promotion_outcome = PromotionOutcome.UNRESOLVED

        mutation = (
            _producer_workspace_mutation(
                snapshot,
                requirements,
                config_path,
                environment_fingerprint,
            )
            if self.protect_source_workspace
            else None
        )
        if mutation:
            return self._error_result(spec, snapshot, duration, mutation)
        result_type = (
            EvidenceAwareVerifierResult
            if isinstance(graph, EvidenceAwareFeedbackGraph)
            else VerifierResult
        )
        result_kwargs = dict(
            verifier_id=spec.verifier_id,
            verdict=verdict,
            summary=summary,
            source_hash=snapshot.checkpoint_key,
            blocking=spec.blocking,
            reproducible=(
                verdict == Verdict.FAIL
                and (
                    bool(reproduction_bundles)
                    if isinstance(graph, EvidenceAwareFeedbackGraph)
                    else True
                )
            ),
            duration_s=duration,
            failure_modes=spec.failure_modes if verdict == Verdict.FAIL else (),
            evidence=evidence,
            command=command,
            stdout=stdout,
            stderr=stderr,
            exit_code=completed.returncode,
            confidence=1.0 if verdict in {Verdict.PASS, Verdict.FAIL} else 0.0,
            lineage=spec.lineage,
            usage={},
            executed_evidence=(
                bool(reproduction_bundles)
                if isinstance(graph, EvidenceAwareFeedbackGraph)
                else True
            ),
            promotion_outcome=promotion_outcome,
        )
        if result_type is EvidenceAwareVerifierResult:
            result_kwargs["reproduction_bundles"] = reproduction_bundles
        return result_type(**result_kwargs)

    def _run_codex_verifier(
        self,
        spec: VerifierSpec,
        snapshot: SourceSnapshot,
        *,
        requirements: tuple[str, ...],
        graph: FeedbackGraph,
        config_path: Path,
        environment_fingerprint: str,
    ) -> VerifierResult:
        prompt = _verifier_prompt(
            spec,
            snapshot,
            requirements,
            graph,
            config_path=config_path,
            environment_fingerprint=environment_fingerprint,
            disposable_environment=self.disposable_environment,
        )
        verdict_schema = Path(
            str(
                files("graft").joinpath(
                    "resources",
                    (
                        "verifier_verdict_vnext.schema.json"
                        if isinstance(graph, EvidenceAwareFeedbackGraph)
                        else "verifier_verdict.schema.json"
                    ),
                )
            )
        )
        started = time.monotonic()
        with _execution_workspace(Path(snapshot.root), spec.isolation) as run_root:
            try:
                turn = self.codex_runner.start_thread(
                    prompt,
                    run_root,
                    RunConfig(
                        sandbox=spec.sandbox,
                        network_access=spec.network_access,
                        model=spec.model,
                        timeout_s=spec.timeout_s,
                        ephemeral=True,
                        output_schema=verdict_schema,
                        isolate_config=True,
                        disable_hooks=True,
                        skip_git_repo_check=True,
                    ),
                )
            except CodexExecutionError as exc:
                return self._error_result(
                    spec, snapshot, time.monotonic() - started, str(exc)
                )
            if turn.return_code != 0:
                detail = _turn_error(turn.events) or _trim(turn.stderr)
                return self._error_result(
                    spec,
                    snapshot,
                    turn.duration_s,
                    f"Codex verifier exited with {turn.return_code}: {detail or 'unknown error'}",
                    stderr=detail,
                )
            try:
                raw = json.loads(turn.final_response)
                verdict = Verdict(str(raw["verdict"]))
            except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
                return self._error_result(
                    spec,
                    snapshot,
                    turn.duration_s,
                    f"Invalid Codex verifier verdict: {exc}",
                    stdout=_trim(turn.final_response),
                    stderr=_trim(turn.stderr),
                )
            reported_modes = tuple(
                str(item) for item in raw.get("failure_modes", [])
            )
            valid_modes = tuple(
                item for item in reported_modes if item in set(spec.failure_modes)
            )
            # A failing verdict's top-level modes identify the failures it claims.
            # A passing promotion verdict intentionally has no top-level failures,
            # but its executed evidence must still map the repaired and preserved
            # checks to the verifier's target modes. Filtering PASS evidence by the
            # empty top-level list made successful promotion mechanically
            # impossible even when the commands were present in Codex events.
            evidence_modes = (
                set(spec.failure_modes)
                if verdict == Verdict.PASS and spec.revalidates_feedback
                else set(valid_modes)
            )
            evidence = _parse_evidence(
                raw.get("evidence", []),
                evidence_modes,
                evidence_aware=isinstance(graph, EvidenceAwareFeedbackGraph),
            )
            validated_evidence = _validated_reproduction_evidence(
                evidence,
                turn.events,
                run_root,
                snapshot,
                requirements,
                spec,
                disposable_environment=self.disposable_environment,
            )
            reproduction_bundles: tuple[ReproductionBundle, ...] = ()
            if isinstance(graph, EvidenceAwareFeedbackGraph):
                bundles: list[ReproductionBundle] = []
                for validated in validated_evidence:
                    item = validated.item
                    expected = getattr(item, "expected", None)
                    actual = getattr(item, "actual", None)
                    if expected is None or actual is None:
                        continue
                    routes = eligible_routes(
                        graph,
                        spec.verifier_id,
                        oracle_origin=item.oracle_origin,
                        transport=validated.transport,
                    )
                    if not routes:
                        continue
                    route = routes[0]
                    bundles.append(
                        _make_reproduction_bundle(
                            snapshot=snapshot,
                            spec=spec,
                            route_id=route.route_id,
                            dependency_origins=route.dependency_origins,
                            oracle_origin=item.oracle_origin,
                            evidence_kind=item.kind,
                            transport=validated.transport,
                            observation=item.observation,
                            expected=expected,
                            actual=actual,
                            command=validated.command,
                            artifact_path=validated.artifact_path,
                            requirement_refs=item.requirement_refs,
                            failure_modes=item.failure_modes,
                        )
                    )
                reproduction_bundles = tuple(bundles)
                reproduced_modes = tuple(
                    dict.fromkeys(
                        failure_mode
                        for bundle in reproduction_bundles
                        for failure_mode in bundle.failure_modes
                    )
                )
            else:
                reproduced_modes = tuple(
                    dict.fromkeys(
                        failure_mode
                        for validated in validated_evidence
                        for failure_mode in validated.item.failure_modes
                    )
                )
            claimed_reproducible = bool(raw.get("reproducible", False))
            reproducible = (
                verdict == Verdict.FAIL
                and claimed_reproducible
                and bool(reproduced_modes)
            )
            summary = str(raw.get("summary", "Codex verifier completed."))
            confidence = float(raw.get("confidence", 0.0))
            if not 0 <= confidence <= 1:
                confidence = 0.0
            stdout = _trim(turn.final_response)
            stderr = _trim(turn.stderr)
            promotion_outcome = None
            if spec.revalidates_feedback:
                try:
                    promotion_outcome = PromotionOutcome(
                        str(raw.get("promotion_outcome", "unresolved"))
                    )
                except ValueError:
                    promotion_outcome = PromotionOutcome.UNRESOLVED
                # Promotion is an evidence-bearing state transition, not a model
                # opinion.  A reviewer may claim FIXED_AND_PRESERVED while its
                # reported commands fail to match any observed Codex tool event.
                # Preserve the PASS verdict for audit, but never promote that
                # unaudited claim.
                if (
                    promotion_outcome == PromotionOutcome.FIXED_AND_PRESERVED
                    and not reproduced_modes
                ):
                    promotion_outcome = PromotionOutcome.UNRESOLVED

        mutation = (
            _producer_workspace_mutation(
                snapshot,
                requirements,
                config_path,
                environment_fingerprint,
            )
            if self.protect_source_workspace
            else None
        )
        if mutation:
            return self._error_result(spec, snapshot, turn.duration_s, mutation)
        stage_cost = stage_cost_from_turn(spec.verifier_id, "verifier", turn)
        result_type = (
            EvidenceAwareVerifierResult
            if isinstance(graph, EvidenceAwareFeedbackGraph)
            else VerifierResult
        )
        result_kwargs = dict(
            verifier_id=spec.verifier_id,
            verdict=verdict,
            summary=summary,
            source_hash=snapshot.checkpoint_key,
            blocking=spec.blocking,
            reproducible=reproducible,
            duration_s=turn.duration_s,
            failure_modes=reproduced_modes if reproducible else valid_modes,
            evidence=evidence,
            stdout=stdout,
            stderr=stderr,
            exit_code=turn.return_code,
            confidence=confidence,
            lineage=spec.lineage,
            usage=dict(turn.usage),
            estimated_cost_usd=stage_cost.estimated_cost_usd,
            executed_evidence=bool(reproduced_modes),
            promotion_outcome=promotion_outcome,
        )
        if result_type is EvidenceAwareVerifierResult:
            result_kwargs["reproduction_bundles"] = reproduction_bundles
        return result_type(**result_kwargs)

    @staticmethod
    def _error_result(
        spec: VerifierSpec,
        snapshot: SourceSnapshot,
        duration: float,
        error: str,
        *,
        command: tuple[str, ...] = (),
        stdout: str = "",
        stderr: str = "",
    ) -> VerifierResult:
        return VerifierResult(
            verifier_id=spec.verifier_id,
            verdict=Verdict.ERROR,
            summary=error,
            source_hash=snapshot.checkpoint_key,
            blocking=spec.blocking,
            reproducible=False,
            duration_s=duration,
            command=command,
            stdout=stdout,
            stderr=stderr,
            error=error,
            lineage=spec.lineage,
        )


def _verifier_prompt(
    spec: VerifierSpec,
    snapshot: SourceSnapshot,
    requirements: tuple[str, ...],
    graph: FeedbackGraph,
    *,
    config_path: Path,
    environment_fingerprint: str,
    disposable_environment: bool = False,
) -> str:
    by_failure = {item.failure_mode_id: item for item in graph.failure_modes}
    targets = [by_failure[item] for item in spec.failure_modes if item in by_failure]
    target_text = "\n".join(
        f"- {item.failure_mode_id}: {item.description}; observable signals: "
        f"{', '.join(item.observable_signals)}"
        for item in targets
    )
    requirement_text = "\n".join(
        f"- R{index}: {item}" for index, item in enumerate(requirements, start=1)
    )
    changed = _candidate_changed_files(snapshot)
    changed_text = (
        "\n".join(f"- {item}" for item in changed)
        if changed is not None
        else "- <baseline manifest unavailable>"
    )
    if changed == ():
        changed_text = "- <none>"
    baseline_diff = baseline_diff_excerpt(snapshot)
    promotion_instructions = ""
    if spec.revalidates_feedback:
        promotion_instructions = """
This verifier is the required candidate-promotion guard. Re-run the prior feedback reproduction
named in the planner objective when it is available. Return PASS only after an eligible command or
runtime artifact was actually observed and shows the prior failure is fixed while the named raw
behaviors remain preserved. Include that executed command/artifact as evidence even for PASS, map it
to the target failure modes, and use the same evidence-origin rules below. If execution is
unavailable or the result is ambiguous, ABSTAIN; source review alone cannot promote the candidate.
Set promotion_outcome to exactly one of: fixed_and_preserved, not_fixed, regressed, unresolved.
Use fixed_and_preserved only with executed evidence for both the repaired finding and the named
preserved behavior. Use regressed when the repair fixes or changes the target but violates a named
raw or unchanged-baseline behavior.
"""
    evidence_protocol = ""
    if isinstance(graph, EvidenceAwareFeedbackGraph):
        capability = next(
            (
                item
                for item in graph.evidence_capabilities
                if item.verifier_id == spec.verifier_id
            ),
            None,
        )
        assessment = next(
            (
                item
                for item in graph.evidence_capability_assessments
                if item.verifier_id == spec.verifier_id
            ),
            None,
        )
        evidence_protocol = f"""

Evidence-capability contract declared before selection:
{json.dumps(to_jsonable(capability), ensure_ascii=False, indent=2)}

Deterministic preflight assessment:
{json.dumps(to_jsonable(assessment), ensure_ascii=False, indent=2)}

Your final executable evidence must use one preflight-eligible route exactly. Exploration may use
other tools, but a blocking result must end with a standalone command whose dependencies already
belong to the declared task environment, frozen candidate, or unchanged baseline. Do not install a
new dependency or rely on a verifier-created file for the final reproduction. For every evidence
item, fill expected and actual with the concrete compared outcomes; use null only for advisory or
unavailable observations. GRAFT will independently match the command event, canonicalize it for
the producer checkpoint, and reject any result that does not match this capability contract.
"""
    if disposable_environment:
        mode = (
            "This entire task environment is an expendable verifier branch restored from the "
            "frozen candidate. You may create temporary tests and artifacts here; no producer "
            "or other verifier shares this branch."
        )
    elif spec.isolation == "temporary-copy":
        mode = (
            "You may create temporary tests and artifacts because this is a disposable "
            "workspace copy."
        )
    else:
        mode = "Do not modify files. You may inspect and execute read-only checks."
    return f"""You are a task-specific verifier instantiated by {graph.method}.

Verifier objective:
{spec.objective}

Planner instructions:
{spec.prompt}

Raw requirements:
{requirement_text or '- <missing>'}

Target Failure Modes:
{target_text or '- <missing>'}

GRAFT content checkpoint hash (not a Git commit or ref): {snapshot.checkpoint_key}
Tree hash: {snapshot.tree_hash}
Requirement hash: {snapshot.requirement_hash}
Configuration hash: {snapshot.config_hash}
Configuration source used by this run: {config_path}
Environment fingerprint used by this run: {environment_fingerprint}
Baseline tree hash: {snapshot.baseline_tree_hash or '<unavailable>'}
Files added or modified after the task baseline:
{changed_text}
Immutable baseline-to-candidate diff (implementation evidence only; not a contract oracle):
{baseline_diff}
{mode}
{promotion_instructions}{evidence_protocol}

Inspect the actual repository and use tools when they can establish observable evidence. Do not
trust the producer's summary. The raw requirements are authoritative. Baseline repository evidence
may clarify them, but candidate-added files, generated tests, mocks, stubs, and source inspection
cannot create a new contract or independently justify blocking feedback. Label every evidence item
with its target failure_modes and one oracle_origin:
- authoritative_runtime: the real task environment or user-visible artifact exhibited the failure;
- baseline_repository: a test/oracle that existed before this task failed;
- requirement_derived_runtime: a check you derived directly from numbered raw requirements and
  executed against the real candidate in this disposable workspace copy, without replacing the
  candidate behavior with a mock, stub, or self-contained simulation;
- candidate_repository: a check or document added by the producer during this task;
- verifier_generated: a test, mock, stub, or oracle you created;
- source_inspection: static/code-review reasoning only;
- unavailable: the needed oracle could not be reached.
A blocking finding discovered inside a multi-case harness is not yet eligible evidence. Before the
final verdict, execute a minimal reproduction for each blocking finding as its own simple tool
command: do not join it to setup, cleanup, another check, a pipeline, a redirection, or a second
command. In the evidence object, copy that exact executed argv or shell payload without simplifying,
rewriting, or substituting an equivalent command. If you cannot execute and report that exact
standalone reproduction, mark the finding non-reproducible or abstain. GRAFT intentionally rejects
claimed commands that do not identify an observed tool event.
Do not use a shell heredoc for executable evidence. You may create a temporary file with the
file-edit tool for exploratory checks, but the final reported reproduction must be one standalone,
portable command. Prefer an inline single-process argv such as `python -c <program>` or invoke a
test/program that already belongs to the frozen candidate. Report that exact standalone command.
A heredoc, pipeline, redirection, chained shell program, rewritten approximation of an observed
command, or command that depends on a verifier-created temporary file is not eligible for feedback
and must be reported as non-reproducible, even if it passed or failed in this copy.
A code-review suspicion, successful source-inspection command, or generated mock/stub
counterexample is not mechanically reproducible blocking evidence. Authoritative runtime and
unchanged baseline evidence must name the exact failure modes plus an actually executed command or
observed runtime artifact. Requirement-derived runtime evidence must additionally use kind
`runtime`, `test`, `trace`, or `state`; cite one or more exact numbered requirement_refs such as
`R1`; execute a command against the actual candidate in a temporary workspace copy; and observe a
direct violation of those cited requirements. Merely inspecting/parsing source, asserting an
unstated stronger policy, or testing a substitute implementation does not qualify. If a
precondition is not stated by the raw requirements or baseline contract, abstain instead of
enforcing the stricter interpretation. When competing standard semantics remain plausible, run a
case that distinguishes them and report which branch the candidate implements; do not call that
branch wrong without contract authority. Return only the schema-conforming verdict object. If the
required capability or oracle is unavailable, abstain instead of guessing.
Treat explicit environment and evaluation constraints in the raw requirements as authoritative too.
Do not label a failure caused solely by a dependency that the task says is absent, forbidden to
install, or supplied only by the evaluation boundary as a product regression. If an interface
instruction appears to require that unavailable dependency at runtime, record the contract conflict
and abstain unless an allowed task-provided boundary or unchanged baseline oracle resolves it. Never
install or inspect a dependency that the task forbids in order to manufacture that resolution.
"""


@contextmanager
def _execution_workspace(root: Path, isolation: str) -> Iterator[Path]:
    resolved = root.resolve()
    if isolation == "ephemeral":
        yield resolved
        return
    if isolation != "temporary-copy":
        raise ValueError(f"Unsupported verifier isolation: {isolation}")
    with tempfile.TemporaryDirectory(prefix="graft-verifier-") as directory:
        target = Path(directory) / "workspace"
        shutil.copytree(
            resolved,
            target,
            symlinks=True,
            ignore=shutil.ignore_patterns(
                ".git", ".graft", "__pycache__", "*.pyc", "*.pyo", ".DS_Store"
            ),
        )
        yield target


def _producer_workspace_mutation(
    snapshot: SourceSnapshot,
    requirements: tuple[str, ...],
    config_path: Path,
    environment_fingerprint: str,
) -> str | None:
    after = freeze_source(
        Path(snapshot.root),
        requirements=requirements,
        config_path=config_path,
        environment_fingerprint=environment_fingerprint,
    )
    if after.tree_hash != snapshot.tree_hash:
        return "Verifier changed the producer workspace; evidence is invalid."
    return None


def _parse_evidence(
    raw_items: Any,
    valid_modes: set[str] | None = None,
    *,
    evidence_aware: bool = False,
) -> tuple[EvidenceItem, ...]:
    if not isinstance(raw_items, list):
        return ()
    result: list[EvidenceItem] = []
    for item in raw_items:
        if not isinstance(item, Mapping):
            continue
        item_type = EvidenceAwareEvidenceItem if evidence_aware else EvidenceItem
        item_kwargs = dict(
                kind=str(item.get("kind", "observation")),
                observation=str(item.get("observation", "")),
                path=str(item["path"]) if item.get("path") else None,
                line=int(item["line"]) if item.get("line") else None,
                command=tuple(str(value) for value in item.get("command", [])),
                failure_modes=tuple(
                    str(value)
                    for value in item.get("failure_modes", [])
                    if valid_modes is None or str(value) in valid_modes
                ),
                requirement_refs=tuple(
                    str(value) for value in item.get("requirement_refs", [])
                ),
                oracle_origin=str(item.get("oracle_origin", "unspecified")),
        )
        if evidence_aware:
            item_kwargs["expected"] = (
                str(item["expected"])
                if item.get("expected") is not None
                else None
            )
            item_kwargs["actual"] = (
                str(item["actual"])
                if item.get("actual") is not None
                else None
            )
        result.append(item_type(**item_kwargs))
    return tuple(result)


@dataclass(frozen=True)
class _ValidatedEvidence:
    item: EvidenceItem
    transport: str
    command: tuple[str, ...] = ()
    artifact_path: str | None = None


def _blocking_reproduced_modes(
    evidence: tuple[EvidenceItem, ...],
    events: tuple[Mapping[str, Any], ...],
    run_root: Path,
    snapshot: SourceSnapshot,
    requirements: tuple[str, ...],
    spec: VerifierSpec,
    *,
    disposable_environment: bool = False,
) -> tuple[str, ...]:
    validated = _validated_reproduction_evidence(
        evidence,
        events,
        run_root,
        snapshot,
        requirements,
        spec,
        disposable_environment=disposable_environment,
    )
    return tuple(
        dict.fromkeys(
            failure_mode
            for record in validated
            for failure_mode in record.item.failure_modes
        )
    )


def _validated_reproduction_evidence(
    evidence: tuple[EvidenceItem, ...],
    events: tuple[Mapping[str, Any], ...],
    run_root: Path,
    snapshot: SourceSnapshot,
    requirements: tuple[str, ...],
    spec: VerifierSpec,
    *,
    disposable_environment: bool = False,
) -> tuple[_ValidatedEvidence, ...]:
    observed = _observed_commands(events)
    validated: list[_ValidatedEvidence] = []
    for item in evidence:
        if item.oracle_origin not in {
            "authoritative_runtime",
            "baseline_repository",
            "requirement_derived_runtime",
        }:
            continue
        observed_command = False
        if item.command:
            wanted = _command_fingerprints(item.command)
            observed_command = bool(wanted & observed)
        artifact_path = _observed_artifact_path(item, run_root)
        if not observed_command and artifact_path is None:
            continue
        canonical_command: tuple[str, ...] = ()
        if observed_command:
            canonical = canonical_reproduction_argv(
                item.command,
                frozen_files=frozenset(snapshot.file_hashes),
                run_root=run_root,
            )
            if canonical is None:
                continue
            canonical_command = canonical
        if (
            item.oracle_origin == "baseline_repository"
            and not _is_baseline_evidence(item, run_root, snapshot)
        ):
            continue
        if item.oracle_origin == "requirement_derived_runtime":
            valid_refs = {f"R{index}" for index in range(1, len(requirements) + 1)}
            if (
                spec.kind != "codex_agent"
                or (
                    spec.isolation != "temporary-copy"
                    and not disposable_environment
                )
                or item.kind not in {"runtime", "test", "trace", "state"}
                or not observed_command
                or not item.requirement_refs
                or not set(item.requirement_refs).issubset(valid_refs)
            ):
                continue
        validated.append(
            _ValidatedEvidence(
                item=item,
                transport=(
                    "standalone_command" if observed_command else "runtime_artifact"
                ),
                command=canonical_command,
                artifact_path=artifact_path,
            )
        )
    return tuple(validated)


def _observed_artifact(item: EvidenceItem, run_root: Path) -> bool:
    return _observed_artifact_path(item, run_root) is not None


def _observed_artifact_path(item: EvidenceItem, run_root: Path) -> str | None:
    if item.kind not in {"runtime", "state", "screenshot", "trace"} or not item.path:
        return None
    candidate = Path(item.path)
    if not candidate.is_absolute():
        candidate = run_root / candidate
    try:
        resolved = candidate.resolve()
    except OSError:
        return None
    if not ((resolved == run_root or run_root in resolved.parents) and resolved.exists()):
        return None
    try:
        return resolved.relative_to(run_root.resolve()).as_posix()
    except ValueError:
        return None


def _is_baseline_evidence(
    item: EvidenceItem, run_root: Path, snapshot: SourceSnapshot
) -> bool:
    if not snapshot.baseline_file_hashes or not item.path:
        return False
    candidate = Path(item.path)
    try:
        if candidate.is_absolute():
            relative = candidate.resolve().relative_to(run_root.resolve())
        else:
            relative = candidate
    except (OSError, ValueError):
        return False
    name = relative.as_posix()
    return (
        name in snapshot.baseline_file_hashes
        and snapshot.file_hashes.get(name) == snapshot.baseline_file_hashes[name]
    )


def _candidate_changed_files(snapshot: SourceSnapshot) -> tuple[str, ...] | None:
    if not snapshot.baseline_tree_hash or not snapshot.baseline_file_hashes:
        return None
    changed = {
        path
        for path, digest in snapshot.file_hashes.items()
        if snapshot.baseline_file_hashes.get(path) != digest
    }
    changed.update(
        f"{path} (deleted)"
        for path in snapshot.baseline_file_hashes
        if path not in snapshot.file_hashes
    )
    return tuple(sorted(changed))


def _portable_reproduction_command(
    command: tuple[str, ...], run_root: Path, snapshot: SourceSnapshot
) -> bool:
    """Reject verifier-only files that disappear before feedback continuation.

    A command is portable when every file it references belongs to the frozen
    candidate snapshot. Inline programs (for example ``python -c``) contain their
    own reproduction and therefore do not depend on a temporary verifier artifact.
    This is deliberately checked while the isolated verifier workspace still
    exists; a generated script can otherwise look reproducible in the report but
    be absent when the producer thread receives that report.
    """

    return portable_reproduction_argv(
        command,
        frozen_files=frozenset(snapshot.file_hashes),
        run_root=run_root,
    )


def _observed_commands(events: tuple[Mapping[str, Any], ...]) -> frozenset[str]:
    result: set[str] = set()
    for event in events:
        item = event.get("item")
        if not isinstance(item, Mapping):
            continue
        if item.get("type") not in {"command_execution", "command"}:
            continue
        command = item.get("command")
        if isinstance(command, list):
            result.update(_command_fingerprints(tuple(str(part) for part in command)))
        elif command is not None:
            result.update(_command_fingerprints(str(command)))
    return frozenset(result)


def _command_fingerprints(
    command: tuple[str, ...] | str, *, _unwrap_depth: int = 0
) -> frozenset[str]:
    """Canonicalize executed and reported commands without substring matching.

    Codex events commonly serialize ``/bin/bash -lc '...'`` as one string,
    while the structured verdict reports ``["bash", "-lc", "..."]``. Exact
    string comparison rejects that genuinely executed evidence. Parsing both
    forms into argv and comparing the shell payload preserves an exact match
    while tolerating executable paths and quoting representation.
    """

    if isinstance(command, str):
        try:
            parts = tuple(shlex.split(command))
        except ValueError:
            normalized = " ".join(command.split())
            return frozenset({f"text:{normalized}"}) if normalized else frozenset()
    else:
        parts = tuple(str(part) for part in command)
    if not parts:
        return frozenset()
    if len(parts) == 1 and any(character.isspace() for character in parts[0]):
        # The verdict schema permits either argv or a shell payload but encodes
        # both as an array of strings. Models sometimes emit one exact payload
        # as ``["python3 -c '...'"]``. Treat only that unambiguous single-item
        # representation as shell text; a genuine argv list remains unchanged.
        return _command_fingerprints(parts[0], _unwrap_depth=_unwrap_depth)

    executable = Path(parts[0]).name
    normalized_parts = (executable, *parts[1:])
    fingerprints = {f"argv:{_normalize_command(normalized_parts)}"}
    if executable in {"bash", "sh", "zsh"}:
        for flag in ("-c", "-lc"):
            if flag in parts:
                index = parts.index(flag)
                if index + 1 < len(parts):
                    payload = " ".join(parts[index + 1].split())
                    fingerprints.add(f"shell:{executable}:{payload}")
                    # Codex command events wrap ordinary commands in the configured
                    # login shell, while structured verifier evidence may report the
                    # exact inner argv. Recognize that representation only for one
                    # simple command. Compound shell programs are deliberately not
                    # unwrapped because observing one branch or pipeline is not proof
                    # that a separately reported inner command executed as claimed.
                    if _unwrap_depth < 2:
                        inner = simple_shell_argv(parts[index + 1])
                        if inner is not None:
                            fingerprints.update(
                                _command_fingerprints(
                                    inner, _unwrap_depth=_unwrap_depth + 1
                                )
                            )
                break
    return frozenset(fingerprints)


def _normalize_command(command: tuple[str, ...]) -> str:
    return " ".join(shlex.quote(part) for part in command).strip()


def _make_reproduction_bundle(
    *,
    snapshot: SourceSnapshot,
    spec: VerifierSpec,
    route_id: str,
    dependency_origins: tuple[str, ...],
    oracle_origin: str,
    evidence_kind: str,
    transport: str,
    observation: str,
    expected: str | None,
    actual: str | None,
    command: tuple[str, ...],
    artifact_path: str | None,
    requirement_refs: tuple[str, ...],
    failure_modes: tuple[str, ...],
) -> ReproductionBundle:
    payload = {
        "checkpoint_key": snapshot.checkpoint_key,
        "verifier_id": spec.verifier_id,
        "failure_modes": list(failure_modes),
        "oracle_origin": oracle_origin,
        "evidence_kind": evidence_kind,
        "transport": transport,
        "observation": observation,
        "expected": expected,
        "actual": actual,
        "command": list(command),
        "artifact_path": artifact_path,
        "requirement_refs": list(requirement_refs),
        "route_id": route_id,
        "dependency_origins": list(dependency_origins),
        "lineage": to_jsonable(spec.lineage),
    }
    bundle_id = hashlib.sha256(
        json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    return ReproductionBundle(
        bundle_id=bundle_id,
        checkpoint_key=snapshot.checkpoint_key,
        verifier_id=spec.verifier_id,
        failure_modes=failure_modes,
        oracle_origin=oracle_origin,
        evidence_kind=evidence_kind,
        transport=transport,
        observation=observation,
        expected=expected,
        actual=actual,
        command=command,
        artifact_path=artifact_path,
        requirement_refs=requirement_refs,
        route_id=route_id,
        dependency_origins=dependency_origins,
        lineage=spec.lineage,
    )


def _trim(value: str, limit: int = 64_000) -> str:
    if len(value) <= limit:
        return value
    half = limit // 2
    return value[:half] + "\n...<truncated>...\n" + value[-half:]


def _last_nonempty_line(*values: str) -> str:
    for value in values:
        lines = [line.strip() for line in value.splitlines() if line.strip()]
        if lines:
            return lines[-1]
    return ""


def _turn_error(events: tuple[Mapping[str, Any], ...]) -> str:
    for event in reversed(events):
        if event.get("type") == "turn.failed":
            error = event.get("error", {})
            if isinstance(error, Mapping) and error.get("message"):
                return str(error["message"])
        if event.get("type") == "error" and event.get("message"):
            return str(event["message"])
    return ""
