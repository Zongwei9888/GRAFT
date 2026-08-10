from __future__ import annotations

from dataclasses import dataclass

from graft.schema import SourceSnapshot

from .session_state import SessionState


@dataclass(frozen=True)
class CheckpointAction:
    kind: str
    reason: str


class DefaultCheckpointPolicy:
    COMPLETION_MARKERS = (
        "completed",
        "implemented",
        "fixed",
        "resolved",
        "tests pass",
        "done",
        "完成",
        "已实现",
        "已修复",
        "测试通过",
    )
    WAITING_MARKERS = (
        "need your input",
        "need clarification",
        "please confirm",
        "waiting for",
        "需要你",
        "请确认",
        "需要澄清",
        "等待",
    )
    EXPLICIT_MARKERS = ("[graft:verify]", "$graft-checkpoint")

    def evaluate(
        self,
        state: SessionState,
        snapshot: SourceSnapshot,
        *,
        mode: str,
        last_assistant_message: str | None,
        stop_hook_active: bool,
    ) -> CheckpointAction:
        if state.baseline_tree_hash is None:
            return CheckpointAction("no_op", "missing_prompt_baseline")
        if snapshot.checkpoint_key == state.last_verified_checkpoint_key:
            return CheckpointAction("no_op", "checkpoint_already_verified")

        requirements = "\n".join(state.requirements).lower()
        if mode == "explicit":
            if any(marker in requirements for marker in self.EXPLICIT_MARKERS):
                if snapshot.tree_hash != state.baseline_tree_hash:
                    return CheckpointAction("verify", "explicit_checkpoint")
                return CheckpointAction("no_op", "no_workspace_change")
            return CheckpointAction("no_op", "explicit_checkpoint_not_requested")
        if mode == "strict":
            if snapshot.tree_hash == state.baseline_tree_hash:
                return CheckpointAction("no_op", "no_workspace_change")
            if stop_hook_active and snapshot.tree_hash == state.last_blocked_tree_hash:
                return CheckpointAction("no_op", "unchanged_after_graft_feedback")
            return CheckpointAction("verify", "workspace_changed")

        message = (last_assistant_message or "").strip().lower()
        if any(marker in message for marker in self.WAITING_MARKERS) or message.endswith("?"):
            return CheckpointAction("no_op", "assistant_awaiting_user")
        completed = any(marker in message for marker in self.COMPLETION_MARKERS)
        if snapshot.tree_hash == state.baseline_tree_hash:
            reason = "read_only_completion" if completed else "no_workspace_change"
            return CheckpointAction("no_op", reason)
        if stop_hook_active and snapshot.tree_hash == state.last_blocked_tree_hash:
            return CheckpointAction("no_op", "unchanged_after_graft_feedback")
        if stop_hook_active:
            return CheckpointAction("verify", "graft_continuation_completed")
        if completed:
            return CheckpointAction("verify", "assistant_claimed_completion")
        return CheckpointAction("no_op", "completion_claim_not_detected")
