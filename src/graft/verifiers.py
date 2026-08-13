from __future__ import annotations

import json
import shlex
import shutil
import subprocess
import tempfile
import time
from contextlib import contextmanager
from importlib.resources import files
from pathlib import Path
from typing import Any, Iterator, Mapping

from graft.codex.cli_runner import CliCodexRunner, CodexExecutionError
from graft.costing import stage_cost_from_turn
from graft.evidence.baseline_archive import baseline_diff_excerpt
from graft.evidence.snapshot import freeze_source
from graft.schema import (
    EvidenceItem,
    FeedbackGraph,
    PromotionOutcome,
    RunConfig,
    SourceSnapshot,
    Verdict,
    VerifierResult,
    VerifierSpec,
)


class VerifierExecutor:
    def __init__(self, *, codex_runner: CliCodexRunner | None = None) -> None:
        self.codex_runner = codex_runner or CliCodexRunner()

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
            evidence = (
                EvidenceItem(kind="command", observation=summary, command=command),
            ) if verdict == Verdict.FAIL else ()
            promotion_outcome = None
            if spec.revalidates_feedback:
                if verdict == Verdict.PASS:
                    promotion_outcome = PromotionOutcome.FIXED_AND_PRESERVED
                elif verdict == Verdict.FAIL:
                    promotion_outcome = PromotionOutcome.NOT_FIXED
                else:
                    promotion_outcome = PromotionOutcome.UNRESOLVED

        mutation = _producer_workspace_mutation(
            snapshot,
            requirements,
            config_path,
            environment_fingerprint,
        )
        if mutation:
            return self._error_result(spec, snapshot, duration, mutation)
        return VerifierResult(
            verifier_id=spec.verifier_id,
            verdict=verdict,
            summary=summary,
            source_hash=snapshot.checkpoint_key,
            blocking=spec.blocking,
            reproducible=verdict == Verdict.FAIL,
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
            executed_evidence=True,
            promotion_outcome=promotion_outcome,
        )

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
        )
        verdict_schema = Path(
            str(files("graft").joinpath("resources", "verifier_verdict.schema.json"))
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
            evidence = _parse_evidence(raw.get("evidence", []), evidence_modes)
            reproduced_modes = _blocking_reproduced_modes(
                evidence,
                turn.events,
                run_root,
                snapshot,
                requirements,
                spec,
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

        mutation = _producer_workspace_mutation(
            snapshot,
            requirements,
            config_path,
            environment_fingerprint,
        )
        if mutation:
            return self._error_result(spec, snapshot, turn.duration_s, mutation)
        stage_cost = stage_cost_from_turn(spec.verifier_id, "verifier", turn)
        return VerifierResult(
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
    mode = (
        "You may create temporary tests and artifacts because this is a disposable workspace copy."
        if spec.isolation == "temporary-copy"
        else "Do not modify files. You may inspect and execute read-only checks."
    )
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
{promotion_instructions}

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
    raw_items: Any, valid_modes: set[str] | None = None
) -> tuple[EvidenceItem, ...]:
    if not isinstance(raw_items, list):
        return ()
    result: list[EvidenceItem] = []
    for item in raw_items:
        if not isinstance(item, Mapping):
            continue
        result.append(
            EvidenceItem(
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
        )
    return tuple(result)


def _blocking_reproduced_modes(
    evidence: tuple[EvidenceItem, ...],
    events: tuple[Mapping[str, Any], ...],
    run_root: Path,
    snapshot: SourceSnapshot,
    requirements: tuple[str, ...],
    spec: VerifierSpec,
) -> tuple[str, ...]:
    observed = _observed_commands(events)
    reproduced: list[str] = []
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
        observed_artifact = _observed_artifact(item, run_root)
        if not observed_command and not observed_artifact:
            continue
        if observed_command and not _portable_reproduction_command(
            item.command, run_root, snapshot
        ):
            continue
        if (
            item.oracle_origin == "baseline_repository"
            and not _is_baseline_evidence(item, run_root, snapshot)
        ):
            continue
        if item.oracle_origin == "requirement_derived_runtime":
            valid_refs = {f"R{index}" for index in range(1, len(requirements) + 1)}
            if (
                spec.kind != "codex_agent"
                or spec.isolation != "temporary-copy"
                or item.kind not in {"runtime", "test", "trace", "state"}
                or not observed_command
                or not item.requirement_refs
                or not set(item.requirement_refs).issubset(valid_refs)
            ):
                continue
        reproduced.extend(item.failure_modes)
    return tuple(dict.fromkeys(reproduced))


def _observed_artifact(item: EvidenceItem, run_root: Path) -> bool:
    if item.kind not in {"runtime", "state", "screenshot", "trace"} or not item.path:
        return False
    candidate = Path(item.path)
    if not candidate.is_absolute():
        candidate = run_root / candidate
    try:
        resolved = candidate.resolve()
    except OSError:
        return False
    return (resolved == run_root or run_root in resolved.parents) and resolved.exists()


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


def portable_reproduction_argv(
    command: tuple[str, ...],
    *,
    frozen_files: frozenset[str],
    run_root: Path | None = None,
) -> bool:
    """Check whether a reported command survives beyond a verifier copy."""

    if not command:
        return False
    root = run_root.resolve() if run_root is not None else None
    executable = Path(command[0]).name
    if executable in {"bash", "sh", "zsh"} and any(
        flag in command for flag in ("-c", "-lc")
    ):
        return False
    inline_payload_indexes: set[int] = set()
    inline_flags = {
        "python": {"-c"},
        "python3": {"-c"},
        "pypy": {"-c"},
        "pypy3": {"-c"},
        "node": {"-e", "--eval", "-p", "--print"},
        "ruby": {"-e"},
        "perl": {"-e", "-E"},
    }.get(executable, set())
    for index, part in enumerate(command[:-1]):
        if part in inline_flags:
            inline_payload_indexes.add(index + 1)
    for index, raw in enumerate(command[1:], start=1):
        if index in inline_payload_indexes:
            continue
        token = str(raw).strip()
        if not token or token.startswith("-") or "\n" in token:
            continue
        # Test runners commonly append a node selector to a real path.
        path_text = token.split("::", 1)[0]
        raw_path = Path(path_text)
        was_absolute = raw_path.is_absolute()
        candidate = raw_path if was_absolute or root is None else root / raw_path
        try:
            resolved = candidate.resolve()
        except OSError:
            resolved = candidate
        looks_like_file = (
            (root is not None and resolved.is_file())
            or was_absolute
            or "/" in path_text
            or "\\" in path_text
            or Path(path_text).suffix
            in {
                ".py",
                ".js",
                ".mjs",
                ".cjs",
                ".rb",
                ".sh",
                ".zsh",
                ".bash",
                ".pl",
                ".php",
                ".lua",
                ".r",
                ".R",
            }
        )
        if not looks_like_file:
            continue
        if root is None:
            if was_absolute:
                return False
            relative = raw_path.as_posix()
        else:
            try:
                relative = resolved.relative_to(root).as_posix()
            except ValueError:
                return False
        if relative not in frozen_files:
            return False
    return True


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
                        inner = _simple_shell_argv(parts[index + 1])
                        if inner is not None:
                            fingerprints.update(
                                _command_fingerprints(
                                    inner, _unwrap_depth=_unwrap_depth + 1
                                )
                            )
                break
    return frozenset(fingerprints)


def _simple_shell_argv(payload: str) -> tuple[str, ...] | None:
    try:
        lexer = shlex.shlex(
            payload,
            posix=True,
            punctuation_chars="();<>|&",
        )
        lexer.whitespace_split = True
        lexer.commenters = ""
        parts = tuple(lexer)
    except ValueError:
        return None
    if not parts:
        return None
    shell_control = frozenset("();<>|&")
    if any(token and set(token) <= shell_control for token in parts):
        return None
    if any("`" in token or "$(" in token for token in parts):
        return None
    return parts


def _normalize_command(command: tuple[str, ...]) -> str:
    return " ".join(shlex.quote(part) for part in command).strip()


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
