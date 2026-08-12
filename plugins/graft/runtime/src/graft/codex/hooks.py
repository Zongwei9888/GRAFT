from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any

from graft.codex.checkpoint_policy import DefaultCheckpointPolicy
from graft.codex.completion import CodexCompletionGate, CompletionGateError
from graft.codex.event_dedup import claim_event
from graft.codex.runtime_authority import (
    RuntimeIdentity,
    current_runtime_identity,
    resolve_runtime_authority,
)
from graft.codex.session_state import SessionState, SessionStateStore, prompt_hash
from graft.codex.telemetry import ProducerEvidenceLedger, record_from_hook_event
from graft.cost_history import CostHistoryStore
from graft.costing import stage_cost_from_result
from graft.configuration import resolve_config
from graft.controller import GraftController
from graft.evidence.baseline_archive import archive_baseline
from graft.evidence.checkpoint_archive import archive_checkpoint
from graft.evidence.snapshot import hash_tree, hash_tree_manifest
from graft.runtime_paths import resolve_workspace, workspace_runtime_paths
from graft.schema import (
    CompletionState,
    Decision,
    DecisionKind,
    PromotionRequirement,
    Verdict,
)


def _read_event() -> dict[str, Any]:
    value = json.load(sys.stdin)
    if not isinstance(value, dict):
        raise ValueError("Hook input must be a JSON object")
    return value


def _emit(value: dict[str, Any]) -> int:
    print(json.dumps(value, ensure_ascii=False))
    return 0


def user_prompt_submit(*, runtime_identity: RuntimeIdentity | None = None) -> int:
    event = _read_event()
    workspace = resolve_workspace(Path(str(event["cwd"])))
    paths = workspace_runtime_paths(workspace)
    identity = runtime_identity or current_runtime_identity("manual")
    if not claim_event(
        paths.events_dir,
        "UserPromptSubmit",
        event,
        runtime_identity=identity.to_dict(),
    ):
        return _emit({"continue": True})
    session_id = str(event.get("session_id", "unknown"))
    prompt = str(event.get("prompt", ""))
    tree_hash, files, file_hashes = hash_tree_manifest(workspace)
    store = SessionStateStore(
        workspace,
        root=paths.state_dir,
        writer_runtime=identity.to_dict(),
    )
    state = store.load(session_id)
    origin = store.record_prompt(state, prompt, tree_hash, files, file_hashes)
    if (
        origin == "user"
        and not state.baseline_archive_path
        and state.baseline_tree_hash == tree_hash
        and state.baseline_file_hashes == file_hashes
    ):
        try:
            state.baseline_archive_path = str(
                archive_baseline(
                    workspace,
                    files=files,
                    file_hashes=file_hashes,
                    tree_hash=tree_hash,
                    archive_root=paths.baselines_dir,
                    session_id=session_id,
                    task_epoch=state.task_epoch,
                )
            )
            store.save(state)
        except (OSError, ValueError):
            # Baseline content improves semantic comparison but must not break
            # the producer loop when external state storage is unavailable.
            pass
    return _emit({"continue": True})


def post_tool_use(*, runtime_identity: RuntimeIdentity | None = None) -> int:
    event = _read_event()
    workspace = resolve_workspace(Path(str(event["cwd"])))
    paths = workspace_runtime_paths(workspace)
    identity = runtime_identity or current_runtime_identity("manual")
    if not claim_event(
        paths.events_dir,
        "PostToolUse",
        event,
        runtime_identity=identity.to_dict(),
    ):
        return _emit({"continue": True})
    session_id = str(event.get("session_id", "unknown"))
    state = SessionStateStore(workspace, root=paths.state_dir).load(session_id)
    record = record_from_hook_event(event, task_epoch=state.task_epoch)
    ProducerEvidenceLedger(paths.telemetry_dir, session_id).append(record)
    return _emit({"continue": True})


