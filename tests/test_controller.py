from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from graft.controller import GraftController
from graft.project_config import initialize_project
from graft.registry import load_config
from graft.schema import (
    Behavior,
    DecisionKind,
    EvidenceItem,
    FailureMode,
    FeedbackGraph,
    Verdict,
    VerifierResult,
    VerifierSpec,
)


def feedback_graph(
    source_hash: str, *, uncertainties: tuple[str, ...] = ()
) -> FeedbackGraph:
    spec = VerifierSpec(
        verifier_id="dynamic-review",
        kind="codex_review",
        cost=1,
        blocking=True,
        failure_modes=("f1",),
        objective="check the modeled behavior",
        prompt="seek an observable counterexample",
        estimated_detection={"f1": 0.9},
    )
    return FeedbackGraph(
        source_hash=source_hash,
        behaviors=(Behavior("b1", "the requested value is correct", (), ("value",), 1, 1, 0),),
        failure_modes=(
            FailureMode("f1", "b1", "the value violates the request", "semantic", (), (), 1),
        ),
        verifiers=(spec,),
        shared_blind_spots=(),
        uncertainties=uncertainties,
    )


class GraphBuilder:
    def __init__(self, *, uncertainties: tuple[str, ...] = ()) -> None:
        self.uncertainties = uncertainties

    def build(self, snapshot, requirements, config, *, config_path):
        self.requirements = requirements
        return feedback_graph(
            snapshot.checkpoint_key, uncertainties=self.uncertainties
        )


class Executor:
    def __init__(
        self,
        verdict: Verdict,
        *,
        reproducible: bool = False,
        source_hash: str | None = None,
    ) -> None:
        self.verdict = verdict
        self.reproducible = reproducible
        self.source_hash = source_hash

    def run(self, spec, snapshot, **kwargs):
        evidence = (
            EvidenceItem(
                "command",
                "observed mismatch",
                command=("check-value",),
                failure_modes=("f1",),
                oracle_origin="authoritative_runtime",
            ),
        ) if self.verdict == Verdict.FAIL else ()
        return VerifierResult(
            verifier_id=spec.verifier_id,
            verdict=self.verdict,
            summary="observed mismatch" if self.verdict == Verdict.FAIL else "no mismatch",
            source_hash=self.source_hash or snapshot.checkpoint_key,
            blocking=True,
            reproducible=self.reproducible,
            duration_s=0.01,
            failure_modes=("f1",) if self.verdict == Verdict.FAIL else (),
            evidence=evidence,
        )


class ControllerTests(unittest.TestCase):
    def _controller(
        self,
        root: Path,
        verdict: Verdict,
        *,
        reproducible: bool = False,
        uncertainties: tuple[str, ...] = (),
        result_source_hash: str | None = None,
    ):
        path = initialize_project(root).path
        builder = GraphBuilder(uncertainties=uncertainties)
        controller = GraftController(
            load_config(path),
            config_path=path,
            graph_builder=builder,
            executor=Executor(
                verdict,
                reproducible=reproducible,
                source_hash=result_source_hash,
            ),
        )
        return controller, builder

    def test_reproducible_failure_blocks_and_writes_behavior_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "artifact.data").write_text("value=1\n", encoding="utf-8")
            controller, builder = self._controller(root, Verdict.FAIL, reproducible=True)
            decision = controller.verify(
                root, requirements=("value must be correct",), session_id="test"
            )
            self.assertEqual(decision.kind, DecisionKind.CONTINUE_WITH_EVIDENCE)
            self.assertEqual(builder.requirements, ("value must be correct",))
            self.assertTrue(Path(decision.report_path or "").exists())
            self.assertIn("Violated behavior", decision.reason)
            self.assertIn("Reproduce", decision.reason)
            self.assertIn("Do not invoke GRAFT verification manually", decision.reason)

    def test_default_behavior_modeler_has_headroom_inside_stop_budget(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = load_config(initialize_project(root).path)
            self.assertEqual(config.behavior_modeler.timeout_s, 180)

    def test_passing_dynamic_verifier_allows_when_residual_is_bounded(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "artifact.data").write_text("value=1\n", encoding="utf-8")
            controller, _ = self._controller(root, Verdict.PASS)
            decision = controller.verify(root, requirements=("keep value correct",))
            self.assertEqual(decision.kind, DecisionKind.ALLOW)
            self.assertIsNotNone(decision.graph)

    def test_abstention_is_unresolved_not_allowed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "artifact.data").write_text("value=1\n", encoding="utf-8")
            controller, _ = self._controller(root, Verdict.ABSTAIN)
            decision = controller.verify(root, requirements=("keep value correct",))
            self.assertEqual(decision.kind, DecisionKind.UNRESOLVED)
            self.assertIn("abstained", decision.reason)

    def test_missing_raw_requirements_never_falls_back_to_hardcoded_checks(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "artifact.data").write_text("value=1\n", encoding="utf-8")
            controller, _ = self._controller(root, Verdict.PASS)
            decision = controller.verify(root)
            self.assertEqual(decision.kind, DecisionKind.UNRESOLVED)
            self.assertIn("raw user requirements", decision.reason)

    def test_missing_shared_lineage_model_is_not_allowed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "artifact.data").write_text("value=1\n", encoding="utf-8")
            controller, _ = self._controller(
                root,
                Verdict.PASS,
                uncertainties=(
                    "lineage_uncertainty: shared sources lack a blind-spot scenario",
                ),
            )
            decision = controller.verify(root, requirements=("keep value correct",))
            self.assertEqual(decision.kind, DecisionKind.UNRESOLVED)
            self.assertIn("lineage_uncertainty", decision.reason)

    def test_stale_verifier_result_is_not_allowed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "artifact.data").write_text("value=1\n", encoding="utf-8")
            controller, _ = self._controller(
                root,
                Verdict.PASS,
                result_source_hash="different-checkpoint",
            )
            decision = controller.verify(root, requirements=("keep value correct",))
            self.assertEqual(decision.kind, DecisionKind.UNRESOLVED)
            self.assertIn("different checkpoint", decision.reason)


if __name__ == "__main__":
    unittest.main()
