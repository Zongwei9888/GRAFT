from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

from graft.codex import CliCodexRunner
from graft.evidence.snapshot import freeze_source
from graft.schema import (
    Behavior,
    FailureMode,
    FeedbackGraph,
    Lineage,
    VerifierSpec,
    Verdict,
)
from graft.verifiers import VerifierExecutor


class CodexReviewerTests(unittest.TestCase):
    def test_fresh_reviewer_is_parsed_and_does_not_mutate_producer(self) -> None:
        fixture = Path(__file__).parent / "fixtures" / "fake_codex.py"
        runner = CliCodexRunner((sys.executable, str(fixture)))
        executor = VerifierExecutor(codex_runner=runner)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "source.py").write_text("value = 1\n", encoding="utf-8")
            config_path = root / "config.json"
            config_path.write_text(json.dumps({"version": 2}), encoding="utf-8")
            source = freeze_source(
                root,
                requirements=("Keep value correct",),
                config_path=config_path,
                environment_fingerprint="test",
            )
            failure = FailureMode("f", "b", "value is wrong", "semantic", (), (), 1)
            spec = VerifierSpec(
                verifier_id="review",
                kind="codex_review",
                cost=1,
                blocking=False,
                failure_modes=("f",),
                objective="verify the task-specific value behavior",
                prompt="derive evidence from the requirement",
                estimated_detection={"f": 0.8},
                lineage=Lineage(provider="openai"),
            )
            graph = FeedbackGraph(
                source.checkpoint_key,
                (Behavior("b", "keep value correct", (), ("value",), 1, 1, 0),),
                (failure,),
                (spec,),
                (),
            )
            result = executor.run(
                spec,
                source,
                requirements=("Keep value correct",),
                graph=graph,
                config_path=config_path,
                environment_fingerprint="test",
            )
            self.assertEqual(result.verdict, Verdict.PASS)
            self.assertFalse(result.blocking)
            after = freeze_source(
                root,
                requirements=("Keep value correct",),
                config_path=config_path,
                environment_fingerprint="test",
            )
            self.assertEqual(source.tree_hash, after.tree_hash)


if __name__ == "__main__":
    unittest.main()
