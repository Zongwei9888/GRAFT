from __future__ import annotations

import json
import shlex
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
    PromotionOutcome,
    TurnResult,
    VerifierSpec,
    Verdict,
)
from graft.verifiers import (
    VerifierExecutor,
    _command_fingerprints,
    _portable_reproduction_command,
)
from graft.verifiers import _verifier_prompt


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
        command = (
            ["python", "-c", "raise AssertionError('observed mismatch')"]
            if self.origin == "requirement_derived_runtime"
            else ["python", "baseline_test.py"]
        )
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


class PassingPromotionRunner:
    def start_thread(self, prompt, repo, config):
        command = ["python", "-c", "print('promotion passed')"]
        response = {
            "verdict": "pass",
            "failure_modes": [],
            "summary": "prior failure is fixed and behavior is preserved",
            "evidence": [
                {
                    "kind": "runtime",
                    "path": None,
                    "line": None,
                    "command": command,
                    "observation": "the repaired runtime check passed",
                    "failure_modes": ["f"],
                    "requirement_refs": ["R1"],
                    "oracle_origin": "requirement_derived_runtime",
                }
            ],
            "confidence": 0.9,
            "reproducible": True,
            "promotion_outcome": "fixed_and_preserved",
        }
        return TurnResult(
            thread_id="promotion-verifier",
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


class UnmatchedPassingPromotionRunner(PassingPromotionRunner):
    def start_thread(self, prompt, repo, config):
        turn = super().start_thread(prompt, repo, config)
        return TurnResult(
            thread_id=turn.thread_id,
            final_response=turn.final_response,
            events=(
                {
                    "type": "item.completed",
                    "item": {
                        "type": "command_execution",
                        "command": ["python", "some_other_check.py"],
                    },
                },
            ),
            usage=turn.usage,
            return_code=turn.return_code,
            stderr=turn.stderr,
            duration_s=turn.duration_s,
        )


class CodexReviewerTests(unittest.TestCase):
    def test_verifier_prompt_requires_exact_standalone_reproduction(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "source.py").write_text("value = 1\n", encoding="utf-8")
            config_path = root / "config.json"
            config_path.write_text('{"version": 2}\n', encoding="utf-8")
            source = freeze_source(root, requirements=("Keep value correct",))
            failure = FailureMode("f", "b", "value is wrong", "runtime", (), (), 1)
            spec = VerifierSpec(
                verifier_id="evidence-agent",
                kind="codex_agent",
                cost=1,
                blocking=True,
                failure_modes=("f",),
                objective="exercise the value",
                prompt="derive a runtime check",
                isolation="temporary-copy",
            )
            graph = FeedbackGraph(
                source.checkpoint_key,
                (Behavior("b", "keep value correct", (), ("value",), 1, 1, 0),),
                (failure,),
                (spec,),
                (),
            )
            prompt = _verifier_prompt(
                spec,
                source,
                ("Keep value correct",),
                graph,
                config_path=config_path,
                environment_fingerprint="test",
            )
            self.assertIn("execute a minimal reproduction", prompt)
            self.assertIn("Do not use a shell heredoc", prompt)
            self.assertIn("temporary file with the", prompt)
            self.assertIn("one standalone,\nportable command", prompt)
            self.assertIn("explicit environment and evaluation constraints", prompt)
            self.assertIn("copy that exact executed argv", prompt)
            self.assertIn("do not join it", prompt)

    def test_shell_wrappers_are_structurally_equivalent_evidence(self) -> None:
        script = "PYTHONPATH=/tmp/workspace python3 - <<'PY'\nprint('ok')\nPY"
        reported = _command_fingerprints(("bash", "-lc", script))
        observed = _command_fingerprints(
            "/bin/bash -lc " + shlex.quote(script)
        )
        self.assertTrue(reported & observed)
        self.assertFalse(
            reported
            & _command_fingerprints(("bash", "-lc", "python3 -c 'print(2)'"))
        )

    def test_shell_wrapped_simple_command_matches_reported_inner_argv(self) -> None:
        script = "import sys; print(repr(sys.stdin.buffer.read()))"
        inner = ("python3", "-c", script)
        payload = " ".join(shlex.quote(part) for part in inner)
        observed = "/bin/zsh -lc " + shlex.quote(payload)
        self.assertTrue(
            _command_fingerprints(inner) & _command_fingerprints(observed)
        )

    def test_single_item_shell_payload_matches_wrapped_simple_command(self) -> None:
        script = "print('exact payload')"
        inner = ("python3", "-c", script)
        payload = " ".join(shlex.quote(part) for part in inner)
        reported = (payload,)
        observed = "/bin/zsh -lc " + shlex.quote(payload)
        self.assertTrue(
            _command_fingerprints(reported) & _command_fingerprints(observed)
        )

    def test_single_item_compound_payload_does_not_match_inner_command(self) -> None:
        script = "print('claimed')"
        inner = ("python3", "-c", script)
        payload = "echo setup && " + " ".join(shlex.quote(part) for part in inner)
        reported = (payload,)
        observed = "/bin/zsh -lc " + shlex.quote(payload)
        self.assertFalse(
            _command_fingerprints(inner) & _command_fingerprints(reported)
        )
        self.assertFalse(
            _command_fingerprints(reported) & _command_fingerprints(observed)
        )

    def test_compound_shell_command_does_not_promote_inner_command(self) -> None:
        script = "print('claimed')"
        inner = ("python3", "-c", script)
        payload = "echo setup && " + " ".join(shlex.quote(part) for part in inner)
        observed = "/bin/zsh -lc " + shlex.quote(payload)
        self.assertFalse(
            _command_fingerprints(inner) & _command_fingerprints(observed)
        )

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

    def test_passing_promotion_retains_target_modes_for_executed_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "config.json").write_text('{"version": 2}\n', encoding="utf-8")
            (root / "source.py").write_text("value = 1\n", encoding="utf-8")
            source = freeze_source(root, requirements=("Keep the public behavior",))
            failure = FailureMode("f", "b", "public behavior fails", "runtime", (), (), 1)
            spec = VerifierSpec(
                verifier_id="promotion-agent",
                kind="codex_agent",
                cost=1,
                blocking=True,
                failure_modes=("f",),
                objective="revalidate the repaired behavior",
                prompt="execute the prior reproduction",
                isolation="temporary-copy",
                revalidates_feedback=True,
            )
            graph = FeedbackGraph(
                source.checkpoint_key,
                (Behavior("b", "keep public behavior", (), ("result",), 1, 1, 0),),
                (failure,),
                (spec,),
                (),
            )
            result = VerifierExecutor(codex_runner=PassingPromotionRunner()).run(
                spec,
                source,
                requirements=("Keep the public behavior",),
                graph=graph,
                config_path=root / "config.json",
                environment_fingerprint="test",
            )
            self.assertEqual(result.verdict, Verdict.PASS)
            self.assertTrue(result.executed_evidence)
            self.assertEqual(result.evidence[0].failure_modes, ("f",))
            self.assertEqual(
                result.promotion_outcome, PromotionOutcome.FIXED_AND_PRESERVED
            )

    def test_unmatched_promotion_claim_is_mechanically_downgraded(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "config.json").write_text('{"version": 2}\n', encoding="utf-8")
            (root / "source.py").write_text("value = 1\n", encoding="utf-8")
            source = freeze_source(root, requirements=("Keep the public behavior",))
            failure = FailureMode("f", "b", "public behavior fails", "runtime", (), (), 1)
            spec = VerifierSpec(
                verifier_id="promotion-agent",
                kind="codex_agent",
                cost=1,
                blocking=True,
                failure_modes=("f",),
                objective="revalidate the repaired behavior",
                prompt="execute the prior reproduction",
                isolation="temporary-copy",
                revalidates_feedback=True,
            )
            graph = FeedbackGraph(
                source.checkpoint_key,
                (Behavior("b", "keep public behavior", (), ("result",), 1, 1, 0),),
                (failure,),
                (spec,),
                (),
            )
            result = VerifierExecutor(
                codex_runner=UnmatchedPassingPromotionRunner()
            ).run(
                spec,
                source,
                requirements=("Keep the public behavior",),
                graph=graph,
                config_path=root / "config.json",
                environment_fingerprint="test",
            )
            self.assertEqual(result.verdict, Verdict.PASS)
            self.assertFalse(result.executed_evidence)
            self.assertEqual(result.promotion_outcome, PromotionOutcome.UNRESOLVED)

    def test_generated_reproduction_script_is_not_portable(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "candidate.py").write_text("value = 1\n", encoding="utf-8")
            snapshot = freeze_source(root, requirements=("Keep value correct",))
            (root / "verifier_check.py").write_text(
                "assert False\n", encoding="utf-8"
            )

            self.assertFalse(
                _portable_reproduction_command(
                    ("python", "verifier_check.py"), root, snapshot
                )
            )
            self.assertTrue(
                _portable_reproduction_command(
                    ("python", "candidate.py"), root, snapshot
                )
            )
            self.assertTrue(
                _portable_reproduction_command(
                    ("python", "-c", "import candidate; assert candidate.value == 1"),
                    root,
                    snapshot,
                )
            )

    def test_simple_shell_transport_unwraps_to_portable_inline_argv(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "candidate.py").write_text("value = 1\n", encoding="utf-8")
            snapshot = freeze_source(root, requirements=("Keep value correct",))
            script = "import candidate; assert candidate.value == 1"
            payload = " ".join(
                shlex.quote(part) for part in ("python3", "-c", script)
            )

            self.assertTrue(
                _portable_reproduction_command(
                    ("bash", "-lc", payload), root, snapshot
                )
            )
            self.assertTrue(
                _portable_reproduction_command((payload,), root, snapshot)
            )

    def test_shell_transport_cannot_hide_nonportable_or_compound_command(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "candidate.py").write_text("value = 1\n", encoding="utf-8")
            snapshot = freeze_source(root, requirements=("Keep value correct",))

            self.assertFalse(
                _portable_reproduction_command(
                    ("bash", "-lc", "python3 verifier_check.py"), root, snapshot
                )
            )
            self.assertFalse(
                _portable_reproduction_command(
                    ("bash", "-lc", "echo setup && python3 -c 'print(1)'"),
                    root,
                    snapshot,
                )
            )
            self.assertFalse(
                _portable_reproduction_command(
                    ("bash", "-lc", "PYTHONPATH=/tmp python3 -c 'print(1)'"),
                    root,
                    snapshot,
                )
            )

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
