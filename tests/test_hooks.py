from __future__ import annotations

import io
import json
import os
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

from graft.codex import hooks
from graft.codex.session_state import SessionStateStore
from graft.configuration import trust_project_config


class HookReplayTests(unittest.TestCase):
    def _config(self, root: Path, exit_code: int) -> None:
        path = root / ".graft" / "config.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(
                {
                    "version": 1,
                    "enabled": True,
                    "budget": 1,
                    "max_set_fpr": 0,
                    "checkpoint_mode": "strict",
                    "max_feedback_rounds": 2,
                    "failure_policy": "open",
                    "verifiers": [
                        {
                            "id": "fixture",
                            "kind": "command",
                            "cost": 1,
                            "blocking": True,
                            "command": [
                                sys.executable,
                                "-c",
                                f"raise SystemExit({exit_code})",
                            ],
                        }
                    ],
                    "calibration": {
                        "failure_scenarios": [
                            {"id": "failure", "detections": {"fixture": 1}}
                        ],
                        "clean_scenarios": [
                            {"id": "clean", "false_alarms": {"fixture": 0}}
                        ],
                    },
                }
            ),
            encoding="utf-8",
        )
        trust_project_config(root)

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

    def _stop(self, root: Path, session: str, active: bool = False) -> dict:
        return self._call(
            hooks.stop,
            {
                "session_id": session,
                "turn_id": "turn-stop",
                "cwd": str(root),
                "hook_event_name": "Stop",
                "stop_hook_active": active,
                "last_assistant_message": "Implemented the change and tests pass.",
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
                self._config(root, exit_code=0)
                self._prompt(root, "session-pass", "Change the value")
                (root / "source.py").write_text("value = 2\n", encoding="utf-8")
                result = self._stop(root, "session-pass")
                self.assertTrue(result["continue"])
                session_state = SessionStateStore(root).load("session-pass")
                self.assertEqual(session_state.status, "accepted")
                self.assertIsNotNone(session_state.last_verified_checkpoint_key)

    def test_failure_continues_and_does_not_repeat_without_change(self) -> None:
        with tempfile.TemporaryDirectory() as directory, tempfile.TemporaryDirectory() as state:
            root = Path(directory)
            with patch.dict(
                os.environ,
                {"GRAFT_STATE_HOME": state, "GRAFT_CONFIG_HOME": str(Path(state) / "config")},
            ):
                (root / "source.py").write_text("value = 1\n", encoding="utf-8")
                self._config(root, exit_code=1)
                self._prompt(root, "session-fail", "Change the value")
                (root / "source.py").write_text("value = 2\n", encoding="utf-8")
                first = self._stop(root, "session-fail")
                self.assertEqual(first["decision"], "block")
                self._prompt(root, "session-fail", first["reason"])
                session_state = SessionStateStore(root).load("session-fail")
                self.assertEqual(session_state.requirements, ("Change the value",))
                second = self._stop(root, "session-fail", active=True)
                self.assertTrue(second["continue"])
                self.assertNotIn("decision", second)

    def test_multi_turn_clarification_stays_in_one_task_epoch(self) -> None:
        with tempfile.TemporaryDirectory() as directory, tempfile.TemporaryDirectory() as state:
            root = Path(directory)
            with patch.dict(
                os.environ,
                {"GRAFT_STATE_HOME": state, "GRAFT_CONFIG_HOME": str(Path(state) / "config")},
            ):
                (root / "source.py").write_text("value = 1\n", encoding="utf-8")
                self._config(root, exit_code=0)
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
                self._config(root, exit_code=0)
                self._prompt(root, "session-epochs", "Set value to 2")
                (root / "source.py").write_text("value = 2\n", encoding="utf-8")
                self._stop(root, "session-epochs")
                self._prompt(root, "session-epochs", "Now add a comment")
                session_state = SessionStateStore(root).load("session-epochs")
                self.assertEqual(session_state.task_epoch, 2)
                self.assertEqual(session_state.requirements, ("Now add a comment",))


if __name__ == "__main__":
    unittest.main()
