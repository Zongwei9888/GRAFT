from __future__ import annotations

import io
import json
import os
import shlex
import sys
import tarfile
import tempfile
import unittest
from contextlib import redirect_stdout
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from graft.codex import hooks
from graft.codex.runtime_authority import AUTHORITY_ENV
from graft.codex.session_state import SessionStateStore
from graft.configuration import trust_project_config
from graft.project_config import initialize_project
from graft.schema import (
    Behavior,
    CompletionAssessment,
    CompletionState,
    FailureMode,
    FeedbackGraph,
    VerifierSpec,
    VerifierValueEstimate,
)


class RepositoryHookConfigurationTests(unittest.TestCase):
    def test_commands_pass_the_required_event_argument(self) -> None:
        project_root = Path(__file__).resolve().parents[1]
        configured_hooks = json.loads(
            (project_root / ".codex" / "hooks.json").read_text(encoding="utf-8")
        )["hooks"]
        expected_events = {
            "UserPromptSubmit": "user-prompt",
            "PostToolUse": "post-tool",
            "Stop": "stop",
        }

        for hook_name, event in expected_events.items():
            command = configured_hooks[hook_name][0]["hooks"][0]["command"]
            arguments = shlex.split(command)
            self.assertIn(event, arguments)
            self.assertLess(arguments.index(event), arguments.index("--installation-id"))