def stop(*, runtime_identity: RuntimeIdentity | None = None) -> int:
    event = _read_event()
    workspace = resolve_workspace(Path(str(event["cwd"])))
    paths = workspace_runtime_paths(workspace)
    identity = runtime_identity or current_runtime_identity("manual")
    if not claim_event(
        paths.events_dir,
        "Stop",
        event,
        runtime_identity=identity.to_dict(),
    ):
        return _emit({"continue": True})
    try:
        session_id = str(event.get("session_id", "unknown"))
        store = SessionStateStore(
            workspace,
            root=paths.state_dir,
            writer_runtime=identity.to_dict(),
        )
        state = store.load(session_id)
        resolution = resolve_config(workspace)
        config = resolution.load()
        controller = GraftController(
            config,
            config_path=resolution.path,
            report_root=paths.reports_dir,
        )
        snapshot = controller.snapshot(
            workspace,
            state.requirements,
            baseline_tree_hash=state.baseline_tree_hash,
            baseline_files=tuple(state.baseline_files),
            baseline_file_hashes=state.baseline_file_hashes,
            baseline_archive_path=state.baseline_archive_path,
        )
        action = DefaultCheckpointPolicy().evaluate(
            state,
            snapshot,
            mode=config.checkpoint_mode,
            last_assistant_message=event.get("last_assistant_message"),
            stop_hook_active=bool(event.get("stop_hook_active", False)),
        )
        if action.kind == "no_op":
            if action.reason == "checkpoint_already_verified":
                state.status = "accepted"
            elif action.reason == "unchanged_after_graft_feedback":
                state.status = "unresolved"
            store.save(state)
            return _emit({"continue": True})

        evidence_ledger = ProducerEvidenceLedger(paths.telemetry_dir, session_id)
        producer_evidence = evidence_ledger.summarize(task_epoch=state.task_epoch)
        if (
            config.selection.strategy == "value-aware"
            and config.checkpoint_mode == "completion"
        ):
            if config.completion_gate is None:
                raise ValueError("value-aware completion mode requires a completion gate")
            try:
                assessment = CodexCompletionGate().assess(
                    snapshot,
                    state.requirements,
                    last_assistant_message=event.get("last_assistant_message"),
                    producer_evidence=producer_evidence,
                    config=config.completion_gate,
                    config_path=resolution.path,
                    environment_fingerprint=config.environment_fingerprint,
                )
            except CompletionGateError as exc:
                state.status = "unresolved"
                store.save(state)
                return _emit(
                    {
                        "continue": True,
                        "systemMessage": f"GRAFT completion gate abstained: {exc}",
                    }
                )
            if assessment.stage_cost is not None:
                state.stage_costs.append(assessment.stage_cost)
                state.spent_wall_time_s += assessment.stage_cost.duration_s
                _record_model_cost(state, assessment.stage_cost)
            if assessment.state != CompletionState.CANDIDATE_COMPLETE:
                store.save(state)
                return _emit({"continue": True})

        available_budget = config.budget
        available_wall_time_s: float | None = None
        available_model_cost_usd: float | None = None
        if config.selection.strategy == "value-aware":
            available_budget = max(0.0, config.budget - state.spent_budget)
            available_wall_time_s = max(
                0.0,
                config.selection.wall_time_budget_s - state.spent_wall_time_s,
            )
            available_model_cost_usd = max(
                0.0,
                config.selection.model_cost_budget_usd - state.spent_model_cost_usd,
            )
            if available_budget <= 1e-12 or available_wall_time_s <= 1e-12:
                state.status = "unresolved"
                store.save(state)
                return _emit(
                    {
                        "continue": True,
                        "systemMessage": "GRAFT task-epoch verification resource budget is exhausted.",
                    }
                )

        archive_checkpoint(
            snapshot,
            session_id=session_id,
            task_epoch=state.task_epoch,
            verification_round=state.verification_round,
        )
        if state.verification_round >= config.max_feedback_rounds:
            state.status = "unresolved"
            store.save(state)
            return _emit(
                {
                    "continue": True,
                    "systemMessage": "GRAFT verification budget exhausted; result is unresolved.",
                }
            )

        verification_started = time.monotonic()
        cost_history = CostHistoryStore(paths.cost_history_dir)
        decision = controller.verify(
            workspace,
            requirements=state.requirements,
            session_id=session_id,
            snapshot=snapshot,
            producer_evidence=producer_evidence,
            available_budget=available_budget,
            promotion=(
                state.pending_promotion
                if config.selection.strategy == "value-aware"
                else None
            ),
            historical_costs=(
                cost_history.estimates()
                if config.selection.strategy == "value-aware"
                else None
            ),
            available_wall_time_s=available_wall_time_s,
            available_model_cost_usd=available_model_cost_usd,
        )
        if config.selection.strategy == "value-aware":
            try:
                cost_history.record(decision)
            except OSError:
                # Cost calibration is advisory. Losing one observation must not alter
                # a source-bound verification decision or the producer lifecycle.
                pass
        state.spent_wall_time_s += time.monotonic() - verification_started
        _record_decision_costs(
            state,
            decision,
            value_aware=config.selection.strategy == "value-aware",
        )
        if decision.promotion_outcome is not None:
            state.promotion_status = decision.promotion_outcome.value
        if decision.kind == DecisionKind.CONTINUE_WITH_EVIDENCE:
            feedback_digest = prompt_hash(decision.reason)
            if (
                state.last_blocked_tree_hash == snapshot.tree_hash
                and state.last_feedback_hash == feedback_digest
            ):
                state.status = "unresolved"
                store.save(state)
                return _emit(
                    {
                        "continue": True,
                        "systemMessage": "GRAFT suppressed repeated feedback for an unchanged workspace.",
                    }
                )
            state.last_blocked_tree_hash = snapshot.tree_hash
            state.last_feedback_hash = feedback_digest
            state.pending_feedback_hash = feedback_digest
            if config.selection.strategy == "value-aware":
                state.pending_promotion = _promotion_from_decision(decision)
                if decision.promotion_outcome is None:
                    state.promotion_status = "pending"
            state.verification_round += 1
            state.status = "active"
            store.save(state)
            return _emit({"decision": "block", "reason": decision.reason})

        if decision.kind == DecisionKind.ALLOW:
            state.last_verified_checkpoint_key = snapshot.checkpoint_key
            state.baseline_tree_hash = snapshot.tree_hash
            state.baseline_files = list(snapshot.files)
            state.baseline_file_hashes = dict(snapshot.file_hashes)
            state.verification_round = 0
            state.pending_promotion = None
            state.status = "accepted"
            store.save(state)
            return _emit({"continue": True})

        state.status = "unresolved"
        store.save(state)
        if config.failure_policy == "closed":
            reason = (
                "[GRAFT Unresolved Verification]\n"
                + decision.reason
                + f"\nReport: {decision.report_path}"
            )
            state.pending_feedback_hash = prompt_hash(reason)
            state.verification_round += 1
            store.save(state)
            return _emit({"decision": "block", "reason": reason})
        return _emit({"continue": True, "systemMessage": decision.reason})
    except Exception as exc:  # Hook boundary must always produce valid JSON.
        return _emit(
            {
                "continue": True,
                "systemMessage": f"GRAFT hook failed open: {type(exc).__name__}: {exc}",
            }
        )


