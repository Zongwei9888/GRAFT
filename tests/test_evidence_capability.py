from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from graft.evidence.capability import preflight_evidence_capabilities
from graft.evidence.reproduction import canonical_reproduction_argv
from graft.evidence.snapshot import freeze_source
from graft.registry import SelectionPolicy
from graft.schema import (
    Behavior,
    EvidenceAwareFeedbackGraph,
    EvidenceAwareVerifierResult,
    EvidenceCapability,
    EvidenceCapabilityDisposition,
    EvidenceRouteAvailability,
    FailureMode,
    PlannedEvidenceRoute,
    TurnResult,
    VerifierSpec,
    VerifierValueEstimate,
)
from graft.selection import ValueAwareSelector
from graft.verifiers import VerifierExecutor


def _route(
    *,
    availability: EvidenceRouteAvailability = EvidenceRouteAvailability.AVAILABLE,
    oracle_origin: str = "requirement_derived_runtime",
    transport: str = "standalone_command",
    dependency_origins: tuple[str, ...] = (
        "task_environment",
        "frozen_candidate",
    ),
) -> PlannedEvidenceRoute:
    return PlannedEvidenceRoute(
        route_id="portable-runtime",
        availability=availability,
        oracle_origin=oracle_origin,
        transport=transport,
        dependency_origins=dependency_origins,
        reason="exercise the frozen candidate with the task environment",
    )


def _graph(source_hash: str, spec: VerifierSpec, route: PlannedEvidenceRoute):
    return EvidenceAwareFeedbackGraph(
        source_hash=source_hash,
        behaviors=(Behavior("b", "keep the value", (), ("value",), 1, 1, 0),),
        failure_modes=(FailureMode("f", "b", "value differs", "runtime", (), (), 1),),
        verifiers=(spec,),
        shared_blind_spots=(),
        method="graft-value-aware",
        evidence_capabilities=(
            EvidenceCapability(spec.verifier_id, (route,), ()),
        ),
    )


