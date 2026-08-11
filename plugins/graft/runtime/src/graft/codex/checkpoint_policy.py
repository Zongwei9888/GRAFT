from __future__ import annotations

from dataclasses import dataclass

from graft.schema import SourceSnapshot

from .session_state import SessionState


@dataclass(frozen=True)
class CheckpointAction:
    kind: str
    reason: str


class DefaultCheckpointPolicy:
    """Recognize state boundaries without classifying natural-language task content."""

    EXPLICIT_PROTOCOL_TOKEN = "[graft:verify]"

    def evaluate(
        self,
        state: SessionState,
        snapshot: SourceSnapshot,
        *,
        mode: str,
        last_assistant_message: str | None,
        stop_hook_active: bool,
    ) -> CheckpointAction:
        del last_assistant_message  # A producer claim is not verification evidence.
        if state.baseline_tree_hash is None:
            return CheckpointAction("no_op", "missing_prompt_baseline")
        if snapshot.checkpoint_key == state.last_verified_checkpoint_key:
            return CheckpointAction("no_op", "checkpoint_already_verified")
        if snapshot.tree_hash == state.baseline_tree_hash:
            return CheckpointAction("no_op", "no_workspace_change")
        if stop_hook_active and snapshot.tree_hash == state.last_blocked_tree_hash:
            return CheckpointAction("no_op", "unchanged_after_graft_feedback")
        if mode == "explicit":
            requested = any(
                self.EXPLICIT_PROTOCOL_TOKEN in requirement.lower()
                for requirement in state.requirements
            )
            if not requested:
                return CheckpointAction("no_op", "explicit_checkpoint_not_requested")
            return CheckpointAction("verify", "explicit_checkpoint")
        if mode not in {"completion", "strict"}:
            raise ValueError(f"Unsupported checkpoint mode: {mode}")
        return CheckpointAction("verify", "workspace_changed_at_stop_boundary")
