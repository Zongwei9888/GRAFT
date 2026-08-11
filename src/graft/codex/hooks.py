from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from graft.codex.checkpoint_policy import DefaultCheckpointPolicy
from graft.codex.event_dedup import claim_event
from graft.codex.session_state import SessionStateStore, prompt_hash
from graft.configuration import resolve_config
from graft.controller import GraftController
from graft.evidence.snapshot import hash_tree, hash_tree_manifest
from graft.runtime_paths import resolve_workspace, workspace_runtime_paths
from graft.schema import DecisionKind


def _read_event() -> dict[str, Any]:
    value = json.load(sys.stdin)
    if not isinstance(value, dict):
        raise ValueError("Hook input must be a JSON object")
    return value


def _emit(value: dict[str, Any]) -> int:
    print(json.dumps(value, ensure_ascii=False))
    return 0


def user_prompt_submit() -> int:
    event = _read_event()
    workspace = resolve_workspace(Path(str(event["cwd"])))
    paths = workspace_runtime_paths(workspace)
    if not claim_event(paths.events_dir, "UserPromptSubmit", event):
        return _emit({"continue": True})
    resolution = resolve_config(workspace)
    session_id = str(event.get("session_id", "unknown"))
    prompt = str(event.get("prompt", ""))
    tree_hash, files, file_hashes = hash_tree_manifest(workspace)
    store = SessionStateStore(workspace, root=paths.state_dir)
    state = store.load(session_id)
    store.record_prompt(state, prompt, tree_hash, files, file_hashes)
    return _emit({"continue": True})


def post_tool_use() -> int:
    event = _read_event()
    workspace = resolve_workspace(Path(str(event["cwd"])))
    paths = workspace_runtime_paths(workspace)
    if not claim_event(paths.events_dir, "PostToolUse", event):
        return _emit({"continue": True})
    session_id = str(event.get("session_id", "unknown"))
    telemetry_dir = paths.telemetry_dir
    telemetry_dir.mkdir(parents=True, exist_ok=True)
    tool_input = event.get("tool_input")
    tool_response = event.get("tool_response")
    record = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "session_id": session_id,
        "turn_id": event.get("turn_id"),
        "tool_name": event.get("tool_name"),
        "tool_use_id": event.get("tool_use_id"),
        "tool_input_hash": _hash_json(tool_input),
        "tool_response_hash": _hash_json(tool_response),
    }
    safe = "".join(character if character.isalnum() else "_" for character in session_id)
    with (telemetry_dir / f"{safe or 'unknown'}.jsonl").open(
        "a", encoding="utf-8"
    ) as handle:
        handle.write(json.dumps(record, ensure_ascii=False) + "\n")
    return _emit({"continue": True})


def stop() -> int:
    event = _read_event()
    workspace = resolve_workspace(Path(str(event["cwd"])))
    paths = workspace_runtime_paths(workspace)
    if not claim_event(paths.events_dir, "Stop", event):
        return _emit({"continue": True})
    try:
        session_id = str(event.get("session_id", "unknown"))
        store = SessionStateStore(workspace, root=paths.state_dir)
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
        if state.verification_round >= config.max_feedback_rounds:
            state.status = "unresolved"
            store.save(state)
            return _emit(
                {
                    "continue": True,
                    "systemMessage": "GRAFT verification budget exhausted; result is unresolved.",
                }
            )

        decision = controller.verify(
            workspace,
            requirements=state.requirements,
            session_id=session_id,
            snapshot=snapshot,
        )
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


def _hash_json(value: Any) -> str:
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, default=str).encode(
        "utf-8"
    )
    return hashlib.sha256(encoded).hexdigest()


def main(argv: list[str] | None = None) -> int:
    arguments = argv if argv is not None else sys.argv[1:]
    parser = argparse.ArgumentParser(prog="graft-hook")
    parser.add_argument("event", choices=("user-prompt", "post-tool", "stop"))
    parser.add_argument("--installation-id", default="manual")
    parsed = parser.parse_args(arguments)
    handlers = {
        "user-prompt": user_prompt_submit,
        "post-tool": post_tool_use,
        "stop": stop,
    }
    return handlers[parsed.event]()


if __name__ == "__main__":
    raise SystemExit(main())