def _spec(*, blocking: bool = True) -> VerifierSpec:
    return VerifierSpec(
        verifier_id="runtime-agent",
        kind="codex_agent",
        cost=1,
        blocking=blocking,
        failure_modes=("f",),
        objective="exercise the required value",
        prompt="run a standalone requirement-derived check",
        estimated_detection={"f": 0.8},
        isolation="temporary-copy",
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


class VNextEvidenceRunner:
    def __init__(self) -> None:
        self.output_schema_name = ""

    def start_thread(self, prompt, repo, config):
        self.output_schema_name = config.output_schema.name
        command = [
            "python3",
            "-c",
            "import source; assert source.value == 2, f'actual={source.value}'",
        ]
        response = {
            "verdict": "fail",
            "failure_modes": ["f"],
            "summary": "the candidate exposes value 1 instead of 2",
            "evidence": [
                {
                    "kind": "test",
                    "path": None,
                    "line": None,
                    "command": command,
                    "observation": "assertion failed with actual=1",
                    "expected": "source.value equals 2",
                    "actual": "source.value equals 1",
                    "failure_modes": ["f"],
                    "requirement_refs": ["R1"],
                    "oracle_origin": "requirement_derived_runtime",
                }
            ],
            "confidence": 0.95,
            "reproducible": True,
            "promotion_outcome": None,
        }
        return TurnResult(
            thread_id="vnext-verifier",
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


class MissingOutcomeRunner(VNextEvidenceRunner):
    def start_thread(self, prompt, repo, config):
        turn = super().start_thread(prompt, repo, config)
        payload = json.loads(turn.final_response)
        payload["evidence"][0]["expected"] = None
        return TurnResult(
            thread_id=turn.thread_id,
            final_response=json.dumps(payload),
            events=turn.events,
            usage=turn.usage,
            return_code=turn.return_code,
            stderr=turn.stderr,
            duration_s=turn.duration_s,
        )


class EvidenceCapabilityTests(unittest.TestCase):
    def test_portable_available_route_is_eligible_before_selection(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "source.py").write_text("value = 1\n", encoding="utf-8")
            snapshot = freeze_source(root, requirements=("value must equal 2",))
            graph = preflight_evidence_capabilities(
                _graph(snapshot.checkpoint_key, _spec(), _route()), snapshot
            )
            self.assertEqual(
                graph.evidence_capability_assessments[0].disposition,
                EvidenceCapabilityDisposition.ELIGIBLE,
            )

    def test_temporary_dependency_is_rejected_before_selection(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "source.py").write_text("value = 1\n", encoding="utf-8")
            snapshot = freeze_source(root, requirements=("value must equal 2",))
            graph = preflight_evidence_capabilities(
                _graph(
                    snapshot.checkpoint_key,
                    _spec(),
                    _route(dependency_origins=("verifier_workspace",)),
                ),
                snapshot,
            )
            assessment = graph.evidence_capability_assessments[0]
            self.assertEqual(
                assessment.disposition,
                EvidenceCapabilityDisposition.UNAVAILABLE,
            )
            self.assertIn("verifier_workspace", assessment.reasons[0])
            selection = ValueAwareSelector().select(
                graph,
                budget=2,
                policy=SelectionPolicy(
                    algorithm="value-aware-hypergraph",
                    max_verifiers=1,
                    min_marginal_gain_per_cost=0,
                    residual_risk_threshold=1,
                    strategy="value-aware",
                    uncertainty_penalty=0,
                    regression_cost=0,
                    wall_time_weight=0,
                    model_cost_weight=0,
                    nominal_cost_weight=0,
                ),
            )
            self.assertFalse(selection.feasible)
            self.assertEqual(selection.verifier_ids, ())

    def test_nonblocking_verifier_remains_advisory(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "source.py").write_text("value = 1\n", encoding="utf-8")
            snapshot = freeze_source(root, requirements=("value must equal 2",))
            graph = preflight_evidence_capabilities(
                _graph(snapshot.checkpoint_key, _spec(blocking=False), _route()),
                snapshot,
            )
            self.assertEqual(
                graph.evidence_capability_assessments[0].disposition,
                EvidenceCapabilityDisposition.ADVISORY,
            )

    def test_vnext_result_requires_and_materializes_reproduction_bundle(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config_path = root / "config.json"
            config_path.write_text('{"version": 2}\n', encoding="utf-8")
            (root / "source.py").write_text("value = 1\n", encoding="utf-8")
            requirements = ("value must equal 2",)
            snapshot = freeze_source(
                root,
                requirements=requirements,
                config_path=config_path,
                environment_fingerprint="test",
            )
            spec = _spec()
            graph = preflight_evidence_capabilities(
                _graph(snapshot.checkpoint_key, spec, _route()), snapshot
            )
            runner = VNextEvidenceRunner()
            result = VerifierExecutor(codex_runner=runner).run(
                spec,
                snapshot,
                requirements=requirements,
                graph=graph,
                config_path=config_path,
                environment_fingerprint="test",
            )
            self.assertIsInstance(result, EvidenceAwareVerifierResult)
            self.assertTrue(result.reproducible)
            self.assertEqual(len(result.reproduction_bundles), 1)
            bundle = result.reproduction_bundles[0]
            self.assertEqual(bundle.checkpoint_key, snapshot.checkpoint_key)
            self.assertEqual(bundle.route_id, "portable-runtime")
            self.assertEqual(bundle.expected, "source.value equals 2")
            self.assertEqual(bundle.actual, "source.value equals 1")
            self.assertEqual(runner.output_schema_name, "verifier_verdict_vnext.schema.json")

    def test_vnext_finding_without_expected_actual_cannot_block(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config_path = root / "config.json"
            config_path.write_text('{"version": 2}\n', encoding="utf-8")
            (root / "source.py").write_text("value = 1\n", encoding="utf-8")
            requirements = ("value must equal 2",)
            snapshot = freeze_source(
                root,
                requirements=requirements,
                config_path=config_path,
                environment_fingerprint="test",
            )
            spec = _spec()
            graph = preflight_evidence_capabilities(
                _graph(snapshot.checkpoint_key, spec, _route()), snapshot
            )
            result = VerifierExecutor(codex_runner=MissingOutcomeRunner()).run(
                spec,
                snapshot,
                requirements=requirements,
                graph=graph,
                config_path=config_path,
                environment_fingerprint="test",
            )
            self.assertFalse(result.reproducible)
            self.assertEqual(result.reproduction_bundles, ())

    def test_absolute_verifier_copy_path_is_canonicalized_for_replay(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            candidate = root / "check"
            candidate.write_text("#!/bin/sh\nexit 1\n", encoding="utf-8")
            canonical = canonical_reproduction_argv(
                (str(candidate),),
                frozen_files=frozenset({"check"}),
                run_root=root,
            )
            self.assertEqual(canonical, ("./check",))

    def test_inline_program_cannot_hide_a_temporary_dependency(self) -> None:
        with patch(
            "graft.evidence.reproduction.shutil.which",
            return_value="/usr/bin/custom-runtime",
        ):
            canonical = canonical_reproduction_argv(
                (
                    "custom-runtime",
                    "--eval",
                    "load('/tmp/verifier-dependency')",
                ),
                frozen_files=frozenset(),
                run_root=Path("/app"),
            )
        self.assertIsNone(canonical)

    def test_missing_task_environment_executable_is_not_replayable(self) -> None:
        with patch("graft.evidence.reproduction.shutil.which", return_value=None):
            canonical = canonical_reproduction_argv(
                ("unavailable-runtime", "--version"),
                frozen_files=frozenset(),
                run_root=Path("/app"),
            )
        self.assertIsNone(canonical)


if __name__ == "__main__":
    unittest.main()
