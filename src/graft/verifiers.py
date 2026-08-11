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
from graft.evidence.snapshot import freeze_source
from graft.schema import (
    EvidenceItem,
    FeedbackGraph,
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
            evidence = _parse_evidence(raw.get("evidence", []))
            claimed_reproducible = bool(raw.get("reproducible", False))
            observed_reproducer = _has_observed_reproducer(
                evidence, turn.events, run_root
            )
            reproducible = (
                verdict == Verdict.FAIL
                and claimed_reproducible
                and observed_reproducer
            )
            reported_modes = tuple(
                str(item) for item in raw.get("failure_modes", [])
            )
            valid_modes = tuple(
                item for item in reported_modes if item in set(spec.failure_modes)
            )
            summary = str(raw.get("summary", "Codex verifier completed."))
            confidence = float(raw.get("confidence", 0.0))
            if not 0 <= confidence <= 1:
                confidence = 0.0
            stdout = _trim(turn.final_response)
            stderr = _trim(turn.stderr)

        mutation = _producer_workspace_mutation(
            snapshot,
            requirements,
            config_path,
            environment_fingerprint,
        )
        if mutation:
            return self._error_result(spec, snapshot, turn.duration_s, mutation)
        return VerifierResult(
            verifier_id=spec.verifier_id,
            verdict=verdict,
            summary=summary,
            source_hash=snapshot.checkpoint_key,
            blocking=spec.blocking,
            reproducible=reproducible,
            duration_s=turn.duration_s,
            failure_modes=valid_modes,
            evidence=evidence,
            stdout=stdout,
            stderr=stderr,
            exit_code=turn.return_code,
            confidence=confidence,
            lineage=spec.lineage,
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
    requirement_text = "\n".join(f"- {item}" for item in requirements)
    mode = (
        "You may create temporary tests and artifacts because this is a disposable workspace copy."
        if spec.isolation == "temporary-copy"
        else "Do not modify files. You may inspect and execute read-only checks."
    )
    return f"""You are a task-specific verifier instantiated by GRAFT Original.

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
{mode}

Inspect the actual repository and use tools when they can establish observable evidence. Do not
trust the producer's summary. A code-review suspicion alone is not mechanically reproducible. If
you report reproducible=true, include a command that you actually executed during this turn or an
observed runtime/state artifact. Return only the schema-conforming verdict object. If the required
capability or oracle is unavailable, abstain instead of guessing.
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


def _parse_evidence(raw_items: Any) -> tuple[EvidenceItem, ...]:
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
            )
        )
    return tuple(result)


def _has_observed_reproducer(
    evidence: tuple[EvidenceItem, ...],
    events: tuple[Mapping[str, Any], ...],
    run_root: Path,
) -> bool:
    observed = _observed_commands(events)
    for item in evidence:
        if item.command:
            wanted = _normalize_command(item.command)
            if any(wanted == command or wanted in command for command in observed):
                return True
        if item.kind in {"runtime", "state", "screenshot", "trace"} and item.path:
            candidate = Path(item.path)
            if not candidate.is_absolute():
                candidate = run_root / candidate
            try:
                resolved = candidate.resolve()
            except OSError:
                continue
            if (resolved == run_root or run_root in resolved.parents) and resolved.exists():
                return True
    return False


def _observed_commands(events: tuple[Mapping[str, Any], ...]) -> tuple[str, ...]:
    result: list[str] = []
    for event in events:
        item = event.get("item")
        if not isinstance(item, Mapping):
            continue
        if item.get("type") not in {"command_execution", "command"}:
            continue
        command = item.get("command")
        if isinstance(command, list):
            result.append(_normalize_command(tuple(str(part) for part in command)))
        elif command is not None:
            result.append(" ".join(str(command).split()))
    return tuple(result)


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