class HookReplayTests(unittest.TestCase):
    def _config(self, root: Path, *, selection_policy: str = "original") -> None:
        initialize_project(root, selection_policy=selection_policy)
        trust_project_config(root)

    def _graph(
        self,
        exit_code: int,
        source_hash: str,
        *,
        value_aware: bool = False,
    ) -> FeedbackGraph:
        verifier = VerifierSpec(
            verifier_id="task-specific-check",
            kind="command",
            cost=1,
            blocking=True,
            failure_modes=("f",),
            objective="execute a test-derived check",
            estimated_detection={"f": 1.0},
            command=(sys.executable, "-c", f"raise SystemExit({exit_code})"),
            value_estimate=(
                VerifierValueEstimate(
                    actionability=1,
                    repair_success=1,
                    regression_risk=0,
                    producer_evidence_overlap=0,
                    confidence=1,
                    predicted_duration_s=1,
                    predicted_model_cost_usd=0,
                )
                if value_aware
                else VerifierValueEstimate()
            ),
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

    def _value_stop(
        self,
        root: Path,
        session: str,
        *,
        state: CompletionState,
        exit_code: int = 0,
    ) -> dict:
        assessment = CompletionAssessment(state, 1.0, "test lifecycle assessment")
        with (
            patch(
                "graft.codex.hooks.CodexCompletionGate.assess",
                return_value=assessment,
            ),
            patch(
                "graft.controller.CodexFeedbackGraphBuilder.build",
                side_effect=lambda snapshot, requirements, config, **kwargs: self._graph(
                    exit_code,
                    snapshot.checkpoint_key,
                    value_aware=True,
                ),
            ),
        ):
            return self._call(
                hooks.stop,
                {
                    "session_id": session,
                    "turn_id": "turn-value-stop",
                    "cwd": str(root),
                    "hook_event_name": "Stop",
                    "stop_hook_active": False,
                    "last_assistant_message": "candidate lifecycle message",
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

    def test_non_authority_main_returns_before_claiming_or_writing_state(self) -> None:
        with tempfile.TemporaryDirectory() as directory, tempfile.TemporaryDirectory() as state:
            root = Path(directory)
            event = {
                "session_id": "shadow",
                "turn_id": "prompt-shadow",
                "cwd": str(root),
                "hook_event_name": "UserPromptSubmit",
                "prompt": "Do not let the shadow runtime mutate state",
            }
            output = io.StringIO()
            with (
                patch.dict(
                    os.environ,
                    {
                        "GRAFT_STATE_HOME": state,
                        AUTHORITY_ENV: "graft-plugin-v1",
                    },
                ),
                patch.object(sys, "stdin", io.StringIO(json.dumps(event))),
                redirect_stdout(output),
            ):
                result = hooks.main(
                    ["user-prompt", "--installation-id", "graft-global-v1"]
                )
            self.assertEqual(result, 0)
            self.assertEqual(json.loads(output.getvalue()), {"continue": True})
            self.assertFalse(list(Path(state).rglob("shadow.json")))

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

    def test_value_aware_intermediate_turn_does_not_build_feedback_graph(self) -> None:
        with tempfile.TemporaryDirectory() as directory, tempfile.TemporaryDirectory() as state:
            root = Path(directory)
            with patch.dict(
                os.environ,
                {"GRAFT_STATE_HOME": state, "GRAFT_CONFIG_HOME": str(Path(state) / "config")},
            ):
                source = root / "source.any"
                source.write_text("before\n", encoding="utf-8")
                self._config(root, selection_policy="value-aware")
                self._prompt(root, "value-intermediate", "Implement the requested change")
                source.write_text("partial\n", encoding="utf-8")
                result = self._value_stop(
                    root,
                    "value-intermediate",
                    state=CompletionState.INTERMEDIATE,
                )
                self.assertTrue(result["continue"])
                captured = SessionStateStore(root).load("value-intermediate")
                self.assertEqual(captured.status, "active")
                self.assertIsNone(captured.last_verified_checkpoint_key)

    def test_value_aware_noop_accepts_without_running_a_verifier(self) -> None:
        with tempfile.TemporaryDirectory() as directory, tempfile.TemporaryDirectory() as state:
            root = Path(directory)
            with patch.dict(
                os.environ,
                {"GRAFT_STATE_HOME": state, "GRAFT_CONFIG_HOME": str(Path(state) / "config")},
            ):
                source = root / "source.any"
                source.write_text("before\n", encoding="utf-8")
                self._config(root, selection_policy="value-aware")
                self._prompt(root, "value-noop", "Implement the requested change")
                source.write_text("candidate\n", encoding="utf-8")
                low_value = self._graph(0, "placeholder", value_aware=True)
                low_value = replace(
                    low_value,
                    verifiers=(
                        replace(
                            low_value.verifiers[0],
                            value_estimate=replace(
                                low_value.verifiers[0].value_estimate,
                                producer_evidence_overlap=1,
                            ),
                        ),
                    ),
                )
                assessment = CompletionAssessment(
                    CompletionState.CANDIDATE_COMPLETE,
                    1.0,
                    "delivery candidate",
                )
                with (
                    patch(
                        "graft.codex.hooks.CodexCompletionGate.assess",
                        return_value=assessment,
                    ),
                    patch(
                        "graft.controller.CodexFeedbackGraphBuilder.build",
                        side_effect=lambda snapshot, requirements, config, **kwargs: FeedbackGraph(
                            source_hash=snapshot.checkpoint_key,
                            behaviors=low_value.behaviors,
                            failure_modes=low_value.failure_modes,
                            verifiers=low_value.verifiers,
                            shared_blind_spots=(),
                            producer_evidence=kwargs.get("producer_evidence"),
                        ),
                    ),
                    patch("graft.controller.VerifierExecutor.run") as execute,
                ):
                    result = self._call(
                        hooks.stop,
                        {
                            "session_id": "value-noop",
                            "turn_id": "turn-value-noop",
                            "cwd": str(root),
                            "hook_event_name": "Stop",
                            "stop_hook_active": False,
                            "last_assistant_message": "ready",
                        },
                    )
                self.assertTrue(result["continue"])
                execute.assert_not_called()
                captured = SessionStateStore(root).load("value-noop")
                self.assertEqual(captured.status, "accepted")
                self.assertEqual(captured.spent_budget, 0)

    def test_value_aware_feedback_creates_a_promotion_requirement(self) -> None:
        with tempfile.TemporaryDirectory() as directory, tempfile.TemporaryDirectory() as state:
            root = Path(directory)
            with patch.dict(
                os.environ,
                {"GRAFT_STATE_HOME": state, "GRAFT_CONFIG_HOME": str(Path(state) / "config")},
            ):
                source = root / "source.any"
                source.write_text("before\n", encoding="utf-8")
                self._config(root, selection_policy="value-aware")
                self._prompt(root, "value-feedback", "Implement the requested change")
                source.write_text("candidate\n", encoding="utf-8")
                result = self._value_stop(
                    root,
                    "value-feedback",
                    state=CompletionState.CANDIDATE_COMPLETE,
                    exit_code=1,
                )
                self.assertEqual(result["decision"], "block")
                captured = SessionStateStore(root).load("value-feedback")
                self.assertIsNotNone(captured.pending_promotion)
                self.assertEqual(captured.verification_round, 1)
                self.assertGreater(captured.spent_budget, 0)


if __name__ == "__main__":
    unittest.main()
