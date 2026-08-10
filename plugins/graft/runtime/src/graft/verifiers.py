from __future__ import annotations

import json
import subprocess
import time
from pathlib import Path

from graft.codex.cli_runner import CliCodexRunner, CodexExecutionError
from graft.evidence.snapshot import freeze_source
from graft.schema import (
    EvidenceItem,
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
        config_path: Path,
        verdict_schema: Path,
    ) -> VerifierResult:
        if spec.kind == "command":
            return self._run_command(spec, snapshot)
        if spec.kind == "codex_review":
            return self._run_codex_review(
                spec,
                snapshot,
                requirements=requirements,
                config_path=config_path,
                verdict_schema=verdict_schema,
            )
        raise ValueError(f"Unsupported verifier kind: {spec.kind}")

    def _run_command(
        self, spec: VerifierSpec, snapshot: SourceSnapshot
    ) -> VerifierResult:
        repo = Path(snapshot.root)
        cwd = (
            (repo / spec.working_directory).resolve()
            if spec.working_directory
            else repo.resolve()
        )
        if cwd != repo.resolve() and repo.resolve() not in cwd.parents:
            return self._error_result(
                spec,
                snapshot,
                0.0,
                f"Verifier working_directory escapes the workspace: {spec.working_directory}",
            )
        command = tuple(part.replace("{repo}", str(repo)) for part in spec.command)
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
            summary = "Command completed successfully."
            reproducible = True
        elif completed.returncode in spec.failure_exit_codes:
            verdict = Verdict.FAIL
            summary = _last_nonempty_line(stderr, stdout) or (
                f"Command exited with {completed.returncode}."
            )
            reproducible = True
        else:
            verdict = Verdict.ERROR
            summary = f"Unexpected exit code {completed.returncode}."
            reproducible = False

        evidence = ()
        if verdict == Verdict.FAIL:
            evidence = (
                EvidenceItem(
                    kind="command",
                    observation=summary,
                    command=command,
                ),
            )
        return VerifierResult(
            verifier_id=spec.verifier_id,
            verdict=verdict,
            summary=summary,
            source_hash=snapshot.checkpoint_key,
            blocking=spec.blocking,
            reproducible=reproducible,
            duration_s=duration,
            evidence=evidence,
            command=command,
            stdout=stdout,
            stderr=stderr,
            exit_code=completed.returncode,
            lineage=spec.lineage,
        )

    def _run_codex_review(
        self,
        spec: VerifierSpec,
        snapshot: SourceSnapshot,
        *,
        requirements: tuple[str, ...],
        config_path: Path,
        verdict_schema: Path,
    ) -> VerifierResult:
        prompt = (spec.prompt_template or _default_review_prompt()).format(
            requirements="\n".join(f"- {item}" for item in requirements),
            source_hash=snapshot.checkpoint_key,
            repo_root=snapshot.root,
        )
        started = time.monotonic()
        try:
            turn = self.codex_runner.start_thread(
                prompt,
                Path(snapshot.root),
                RunConfig(
                    sandbox="read-only",
                    model=spec.model,
                    timeout_s=spec.timeout_s,
                    ephemeral=True,
                    output_schema=verdict_schema,
                    isolate_config=True,
                    disable_hooks=True,
                ),
            )
        except CodexExecutionError as exc:
            return self._error_result(
                spec,
                snapshot,
                time.monotonic() - started,
                str(exc),
            )

        if turn.return_code != 0:
            detail = _turn_error(turn.events) or _trim(turn.stderr)
            return self._error_result(
                spec,
                snapshot,
                turn.duration_s,
                f"Codex exited with {turn.return_code}: {detail or 'unknown error'}",
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
                f"Invalid Codex reviewer verdict: {exc}",
                stdout=_trim(turn.final_response),
                stderr=_trim(turn.stderr),
            )

        after = freeze_source(
            Path(snapshot.root),
            requirements=requirements,
            config_path=config_path,
        )
        if after.tree_hash != snapshot.tree_hash:
            return self._error_result(
                spec,
                snapshot,
                turn.duration_s,
                "Read-only Codex reviewer changed the source tree.",
            )

        evidence = tuple(
            EvidenceItem(
                kind=str(item.get("kind", "code")),
                observation=str(item.get("observation", "")),
                path=str(item["path"]) if item.get("path") else None,
                line=int(item["line"]) if item.get("line") else None,
                command=tuple(str(value) for value in item.get("command", [])),
            )
            for item in raw.get("evidence", [])
            if isinstance(item, dict)
        )
        summary = str(raw.get("summary", "Codex review completed."))
        return VerifierResult(
            verifier_id=spec.verifier_id,
            verdict=verdict,
            summary=summary,
            source_hash=snapshot.checkpoint_key,
            blocking=spec.blocking,
            reproducible=False,
            duration_s=turn.duration_s,
            evidence=evidence,
            stdout=_trim(turn.final_response),
            stderr=_trim(turn.stderr),
            exit_code=turn.return_code,
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


def _default_review_prompt() -> str:
    return """You are an independent, read-only verifier. Review the current repository state
against the raw requirements below. Do not modify files. Do not trust the producer's summary.
Report only concrete, actionable failures grounded in repository evidence. If you cannot establish
a failure, return pass or abstain. A model suspicion is not mechanically reproducible evidence.

Requirements:
{requirements}

Checkpoint key: {source_hash}
Repository root: {repo_root}

Treat the current working tree as the complete review target even if it has no commit baseline.
Do not read or search parent directories. Limit inspection to files under the repository root, and
prefer src/, tests/, schemas/, configs/, and README.md. Keep commands targeted; never use `find ..`.
"""


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


def _turn_error(events) -> str:
    for event in reversed(events):
        if event.get("type") == "turn.failed":
            error = event.get("error", {})
            if isinstance(error, dict) and error.get("message"):
                return str(error["message"])
        if event.get("type") == "error" and event.get("message"):
            return str(event["message"])
    return ""
