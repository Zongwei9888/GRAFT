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
    TurnResult,
    VerifierSpec,
    Verdict,
)
from graft.verifiers import VerifierExecutor


class EvidenceRunner:
    def __init__(
        self,
        *,
        origin: str,
        path: str | None = None,
        kind: str = "command",
        requirement_refs: tuple[str, ...] = (),
    ) -> None:
        self.origin = origin
        self.path = path
        self.kind = kind
        self.requirement_refs = requirement_refs

    def start_thread(self, prompt, repo, config):
        command = ["python", "baseline_test.py"]
        response = {
            "verdict": "fail",
            "failure_modes": ["f"],
            "summary": "observed mismatch",
            "evidence": [
                {
                    "kind": self.kind,
                    "path": self.path,
                    "line": None,
                    "command": command,
                    "observation": "the check failed",
                    "failure_modes": ["f"],
                    "requirement_refs": list(self.requirement_refs),
                    "oracle_origin": self.origin,
                }
            ],
            "confidence": 0.9,
            "reproducible": True,
        }
        return TurnResult(
            thread_id="verifier",
            final_response=json.dumps(response),
            events=(
                {
                    "type": "item.completed",
                    "item": {"type": "command_execution", "command": command},
                },
            ),
            usage={},
            return_code=0,
            stderr="",
            duration_s=0.01,
        )


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

    def _run_evidence_case(
        self,
        root: Path,
        *,
        origin: str,
        path: str | None,
        baseline,
        kind: str = "command",
        requirement_refs: tuple[str, ...] = (),
        isolation: str = "ephemeral",
    ):
        config_path = root / "config.json"
        source = freeze_source(
            root,
            requirements=("Keep the public behavior",),
            config_path=config_path,
            environment_fingerprint="test",
            baseline_tree_hash=baseline.tree_hash,
            baseline_files=baseline.files,
            baseline_file_hashes=baseline.file_hashes,
        )
        failure = FailureMode("f", "b", "public behavior fails", "runtime", (), (), 1)
        spec = VerifierSpec(
            verifier_id="evidence-agent",
            kind="codex_agent",
            cost=1,
            blocking=True,
            failure_modes=("f",),
            objective="exercise the public behavior",
            prompt="use an authoritative oracle",
            estimated_detection={"f": 0.8},
            isolation=isolation,
            lineage=Lineage(provider="openai"),
        )
        graph = FeedbackGraph(
            source.checkpoint_key,
            (Behavior("b", "keep public behavior", (), ("result",), 1, 1, 0),),
            (failure,),
            (spec,),
            (),
        )
        return VerifierExecutor(
            codex_runner=EvidenceRunner(
                origin=origin,
                path=path,
                kind=kind,
                requirement_refs=requirement_refs,
            )
        ).run(
            spec,
            source,
            requirements=("Keep the public behavior",),
            graph=graph,
            config_path=config_path,
            environment_fingerprint="test",
        )

    def test_source_inspection_command_cannot_be_promoted_to_reproduction(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "config.json").write_text('{"version": 2}\n', encoding="utf-8")
            (root / "baseline_test.py").write_text("assert True\n", encoding="utf-8")
            (root / "source.py").write_text("before\n", encoding="utf-8")
            baseline = freeze_source(root)
            (root / "source.py").write_text("after\n", encoding="utf-8")
            result = self._run_evidence_case(
                root,
                origin="source_inspection",
                path="source.py",
                baseline=baseline,
            )
            self.assertEqual(result.verdict, Verdict.FAIL)
            self.assertFalse(result.reproducible)

    def test_requirement_derived_runtime_can_support_test_agent_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "config.json").write_text('{"version": 2}\n', encoding="utf-8")
            (root / "source.py").write_text("value = 1\n", encoding="utf-8")
            baseline = freeze_source(root)
            result = self._run_evidence_case(
                root,
                origin="requirement_derived_runtime",
                path=None,
                baseline=baseline,
                kind="test",
                requirement_refs=("R1",),
                isolation="temporary-copy",
            )
            self.assertTrue(result.reproducible)
            self.assertEqual(result.failure_modes, ("f",))

    def test_requirement_derived_runtime_requires_valid_raw_requirement_refs(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "config.json").write_text('{"version": 2}\n', encoding="utf-8")
            (root / "source.py").write_text("value = 1\n", encoding="utf-8")
            baseline = freeze_source(root)
            result = self._run_evidence_case(
                root,
                origin="requirement_derived_runtime",
                path=None,
                baseline=baseline,
                kind="test",
                requirement_refs=("R2",),
                isolation="temporary-copy",
            )
            self.assertFalse(result.reproducible)

    def test_requirement_derived_source_inspection_stays_nonblocking(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "config.json").write_text('{"version": 2}\n', encoding="utf-8")
            (root / "source.py").write_text("value = 1\n", encoding="utf-8")
            baseline = freeze_source(root)
            result = self._run_evidence_case(
                root,
                origin="requirement_derived_runtime",
                path="source.py",
                baseline=baseline,
                kind="command",
                requirement_refs=("R1",),
                isolation="temporary-copy",
            )
            self.assertFalse(result.reproducible)

    def test_unchanged_baseline_oracle_can_support_blocking_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "config.json").write_text('{"version": 2}\n', encoding="utf-8")
            (root / "baseline_test.py").write_text("assert True\n", encoding="utf-8")
            (root / "source.py").write_text("before\n", encoding="utf-8")
            baseline = freeze_source(root)
            (root / "source.py").write_text("after\n", encoding="utf-8")
            result = self._run_evidence_case(
                root,
                origin="baseline_repository",
                path="baseline_test.py",
                baseline=baseline,
            )
            self.assertTrue(result.reproducible)
            self.assertEqual(result.failure_modes, ("f",))

    def test_candidate_modified_oracle_cannot_support_blocking_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "config.json").write_text('{"version": 2}\n', encoding="utf-8")
            (root / "baseline_test.py").write_text("assert True\n", encoding="utf-8")
            baseline = freeze_source(root)
            (root / "baseline_test.py").write_text("assert False\n", encoding="utf-8")
            result = self._run_evidence_case(
                root,
                origin="baseline_repository",
                path="baseline_test.py",
                baseline=baseline,
            )
            self.assertFalse(result.reproducible)


if __name__ == "__main__":
    unittest.main()
