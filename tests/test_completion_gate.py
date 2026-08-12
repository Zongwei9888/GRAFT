from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from graft.codex.completion import CodexCompletionGate
from graft.evidence.snapshot import freeze_source
from graft.project_config import initialize_project
from graft.registry import load_config
from graft.schema import (
    CompletionState,
    ProducerEvidenceSummary,
    TurnResult,
)


class CompletionRunner:
    def __init__(self, response: dict) -> None:
        self.response = response
        self.calls = []

    def start_thread(self, prompt, repo, config):
        self.calls.append((prompt, repo, config))
        return TurnResult(
            thread_id="completion",
            final_response=json.dumps(self.response),
            events=(),
            usage={"input_tokens": 50, "output_tokens": 8},
            return_code=0,
            stderr="",
            duration_s=0.4,
        )


class CompletionGateTests(unittest.TestCase):
    def _fixture(self):
        directory = tempfile.TemporaryDirectory()
        root = Path(directory.name)
        (root / "artifact.any").write_text("candidate\n", encoding="utf-8")
        config_path = initialize_project(
            root, selection_policy="value-aware"
        ).path
        config = load_config(config_path)
        requirement = "Create the requested artifact"
        snapshot = freeze_source(
            root,
            requirements=(requirement,),
            config_path=config_path,
            environment_fingerprint=config.environment_fingerprint,
        )
        return directory, root, config_path, config, requirement, snapshot

    def test_classifies_lifecycle_without_claiming_correctness(self) -> None:
        fixture = self._fixture()
        directory, _, config_path, config, requirement, snapshot = fixture
        self.addCleanup(directory.cleanup)
        runner = CompletionRunner(
            {
                "state": "candidate_complete",
                "confidence": 0.95,
                "reason": "The turn presents the changed artifact for delivery.",
            }
        )
        evidence = ProducerEvidenceSummary(
            task_epoch=1,
            event_count=1,
            succeeded=1,
            failed=0,
            unknown=0,
            total_duration_s=0.2,
            command_previews=("./visible-check",),
        )
        assessment = CodexCompletionGate(codex_runner=runner).assess(
            snapshot,
            (requirement,),
            last_assistant_message="Implemented and summarized the result.",
            producer_evidence=evidence,
            config=config.completion_gate,
            config_path=config_path,
            environment_fingerprint=config.environment_fingerprint,
        )
        self.assertEqual(assessment.state, CompletionState.CANDIDATE_COMPLETE)
        self.assertEqual(assessment.stage_cost.input_tokens, 50)
        prompt, _, run_config = runner.calls[0]
        self.assertIn(requirement, prompt)
        self.assertIn("./visible-check", prompt)
        self.assertIn("Do not judge whether", prompt)
        self.assertTrue(run_config.disable_hooks)
        self.assertTrue(run_config.isolate_config)

    def test_low_confidence_becomes_abstain(self) -> None:
        fixture = self._fixture()
        directory, _, config_path, config, requirement, snapshot = fixture
        self.addCleanup(directory.cleanup)
        runner = CompletionRunner(
            {
                "state": "candidate_complete",
                "confidence": 0.2,
                "reason": "Lifecycle evidence is weak.",
            }
        )
        assessment = CodexCompletionGate(codex_runner=runner).assess(
            snapshot,
            (requirement,),
            last_assistant_message=None,
            producer_evidence=ProducerEvidenceSummary(1, 0, 0, 0, 0, None),
            config=config.completion_gate,
            config_path=config_path,
            environment_fingerprint=config.environment_fingerprint,
        )
        self.assertEqual(assessment.state, CompletionState.ABSTAIN)


if __name__ == "__main__":
    unittest.main()
