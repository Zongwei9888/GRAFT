from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

from graft.controller import GraftController
from graft.schema import (
    DecisionKind,
    Selection,
    SourceSnapshot,
    Verdict,
    VerifierResult,
)


class ControllerTests(unittest.TestCase):
    def _write_config(self, root: Path, exit_code: int) -> Path:
        config_path = root / ".graft" / "config.json"
        config_path.parent.mkdir(parents=True, exist_ok=True)
        config = {
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
                    "command": [sys.executable, "-c", f"raise SystemExit({exit_code})"],
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
        config_path.write_text(json.dumps(config), encoding="utf-8")
        return config_path

    def test_reproducible_failure_blocks_and_writes_report(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "source.py").write_text("value = 1\n", encoding="utf-8")
            path = self._write_config(root, exit_code=1)
            decision = GraftController.from_path(path).verify(
                root, requirements=("value must be correct",), session_id="test"
            )
            self.assertEqual(decision.kind, DecisionKind.CONTINUE_WITH_EVIDENCE)
            self.assertTrue(Path(decision.report_path or "").exists())
            self.assertIn("Reproduce", decision.reason)

    def test_passing_verifier_allows(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "source.py").write_text("value = 1\n", encoding="utf-8")
            path = self._write_config(root, exit_code=0)
            decision = GraftController.from_path(path).verify(root)
            self.assertEqual(decision.kind, DecisionKind.ALLOW)

    def test_abstention_is_unresolved_not_allowed(self) -> None:
        controller = object.__new__(GraftController)
        source = SourceSnapshot(
            "/tmp", "tree", "requirements", "config", "checkpoint", (), "now"
        )
        selection = Selection(("review",), 0.5, 0.0, 1.0, True, 2)
        result = VerifierResult(
            verifier_id="review",
            verdict=Verdict.ABSTAIN,
            summary="Insufficient evidence.",
            source_hash="checkpoint",
            blocking=False,
            reproducible=False,
            duration_s=0.1,
        )
        decision = controller._decide(source, selection, (result,))
        self.assertEqual(decision.kind, DecisionKind.UNRESOLVED)
        self.assertIn("abstained", decision.reason)


if __name__ == "__main__":
    unittest.main()
