from __future__ import annotations

import io
import json
import os
import sys
import tarfile
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

from graft.codex import hooks
from graft.codex.session_state import SessionStateStore
from graft.configuration import trust_project_config
from graft.project_config import initialize_project
from graft.schema import Behavior, FailureMode, FeedbackGraph, VerifierSpec


class HookReplayTests(unittest.TestCase):
    def _config(self, root: Path) -> None:
        initialize_project(root)
        trust_project_config(root)

    def _graph(self, exit_code: int, source_hash: str) -> FeedbackGraph:
        verifier = VerifierSpec(
            verifier_id="task-specific-check",
            kind="command",
            cost=1,
            blocking=True,
            failure_modes=("f",),
            objective="execute a test-derived check",
            estimated_detection={"f": 1.0},
            command=(sys.executable, "-c", f"raise SystemExit({exit_code})"),
        )
        return FeedbackGraph(
            source_hash=source_hash,
            behaviors=(Behavior("b", "requested behavior", (), ("result",), 1, 1, 0),),
            failure_modes=(FailureMode("f", "b", "behavior fails", "task", (), (), 1),),
            verifiers=(verifier,),
            shared_blind_spots=(),
        )

    def _call(self, function, event: dict) -> dict:
        output = io.StringIO()
        with patch.object(sys, "stdin", io.StringIO(json.dumps(event))):
            with redirect_stdout(output):
                code = function()
        self.assertEqual(code, 0)
        return json.loads(output.getvalue())

    def _prompt(self, root: Path, session: str, prompt: str) -> dict:
        return self._call(
            hooks.user_prompt_submit,
            {
                "session_id": session,
                "turn_id": "turn-prompt",
                "cwd": str(root),
                "hook_event_name": "UserPromptSubmit",
                "prompt": prompt,
            },
        )

    def _stop(
        self, root: Path, session: str, *, exit_code: int, active: bool = False
    ) -> dict:
        with patch(
            "graft.controller.CodexFeedbackGraphBuilder.build",
            side_effect=lambda snapshot, requirements, config, *, config_path: self._graph(
                exit_code, snapshot.checkpoint_key
            ),
        ):
            return self._call(
                hooks.stop,
                {
                    "session_id": session,
                    "turn_id": "turn-stop",
                    "cwd": str(root),
                    "hook_event_name": "Stop",
                    "stop_hook_active": active,
                    "last_assistant_message": "arbitrary producer message",
                },
            )

    def test_passing_changed_checkpoint_is_allowed(self) -> None:
        with tempfile.TemporaryDirectory() as directory, tempfile.TemporaryDirectory() as state:
            root = Path(directory)
            with patch.dict(
                os.environ,
                {"GRAFT_STATE_HOME": state, "GRAFT_CONFIG_HOME": str(Path(state) / "config")},
            ):
                (root / "source.py").write_text("value = 1\n", encoding="utf-8")
                self._config(root)
                self._prompt(root, "session-pass", "Change the value")
                captured = SessionStateStore(root).load("session-pass")
                self.assertIn("source.py", captured.baseline_file_hashes)
                self.assertIsNotNone(captured.baseline_archive_path)
                self.assertTrue(Path(captured.baseline_archive_path).is_file())
                original_digest = captured.baseline_file_hashes["source.py"]
                (root / "source.py").write_text("value = 2\n", encoding="utf-8")
                result = self._stop(root, "session-pass", exit_code=0)
                self.assertTrue(result["continue"])
                session_state = SessionStateStore(root).load("session-pass")
                self.assertEqual(session_state.status, "accepted")
                self.assertIsNotNone(session_state.last_verified_checkpoint_key)
                self.assertNotEqual(
                    session_state.baseline_file_hashes["source.py"], original_digest
                )

    def test_failure_continues_and_does_not_repeat_without_change(self) -> None:
        with tempfile.TemporaryDirectory() as directory, tempfile.TemporaryDirectory() as state:
            root = Path(directory)
            with patch.dict(
                os.environ,
                {"GRAFT_STATE_HOME": state, "GRAFT_CONFIG_HOME": str(Path(state) / "config")},
            ):
                (root / "source.py").write_text("value = 1\n", encoding="utf-8")
                self._config(root)
                self._prompt(root, "session-fail", "Change the value")
                (root / "source.py").write_text("value = 2\n", encoding="utf-8")
                first = self._stop(root, "session-fail", exit_code=1)
                self.assertEqual(first["decision"], "block")
                self._prompt(root, "session-fail", first["reason"])
                session_state = SessionStateStore(root).load("session-fail")
                self.assertEqual(session_state.requirements, ("Change the value",))
                second = self._stop(
                    root, "session-fail", exit_code=1, active=True
                )
                self.assertTrue(second["continue"])
                self.assertNotIn("decision", second)

    def test_opt_in_archive_freezes_the_pre_feedback_checkpoint(self) -> None:
        with (
            tempfile.TemporaryDirectory() as directory,
            tempfile.TemporaryDirectory() as state,
            tempfile.TemporaryDirectory() as archives,
        ):
            root = Path(directory)
            with patch.dict(
                os.environ,
                {
                    "GRAFT_STATE_HOME": state,
                    "GRAFT_CONFIG_HOME": str(Path(state) / "config"),
                    "GRAFT_CHECKPOINT_ARCHIVE_HOME": archives,
                },
            ):
                source = root / "source.py"
                source.write_text("value = 1\n", encoding="utf-8")
                self._config(root)
                self._prompt(root, "session-archive", "Change the value")
                source.write_text("value = 2\n", encoding="utf-8")

                result = self._stop(root, "session-archive", exit_code=1)
                self.assertEqual(result["decision"], "block")

                captured = list(Path(archives).rglob("*.tar.gz"))
                self.assertEqual(len(captured), 1)
                checksum = captured[0].with_suffix(".gz.sha256")
                self.assertTrue(checksum.is_file())
                with tarfile.open(captured[0], "r:gz") as archive:
                    member = archive.extractfile("workspace/source.py")
                    self.assertIsNotNone(member)
                    self.assertEqual(member.read(), b"value = 2\n")
                    metadata_file = archive.extractfile("checkpoint.json")
                    self.assertIsNotNone(metadata_file)
                    metadata = json.load(metadata_file)
                self.assertEqual(metadata["verification_round"], 0)
                self.assertEqual(metadata["task_epoch"], 1)
                self.assertEqual(metadata["skipped_files"], [])

    def test_multi_turn_clarification_stays_in_one_task_epoch(self) -> None:
        with tempfile.TemporaryDirectory() as directory, tempfile.TemporaryDirectory() as state:
            root = Path(directory)
            with patch.dict(
                os.environ,
                {"GRAFT_STATE_HOME": state, "GRAFT_CONFIG_HOME": str(Path(state) / "config")},
            ):
                (root / "source.py").write_text("value = 1\n", encoding="utf-8")
                self._config(root)
                self._prompt(root, "session-multi", "Change the value")
                waiting = self._call(
                    hooks.stop,
                    {
                        "session_id": "session-multi",
                        "turn_id": "turn-waiting",
                        "cwd": str(root),
                        "hook_event_name": "Stop",
                        "stop_hook_active": False,
                        "last_assistant_message": "Which value should I use?",
                    },
                )
                self.assertTrue(waiting["continue"])
                self._prompt(root, "session-multi", "Use the value 2")
                session_state = SessionStateStore(root).load("session-multi")
                self.assertEqual(session_state.task_epoch, 1)
                self.assertEqual(
                    session_state.requirements,
                    ("Change the value", "Use the value 2"),
                )

    def test_new_prompt_after_acceptance_starts_a_new_task_epoch(self) -> None:
        with tempfile.TemporaryDirectory() as directory, tempfile.TemporaryDirectory() as state:
            root = Path(directory)
            with patch.dict(
                os.environ,
                {"GRAFT_STATE_HOME": state, "GRAFT_CONFIG_HOME": str(Path(state) / "config")},
            ):
                (root / "source.py").write_text("value = 1\n", encoding="utf-8")
                self._config(root)
                self._prompt(root, "session-epochs", "Set value to 2")
                (root / "source.py").write_text("value = 2\n", encoding="utf-8")
                self._stop(root, "session-epochs", exit_code=0)
                self._prompt(root, "session-epochs", "Now add a comment")
                session_state = SessionStateStore(root).load("session-epochs")
                self.assertEqual(session_state.task_epoch, 2)
                self.assertEqual(session_state.requirements, ("Now add a comment",))


if __name__ == "__main__":
    unittest.main()
