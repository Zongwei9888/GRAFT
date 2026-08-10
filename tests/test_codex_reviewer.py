from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

from graft.codex import CliCodexRunner
from graft.evidence.snapshot import freeze_source
from graft.schema import Lineage, VerifierSpec, Verdict
from graft.verifiers import VerifierExecutor


class CodexReviewerTests(unittest.TestCase):
    def test_fresh_reviewer_is_parsed_and_remains_nonblocking(self) -> None:
        fixture = Path(__file__).parent / "fixtures" / "fake_codex.py"
        runner = CliCodexRunner((sys.executable, str(fixture)))
        executor = VerifierExecutor(codex_runner=runner)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "source.py").write_text("value = 1\n", encoding="utf-8")
            config_path = root / "config.json"
            config_path.write_text(json.dumps({"version": 1}), encoding="utf-8")
            schema_path = root / "schema.json"
            schema_path.write_text("{}", encoding="utf-8")
            source = freeze_source(
                root,
                requirements=("Keep value correct",),
                config_path=config_path,
            )
            result = executor.run(
                VerifierSpec(
                    verifier_id="review",
                    kind="codex_review",
                    cost=1,
                    blocking=False,
                    failure_modes=("incomplete_fix",),
                    lineage=Lineage(provider="openai"),
                ),
                source,
                requirements=("Keep value correct",),
                config_path=config_path,
                verdict_schema=schema_path,
            )
            self.assertEqual(result.verdict, Verdict.PASS)
            self.assertFalse(result.blocking)
            self.assertEqual(result.source_hash, source.checkpoint_key)
            after = freeze_source(
                root,
                requirements=("Keep value correct",),
                config_path=config_path,
            )
            self.assertEqual(source.tree_hash, after.tree_hash)


if __name__ == "__main__":
    unittest.main()
