from __future__ import annotations

import sys
import unittest
from pathlib import Path

from graft.codex import CliCodexRunner
from graft.schema import RunConfig


class CodexRunnerTests(unittest.TestCase):
    def setUp(self) -> None:
        fixture = Path(__file__).parent / "fixtures" / "fake_codex.py"
        self.runner = CliCodexRunner((sys.executable, str(fixture)))
        self.repo = Path(__file__).resolve().parents[1]

    def test_start_parses_jsonl_and_usage(self) -> None:
        result = self.runner.start_thread(
            "review this", self.repo, RunConfig(timeout_s=5)
        )
        self.assertEqual(result.thread_id, "thread-new")
        self.assertEqual(result.return_code, 0)
        self.assertIn('"verdict": "pass"', result.final_response)
        self.assertGreater(result.usage["input_tokens"], 0)

    def test_continue_uses_resume_protocol(self) -> None:
        result = self.runner.continue_thread(
            "thread-new", "continue", self.repo, RunConfig(timeout_s=5)
        )
        self.assertEqual(result.thread_id, "thread-resumed")


if __name__ == "__main__":
    unittest.main()
