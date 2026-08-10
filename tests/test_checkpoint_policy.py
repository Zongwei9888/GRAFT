from __future__ import annotations

import unittest

from graft.codex.checkpoint_policy import DefaultCheckpointPolicy
from graft.codex.session_state import SessionState
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
            last_assistant_message="Implemented the change.",
            stop_hook_active=False,
        )
        self.assertEqual(action.kind, "no_op")

    def test_completion_with_change_verifies(self) -> None:
        action = self.policy.evaluate(
            self.state,
            snapshot("new"),
            mode="completion",
            last_assistant_message="Implemented the change and tests pass.",
            stop_hook_active=False,
        )
        self.assertEqual(action.kind, "verify")

    def test_waiting_for_user_is_noop(self) -> None:
        action = self.policy.evaluate(
            self.state,
            snapshot("new"),
            mode="completion",
            last_assistant_message="Please confirm the intended behavior?",
            stop_hook_active=False,
        )
        self.assertEqual(action.kind, "no_op")

    def test_waiting_for_user_wins_even_without_a_workspace_change(self) -> None:
        action = self.policy.evaluate(
            self.state,
            snapshot("old"),
            mode="completion",
            last_assistant_message="I need clarification: which behavior should change?",
            stop_hook_active=False,
        )
        self.assertEqual(action.reason, "assistant_awaiting_user")

    def test_completed_read_only_turn_becomes_an_epoch_boundary(self) -> None:
        action = self.policy.evaluate(
            self.state,
            snapshot("old"),
            mode="completion",
            last_assistant_message="The repository review is completed.",
            stop_hook_active=False,
        )
        self.assertEqual(action.reason, "read_only_completion")

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
