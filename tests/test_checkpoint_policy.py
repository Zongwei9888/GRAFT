from __future__ import annotations

import unittest

from graft.codex.checkpoint_policy import DefaultCheckpointPolicy
from graft.codex.session_state import PromptRecord, SessionState, prompt_hash
from graft.schema import SourceSnapshot


def snapshot(tree_hash: str, checkpoint_key: str = "key") -> SourceSnapshot:
    return SourceSnapshot("/tmp", tree_hash, "r", "c", checkpoint_key, (), "now")


class CheckpointPolicyTests(unittest.TestCase):
    def setUp(self) -> None:
        self.policy = DefaultCheckpointPolicy()
        self.state = SessionState("session", baseline_tree_hash="old")

    def test_no_workspace_change_is_noop(self) -> None:
        action = self.policy.evaluate(
            self.state,
            snapshot("old"),
            mode="completion",
            last_assistant_message="any producer message",
            stop_hook_active=False,
        )
        self.assertEqual(action.reason, "no_workspace_change")

    def test_changed_stop_boundary_verifies_without_language_markers(self) -> None:
        action = self.policy.evaluate(
            self.state,
            snapshot("new"),
            mode="completion",
            last_assistant_message="这段自然语言不需要匹配任何完成关键词",
            stop_hook_active=False,
        )
        self.assertEqual(action.kind, "verify")
        self.assertEqual(action.reason, "workspace_changed_at_stop_boundary")

    def test_producer_question_does_not_classify_task_semantics(self) -> None:
        action = self.policy.evaluate(
            self.state,
            snapshot("new"),
            mode="completion",
            last_assistant_message="Should this be considered complete?",
            stop_hook_active=False,
        )
        self.assertEqual(action.kind, "verify")

    def test_explicit_mode_uses_only_a_protocol_token(self) -> None:
        requirement = "Do work [graft:verify]"
        self.state.prompts.append(
            PromptRecord(requirement, prompt_hash(requirement), "user")
        )
        action = self.policy.evaluate(
            self.state,
            snapshot("new"),
            mode="explicit",
            last_assistant_message=None,
            stop_hook_active=False,
        )
        self.assertEqual(action.reason, "explicit_checkpoint")

    def test_unchanged_continuation_does_not_loop(self) -> None:
        self.state.last_blocked_tree_hash = "new"
        action = self.policy.evaluate(
            self.state,
            snapshot("new"),
            mode="completion",
            last_assistant_message="Done.",
            stop_hook_active=True,
        )
        self.assertEqual(action.reason, "unchanged_after_graft_feedback")


if __name__ == "__main__":
    unittest.main()
