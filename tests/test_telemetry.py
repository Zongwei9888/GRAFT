from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from graft.codex.telemetry import (
    ProducerEvidenceLedger,
    record_from_hook_event,
)


class ProducerEvidenceLedgerTests(unittest.TestCase):
    def test_records_bounded_semantics_without_exposing_secrets(self) -> None:
        record = record_from_hook_event(
            {
                "session_id": "session",
                "turn_id": "turn",
                "tool_name": "Bash",
                "tool_input": {"command": "API_KEY=super-secret ./check --case edge"},
                "tool_response": {
                    "exit_code": 1,
                    "wall_time_seconds": 2.5,
                    "output": "counterexample observed",
                },
            },
            task_epoch=3,
        )
        self.assertEqual(record.family, "execution")
        self.assertEqual(record.outcome, "failed")
        self.assertEqual(record.exit_code, 1)
        self.assertEqual(record.duration_s, 2.5)
        self.assertIn("<redacted>", record.command_preview or "")
        self.assertNotIn("super-secret", record.command_preview or "")
        self.assertTrue(record.input_hash)
        self.assertTrue(record.response_hash)

    def test_patch_paths_are_protocol_evidence_not_task_rules(self) -> None:
        record = record_from_hook_event(
            {
                "session_id": "session",
                "tool_name": "apply_patch",
                "tool_input": {
                    "command": "*** Begin Patch\n*** Update File: src/module.any\n*** End Patch"
                },
                "tool_response": {"isError": False},
            },
            task_epoch=1,
        )
        self.assertEqual(record.family, "workspace_change")
        self.assertEqual(record.changed_paths, ("src/module.any",))
        self.assertEqual(record.outcome, "succeeded")

    def test_summary_is_scoped_to_the_current_task_epoch(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            ledger = ProducerEvidenceLedger(Path(directory), "session")
            first = record_from_hook_event(
                {
                    "session_id": "session",
                    "tool_name": "Bash",
                    "tool_input": {"command": "./old-check"},
                    "tool_response": {"exit_code": 0},
                },
                task_epoch=1,
            )
            current = record_from_hook_event(
                {
                    "session_id": "session",
                    "tool_name": "Bash",
                    "tool_input": {"command": "./current-check"},
                    "tool_response": {"exit_code": 0},
                },
                task_epoch=2,
            )
            ledger.append(first)
            ledger.append(current)
            summary = ledger.summarize(task_epoch=2)
            self.assertEqual(summary.event_count, 1)
            self.assertEqual(summary.succeeded, 1)
            self.assertEqual(summary.command_previews, ("./current-check",))


if __name__ == "__main__":
    unittest.main()
