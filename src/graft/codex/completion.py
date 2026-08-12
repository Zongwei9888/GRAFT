from __future__ import annotations

import json
from importlib.resources import files
from pathlib import Path
from typing import Any, Mapping, Protocol

from graft.codex.cli_runner import CliCodexRunner, CodexExecutionError
from graft.costing import stage_cost_from_turn
from graft.evidence.snapshot import freeze_source
from graft.registry import CompletionGateConfig
from graft.schema import (
    CompletionAssessment,
    CompletionState,
    ProducerEvidenceSummary,
    RunConfig,
    SourceSnapshot,
    to_jsonable,
)


class CompletionGateError(RuntimeError):
    pass


class CompletionGate(Protocol):
    def assess(
        self,
        snapshot: SourceSnapshot,
        requirements: tuple[str, ...],
        *,
        last_assistant_message: str | None,
        producer_evidence: ProducerEvidenceSummary,
        config: CompletionGateConfig,
        config_path: Path,
        environment_fingerprint: str,
    ) -> CompletionAssessment: ...


class CodexCompletionGate:
    """Classify lifecycle readiness without treating the producer claim as evidence."""

    def __init__(self, *, codex_runner: CliCodexRunner | None = None) -> None:
        self.codex_runner = codex_runner or CliCodexRunner()

    def assess(
        self,
        snapshot: SourceSnapshot,
        requirements: tuple[str, ...],
        *,
        last_assistant_message: str | None,
        producer_evidence: ProducerEvidenceSummary,
        config: CompletionGateConfig,
        config_path: Path,
        environment_fingerprint: str,
    ) -> CompletionAssessment:
        schema = Path(
            str(files("graft").joinpath("resources", "completion_decision.schema.json"))
        )
        try:
            turn = self.codex_runner.start_thread(
                _completion_prompt(
                    snapshot,
                    requirements,
                    last_assistant_message=last_assistant_message,
                    producer_evidence=producer_evidence,
                ),
                Path(snapshot.root),
                RunConfig(
                    sandbox="read-only",
                    model=config.stage.model,
                    timeout_s=config.stage.timeout_s,
                    ephemeral=True,
                    output_schema=schema,
                    isolate_config=True,
                    disable_hooks=True,
                    skip_git_repo_check=True,
                ),
            )
        except CodexExecutionError as exc:
            raise CompletionGateError(str(exc)) from exc
        if turn.return_code != 0:
            raise CompletionGateError(
                f"{config.stage.prompt_family} exited with {turn.return_code}: "
                f"{_turn_error(turn.events) or turn.stderr.strip() or 'unknown error'}"
            )
        try:
            raw = json.loads(turn.final_response)
            state = CompletionState(str(raw["state"]))
            confidence = float(raw["confidence"])
            reason = str(raw["reason"]).strip()
        except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
            raise CompletionGateError(f"invalid completion assessment: {exc}") from exc
        if not 0 <= confidence <= 1 or not reason:
            raise CompletionGateError("completion assessment has invalid confidence or reason")
        after = freeze_source(
            Path(snapshot.root),
            requirements=requirements,
            config_path=config_path,
            environment_fingerprint=environment_fingerprint,
            baseline_tree_hash=snapshot.baseline_tree_hash,
            baseline_files=snapshot.baseline_files,
            baseline_file_hashes=snapshot.baseline_file_hashes,
            baseline_archive_path=snapshot.baseline_archive_path,
        )
        if after.tree_hash != snapshot.tree_hash:
            raise CompletionGateError("read-only completion gate changed the producer workspace")
        if confidence < config.min_confidence:
            state = CompletionState.ABSTAIN
            reason = (
                f"Completion confidence {confidence:.3f} is below the configured "
                f"minimum {config.min_confidence:.3f}: {reason}"
            )
        return CompletionAssessment(
            state=state,
            confidence=confidence,
            reason=reason,
            stage_cost=stage_cost_from_turn(
                config.stage.prompt_family, "completion_gate", turn
            ),
        )


def _completion_prompt(
    snapshot: SourceSnapshot,
    requirements: tuple[str, ...],
    *,
    last_assistant_message: str | None,
    producer_evidence: ProducerEvidenceSummary,
) -> str:
    requirements_text = "\n".join(
        f"- R{index}: {item}" for index, item in enumerate(requirements, start=1)
    ) or "- <missing>"
    return f"""You are GRAFT's lifecycle completion classifier.

Decide only whether the current Codex turn presents a stable candidate that is ready for external
verification. Do not judge whether the implementation is correct. The producer's last message is a
lifecycle signal, never correctness evidence. Use the full task epoch, observable changed state and
producer tool summary. Do not use language-, framework-, repository-, or benchmark-specific keyword
rules. Classify:

- candidate_complete: the turn presents an implementation/result as ready for delivery;
- intermediate: work is explicitly partial or another implementation step is pending;
- question: the producer is asking the user for information or a decision;
- explanation: the turn only explains/reviews and does not present new implementation work;
- blocked: the producer cannot complete because of an unresolved external blocker;
- abstain: available lifecycle evidence does not support a reliable classification.

Raw task-epoch requirements:
{requirements_text}

Checkpoint key: {snapshot.checkpoint_key}
Tree changed from baseline: {snapshot.tree_hash != snapshot.baseline_tree_hash}
Visible entries: {len(snapshot.files)}

Producer evidence summary:
{json.dumps(to_jsonable(producer_evidence), ensure_ascii=False, indent=2)}

Last producer message:
{last_assistant_message or '<unavailable>'}

Return only the schema-conforming object. Keep the reason concise and refer to lifecycle evidence,
not implementation correctness.
"""


def _turn_error(events: tuple[Mapping[str, Any], ...]) -> str:
    for event in reversed(events):
        if event.get("type") == "turn.failed":
            error = event.get("error", {})
            if isinstance(error, Mapping) and error.get("message"):
                return str(error["message"])
        if event.get("type") == "error" and event.get("message"):
            return str(event["message"])
    return ""
