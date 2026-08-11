from __future__ import annotations

import json
import subprocess
import time
from pathlib import Path
from typing import Any, Sequence

from graft.schema import RunConfig, TurnResult


class CodexExecutionError(RuntimeError):
    pass


class CliCodexRunner:
    """Codex CLI adapter with JSONL audit capture and session continuation."""

    def __init__(self, executable: Sequence[str] = ("codex",)) -> None:
        if not executable:
            raise ValueError("Codex executable may not be empty")
        self.executable = tuple(executable)

    def start_thread(
        self, task: str, repo: Path, config: RunConfig = RunConfig()
    ) -> TurnResult:
        command = [*self.executable, "exec"]
        command.extend(
            self._common_args(
                config, repo=repo, include_sandbox=True, include_color=True
            )
        )
        command.append("-")
        return self._invoke(command, task, repo, config.timeout_s)

    def continue_thread(
        self,
        thread_id: str,
        feedback: str,
        repo: Path,
        config: RunConfig = RunConfig(),
    ) -> TurnResult:
        if not thread_id:
            raise ValueError("thread_id is required for continuation")
        command = [*self.executable, "exec", "resume"]
        command.extend(
            self._common_args(
                config, repo=None, include_sandbox=False, include_color=False
            )
        )
        command.extend((thread_id, "-"))
        return self._invoke(command, feedback, repo, config.timeout_s)

    @staticmethod
    def parse_events(stdout: str) -> tuple[dict[str, Any], ...]:
        events: list[dict[str, Any]] = []
        for line_number, line in enumerate(stdout.splitlines(), start=1):
            if not line.strip():
                continue
            try:
                value = json.loads(line)
            except json.JSONDecodeError as exc:
                raise CodexExecutionError(
                    f"Invalid Codex JSONL at line {line_number}: {exc}"
                ) from exc
            if not isinstance(value, dict):
                raise CodexExecutionError(
                    f"Codex JSONL line {line_number} is not an object"
                )
            events.append(value)
        return tuple(events)

    def _common_args(
        self,
        config: RunConfig,
        *,
        repo: Path | None,
        include_sandbox: bool,
        include_color: bool,
    ) -> list[str]:
        args = ["--json"]
        if include_color:
            args.extend(("--color", "never"))
        if repo is not None:
            args.extend(("-C", str(repo.resolve())))
        if include_sandbox:
            args.extend(("--sandbox", config.sandbox))
        if config.model:
            args.extend(("--model", config.model))
        if config.ephemeral:
            args.append("--ephemeral")
        if config.isolate_config:
            args.extend(("--ignore-user-config", "--ignore-rules"))
        if config.disable_hooks:
            args.extend(("--disable", "hooks"))
        if config.skip_git_repo_check:
            args.append("--skip-git-repo-check")
        if config.output_schema:
            args.extend(("--output-schema", str(config.output_schema.resolve())))
        return args

    def _invoke(
        self, command: list[str], prompt: str, repo: Path, timeout_s: float
    ) -> TurnResult:
        started = time.monotonic()
        try:
            completed = subprocess.run(
                command,
                input=prompt,
                cwd=repo,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=timeout_s,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            raise CodexExecutionError(
                f"Codex timed out after {timeout_s:.1f}s"
            ) from exc
        except OSError as exc:
            raise CodexExecutionError(f"Could not start Codex: {exc}") from exc

        duration = time.monotonic() - started
        events = self.parse_events(completed.stdout)
        thread_id: str | None = None
        final_response = ""
        usage: dict[str, Any] = {}
        for event in events:
            if event.get("type") == "thread.started":
                thread_id = str(event.get("thread_id"))
            if event.get("type") == "turn.completed":
                raw_usage = event.get("usage", {})
                if isinstance(raw_usage, dict):
                    usage = dict(raw_usage)
            if event.get("type") == "item.completed":
                item = event.get("item", {})
                if isinstance(item, dict) and item.get("type") == "agent_message":
                    final_response = str(item.get("text", ""))

        return TurnResult(
            thread_id=thread_id,
            final_response=final_response,
            events=events,
            usage=usage,
            return_code=completed.returncode,
            stderr=completed.stderr,
            duration_s=duration,
        )
