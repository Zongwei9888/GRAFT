from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from graft.evidence.snapshot import freeze_source
from graft.modeling import CodexFeedbackGraphBuilder, FeedbackGraphBuildError
from graft.project_config import initialize_project
from graft.registry import load_config
from graft.schema import ProducerEvidenceSummary, TurnResult


class StructuredRunner:
    def __init__(
        self, *, duplicate_candidate: bool = False, value_aware: bool = False
    ) -> None:
        self.calls = []
        self.duplicate_candidate = duplicate_candidate
        self.value_aware = value_aware

    def start_thread(self, prompt, repo, config):
        self.calls.append((prompt, repo, config))
        if config.output_schema.name == "task_analysis.schema.json":
            response = {
                "behaviors": [
                    {
                        "id": "b-render",
                        "description": "the requested scene is visibly rendered",
                        "source_refs": ["raw requirement"],
                        "observables": ["rendered frame"],
                        "severity": 1,
                        "likelihood": 0.7,
                        "reach": 0.8,
                    }
                ],
                "failure_modes": [
                    {
                        "id": "f-empty-frame",
                        "behavior_id": "b-render",
                        "description": "the resulting frame is empty",
                        "category": "visual behavior",
                        "observable_signals": ["rendered frame contains no scene"],
                        "required_capabilities": ["runtime observation"],
                        "risk": 1.2,
                    }
                ],
                "uncertainties": [],
            }
        else:
            candidates = [
                {
                        "id": "visual-runtime-probe",
                        "template_id": "agentic-evidence-reviewer",
                        "target_failure_modes": ["f-empty-frame"],
                        "objective": "run the artifact and inspect the rendered frame",
                        "prompt": "derive an observation procedure from the current repository",
                        "estimated_detection": [
                            {"failure_mode_id": "f-empty-frame", "probability": 0.85}
                        ],
                        "additional_context_sources": ["runtime frame"],
                        "additional_modalities": ["visual"],
                        "oracle": "observed rendered output",
                }
            ]
            if self.value_aware:
                candidates[0]["value_estimate"] = {
                    "actionability": 0.8,
                    "repair_success": 0.7,
                    "regression_risk": 0.1,
                    "producer_evidence_overlap": 0.2,
                    "confidence": 0.75,
                    "predicted_duration_s": 20,
                    "predicted_model_cost_usd": 0.1,
                }
                candidates[0]["revalidates_feedback"] = False
            if self.duplicate_candidate:
                candidates.append({**candidates[0], "id": "visual-runtime-probe-2"})
            response = {
                "candidates": candidates,
                "shared_blind_spots": [],
                "coverage_gaps": [],
            }
        return TurnResult(
            thread_id="fresh",
            final_response=json.dumps(response),
            events=(),
            usage={},
            return_code=0,
            stderr="",
            duration_s=0.01,
        )


class ModelingTests(unittest.TestCase):
    def test_behavior_and_verifiers_are_created_from_the_new_task(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "artifact.bin").write_bytes(b"candidate")
            config_path = initialize_project(root).path
            config = load_config(config_path)
            requirement = "Render an interactive galaxy whose stars remain visible after resize"
            snapshot = freeze_source(
                root,
                requirements=(requirement,),
                config_path=config_path,
                environment_fingerprint=config.environment_fingerprint,
            )
            runner = StructuredRunner()
            graph = CodexFeedbackGraphBuilder(codex_runner=runner).build(
                snapshot,
                (requirement,),
                config,
                config_path=config_path,
            )
            self.assertEqual(graph.behaviors[0].behavior_id, "b-render")
            self.assertEqual(graph.verifiers[0].objective, "run the artifact and inspect the rendered frame")
            self.assertEqual(graph.verifiers[0].failure_modes, ("f-empty-frame",))
            self.assertEqual(len(runner.calls), 2)
            for prompt, _, run_config in runner.calls:
                self.assertIn(requirement, prompt)
                self.assertEqual(run_config.sandbox, "read-only")
                self.assertTrue(run_config.ephemeral)
                self.assertTrue(run_config.isolate_config)
                self.assertTrue(run_config.disable_hooks)
                self.assertTrue(run_config.skip_git_repo_check)
            self.assertIn("candidate-modified files", runner.calls[0][0])
            self.assertIn("must never introduce a new", runner.calls[0][0])
            self.assertEqual(len(graph.stage_costs), 2)

    def test_shared_lineage_without_a_blind_spot_model_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "artifact.bin").write_bytes(b"candidate")
            config_path = initialize_project(root).path
            config = load_config(config_path)
            requirement = "Render the requested scene"
            snapshot = freeze_source(
                root,
                requirements=(requirement,),
                config_path=config_path,
                environment_fingerprint=config.environment_fingerprint,
            )
            with self.assertRaisesRegex(
                FeedbackGraphBuildError, "high-order blind-spot"
            ):
                CodexFeedbackGraphBuilder(
                    codex_runner=StructuredRunner(duplicate_candidate=True)
                ).build(
                    snapshot,
                    (requirement,),
                    config,
                    config_path=config_path,
                )

    def test_value_aware_modeling_receives_producer_evidence_and_value_estimates(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "artifact.bin").write_bytes(b"candidate")
            config_path = initialize_project(
                root, selection_policy="value-aware"
            ).path
            config = load_config(config_path)
            requirement = "Render the requested scene"
            snapshot = freeze_source(
                root,
                requirements=(requirement,),
                config_path=config_path,
                environment_fingerprint=config.environment_fingerprint,
            )
            evidence = ProducerEvidenceSummary(
                1,
                1,
                1,
                0,
                0,
                0.5,
                command_previews=("./existing-check",),
            )
            runner = StructuredRunner(value_aware=True)
            graph = CodexFeedbackGraphBuilder(codex_runner=runner).build(
                snapshot,
                (requirement,),
                config,
                config_path=config_path,
                producer_evidence=evidence,
            )
            self.assertEqual(graph.producer_evidence, evidence)
            self.assertEqual(graph.verifiers[0].value_estimate.actionability, 0.8)
            self.assertIn("./existing-check", runner.calls[0][0])
            self.assertIn("overlap with producer evidence", runner.calls[1][0])


if __name__ == "__main__":
    unittest.main()
