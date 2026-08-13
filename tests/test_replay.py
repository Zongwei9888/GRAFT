from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from graft.project_config import initialize_project
from graft.registry import load_config
from graft.replay import ReplayInputError, load_report_graph, replay_selection
from graft.schema import (
    Behavior,
    EvidenceAwareFeedbackGraph,
    EvidenceCapability,
    EvidenceCapabilityAssessment,
    EvidenceCapabilityDisposition,
    EvidenceRouteAvailability,
    FailureMode,
    FeedbackGraph,
    PlannedEvidenceRoute,
    VerifierSpec,
    VerifierValueEstimate,
    to_jsonable,
)


class ReplayTests(unittest.TestCase):
    def test_value_aware_report_replays_without_execution(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            config_path = initialize_project(
                root, selection_policy="value-aware"
            ).path
            report = root / "report.json"
            report.write_text(
                json.dumps({"graph": to_jsonable(_graph())}), encoding="utf-8"
            )
            selection = replay_selection(report, load_config(config_path))
            self.assertEqual(selection.verifier_ids, ("dynamic",))
            self.assertEqual(load_report_graph(report).source_hash, "checkpoint")

    def test_missing_graph_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            report = Path(temporary) / "report.json"
            report.write_text("{}", encoding="utf-8")
            with self.assertRaises(ReplayInputError):
                load_report_graph(report)

    def test_evidence_aware_replay_preserves_preflight_exclusion(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            config_path = initialize_project(
                root, selection_policy="value-aware"
            ).path
            base = _graph()
            graph = EvidenceAwareFeedbackGraph(
                **base.__dict__,
                evidence_capabilities=(
                    EvidenceCapability(
                        "dynamic",
                        (
                            PlannedEvidenceRoute(
                                "temporary-route",
                                EvidenceRouteAvailability.AVAILABLE,
                                "requirement_derived_runtime",
                                "standalone_command",
                                ("verifier_workspace",),
                                "depends on disposable state",
                            ),
                        ),
                    ),
                ),
                evidence_capability_assessments=(
                    EvidenceCapabilityAssessment(
                        "dynamic",
                        EvidenceCapabilityDisposition.UNAVAILABLE,
                        reasons=("non-portable dependency",),
                    ),
                ),
            )
            report = root / "evidence-aware-report.json"
            report.write_text(
                json.dumps({"graph": to_jsonable(graph)}), encoding="utf-8"
            )
            loaded = load_report_graph(report)
            self.assertIsInstance(loaded, EvidenceAwareFeedbackGraph)
            selection = replay_selection(report, load_config(config_path))
            self.assertEqual(selection.verifier_ids, ())
            self.assertFalse(selection.feasible)


def _graph() -> FeedbackGraph:
    verifier = VerifierSpec(
        verifier_id="dynamic",
        template_id="semantic-reviewer",
        kind="codex_review",
        cost=1,
        blocking=True,
        failure_modes=("f",),
        estimated_detection={"f": 1.0},
        value_estimate=VerifierValueEstimate(
            actionability=1,
            repair_success=1,
            regression_risk=0,
            producer_evidence_overlap=0,
            confidence=1,
            predicted_duration_s=1,
            predicted_model_cost_usd=0,
        ),
    )
    return FeedbackGraph(
        source_hash="checkpoint",
        behaviors=(Behavior("b", "required", (), ("result",), 1, 1, 0),),
        failure_modes=(FailureMode("f", "b", "fails", "semantic", (), (), 1),),
        verifiers=(verifier,),
        shared_blind_spots=(),
    )


if __name__ == "__main__":
    unittest.main()
