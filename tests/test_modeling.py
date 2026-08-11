from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from graft.evidence.snapshot import freeze_source
from graft.modeling import CodexFeedbackGraphBuilder
from graft.project_config import initialize_project
from graft.registry import load_config
from graft.schema import TurnResult


class StructuredRunner:
    def __init__(self) -> None:
        self.calls = []

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
            response = {
                "candidates": [
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
                ],
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


if __name__ == "__main__":
    unittest.main()