def main(argv: list[str] | None = None) -> int:
    arguments = argv if argv is not None else sys.argv[1:]
    parser = argparse.ArgumentParser(prog="graft-hook")
    parser.add_argument("event", choices=("user-prompt", "post-tool", "stop"))
    parser.add_argument("--installation-id", default="manual")
    parsed = parser.parse_args(arguments)
    identity = current_runtime_identity(parsed.installation_id)
    handlers = {
        "user-prompt": user_prompt_submit,
        "post-tool": post_tool_use,
        "stop": stop,
    }
    event = _read_event()
    workspace = resolve_workspace(Path(str(event["cwd"])))
    authority = resolve_runtime_authority(workspace, identity)
    if not authority.authoritative:
        return _emit({"continue": True})
    # Handler functions own stdin parsing for direct testability. Reconstruct a
    # JSON stream after authority discovery consumed the hook event.
    sys.stdin = _JsonEventStream(event)
    return handlers[parsed.event](runtime_identity=identity)


class _JsonEventStream:
    def __init__(self, event: dict[str, Any]) -> None:
        self._event = event

    def read(self, *args, **kwargs) -> str:
        return json.dumps(self._event, ensure_ascii=False)


def _record_decision_costs(
    state: SessionState, decision: Decision, *, value_aware: bool
) -> None:
    if decision.graph is not None:
        state.stage_costs.extend(decision.graph.stage_costs)
        for cost in decision.graph.stage_costs:
            _record_model_cost(state, cost)
    result_costs = tuple(stage_cost_from_result(item) for item in decision.results)
    state.stage_costs.extend(result_costs)
    for cost in result_costs:
        _record_model_cost(state, cost)
    if value_aware and decision.selection is not None:
        state.spent_budget += decision.selection.total_cost


def _record_model_cost(state: SessionState, cost) -> None:
    if cost.estimated_cost_usd is None:
        state.unknown_cost_stages += 1
    else:
        state.spent_model_cost_usd += cost.estimated_cost_usd


def _promotion_from_decision(decision: Decision) -> PromotionRequirement:
    graph = decision.graph
    if graph is None:
        return PromotionRequirement(
            feedback_checkpoint_key=decision.snapshot.checkpoint_key,
            report_path=decision.report_path,
            behavior_descriptions=(),
            failure_descriptions=(),
            evidence_observations=(),
            reproduction_commands=(),
        )
    behaviors = {item.behavior_id: item for item in graph.behaviors}
    failures = {item.failure_mode_id: item for item in graph.failure_modes}
    blocking = tuple(
        item
        for item in decision.results
        if item.verdict == Verdict.FAIL and item.blocking and item.reproducible
    )
    behavior_descriptions: list[str] = []
    failure_descriptions: list[str] = []
    observations: list[str] = []
    commands: list[tuple[str, ...]] = []
    eligible_origins = {
        "authoritative_runtime",
        "baseline_repository",
        "requirement_derived_runtime",
    }
    for result in blocking:
        for failure_id in result.failure_modes:
            failure = failures.get(failure_id)
            if failure is None:
                continue
            failure_descriptions.append(failure.description)
            behavior = behaviors.get(failure.behavior_id)
            if behavior is not None:
                behavior_descriptions.append(behavior.description)
        if result.command:
            commands.append(result.command)
        for item in result.evidence:
            if item.oracle_origin not in eligible_origins:
                continue
            if item.observation:
                observations.append(item.observation)
            if item.command:
                commands.append(item.command)
    return PromotionRequirement(
        feedback_checkpoint_key=decision.snapshot.checkpoint_key,
        report_path=decision.report_path,
        behavior_descriptions=tuple(dict.fromkeys(behavior_descriptions)),
        failure_descriptions=tuple(dict.fromkeys(failure_descriptions)),
        evidence_observations=tuple(dict.fromkeys(observations)),
        reproduction_commands=tuple(dict.fromkeys(commands)),
    )


if __name__ == "__main__":
    raise SystemExit(main())
