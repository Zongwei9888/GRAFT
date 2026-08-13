from __future__ import annotations

import json
import hashlib
import sys
import tempfile
import unittest
from pathlib import Path

from experiments.coding_verifier_matrix.verifier_matrix import (
    DisposableBranchCodexRunner,
    OuterContainerCopyCodexRunner,
    _changed_paths,
    assemble_branch_matrix,
    capture_baseline,
    capture_candidate,
    materialize_config,
    run_matrix,
    run_verifier_branch,
    select_workspace,
    select_unique_workspace,
)
from experiments.coding_verifier_matrix.continuation_replay import (
    _eligible_evidence,
    feedback_packet,
    restore_candidate,
)
from graft.evidence.baseline_archive import archive_baseline
from graft.evidence.snapshot import hash_tree_manifest
from graft.registry import default_original_config_payload, load_config
from graft.schema import (
    Behavior,
    FailureMode,
    FeedbackGraph,
    RunConfig,
    VerifierSpec,
    to_jsonable,
)


PROJECT_ROOT = Path(__file__).resolve().parents[1]


class CodingVerifierMatrixTests(unittest.TestCase):
    def test_legacy_copy_matrix_fails_closed_on_absolute_workspace_contract(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            repo = root / "repo"
            repo.mkdir()
            (repo / "source.txt").write_text("before\n", encoding="utf-8")
            baseline = capture_baseline(repo, root / "baselines")
            (repo / "source.txt").write_text("after\n", encoding="utf-8")
            config_path = root / "config.json"
            config_path.write_text(
                json.dumps(materialize_config("gpt-5.6-sol")), encoding="utf-8"
            )

            result = run_matrix(
                repo,
                (f"Write the result below `{repo.resolve()}/output`.",),
                baseline,
                config_path=config_path,
                candidate_archive_root=root / "candidate-archives",
                max_verifiers=8,
            )

            self.assertEqual(result["status"], "isolation_not_supported")
            self.assertEqual(result["absolute_workspace_requirement_refs"], ["R1"])
            self.assertEqual(result["verifier_count"], 0)
            self.assertNotIn("graph", result)

    def test_independent_verifier_branch_may_mutate_only_its_disposable_tree(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            repo = root / "repo"
            repo.mkdir()
            (repo / "source.txt").write_text("before\n", encoding="utf-8")
            baseline = capture_baseline(repo, root / "baselines")
            (repo / "source.txt").write_text("candidate\n", encoding="utf-8")
            config_path = root / "config.json"
            config_path.write_text(
                json.dumps(materialize_config("gpt-5.6-sol")), encoding="utf-8"
            )
            requirements = ("Keep the public result correct.",)
            candidate = capture_candidate(
                repo,
                requirements,
                baseline,
                config_path=config_path,
                archive_root=root / "candidates",
            )
            self.assertEqual(candidate["status"], "candidate_captured")
            graph = FeedbackGraph(
                source_hash=candidate["checkpoint_key"],
                behaviors=(
                    Behavior("B1", "keep result correct", ("R1",), ("result",), 1, 1, 1),
                ),
                failure_modes=(
                    FailureMode("F1", "B1", "result is wrong", "runtime", (), (), 1),
                ),
                verifiers=(
                    VerifierSpec(
                        verifier_id="branch-check",
                        kind="command",
                        cost=1,
                        blocking=True,
                        failure_modes=("F1",),
                        command=(
                            sys.executable,
                            "-c",
                            "from pathlib import Path; "
                            "Path('source.txt').write_text('verifier\\n'); "
                            "raise SystemExit(1)",
                        ),
                    ),
                ),
                shared_blind_spots=(),
            )
            plan = {
                **candidate,
                "phase": "plan",
                "status": "planned",
                "graph": to_jsonable(graph),
                "verifier_count": 1,
                "verifier_ids": ["branch-check"],
            }
            plan_path = root / "plan.json"
            plan_path.write_text(json.dumps(plan), encoding="utf-8")

            branch = run_verifier_branch(
                repo,
                requirements,
                baseline,
                plan_path=plan_path,
                config_path=config_path,
                verifier_id="branch-check",
            )

            self.assertEqual(branch["status"], "complete")
            self.assertTrue(branch["branch_mutated"])
            self.assertEqual(branch["result"]["verdict"], "fail")
            self.assertTrue(branch["result"]["reproducible"])
            branch_path = root / "branch.json"
            branch_path.write_text(json.dumps(branch), encoding="utf-8")
            matrix = assemble_branch_matrix(plan_path, (branch_path,))
            self.assertEqual(matrix["status"], "complete")
            self.assertTrue(matrix["producer_untouched_by_design"])
            self.assertEqual(
                matrix["eligible_reproducible_failure_verifiers"],
                ["branch-check"],
            )

    def test_disposable_branch_runner_rejects_another_workspace(self) -> None:
        runner = DisposableBranchCodexRunner(Path("/branch"))
        config = runner.branch_config(Path("/branch"), RunConfig())
        self.assertEqual(config.sandbox, "danger-full-access")
        self.assertFalse(config.network_access)
        with self.assertRaisesRegex(RuntimeError, "non-branch"):
            runner.branch_config(Path("/producer"), RunConfig())

    def test_nonportable_selected_evidence_becomes_no_op(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config_path = root / "config.json"
            config_path.write_text(
                json.dumps(default_original_config_payload()), encoding="utf-8"
            )
            graph = FeedbackGraph(
                source_hash="checkpoint",
                behaviors=(
                    Behavior("B1", "public behavior", (), ("result",), 1, 1, 1),
                ),
                failure_modes=(
                    FailureMode("F1", "B1", "behavior fails", "runtime", (), (), 1),
                ),
                verifiers=(
                    VerifierSpec(
                        verifier_id="selected",
                        kind="codex_agent",
                        cost=1,
                        blocking=True,
                        failure_modes=("F1",),
                        estimated_detection={"F1": 0.9},
                    ),
                ),
                shared_blind_spots=(),
            )
            report_path = root / "matrix.json"
            report_path.write_text(
                json.dumps(
                    {
                        "checkpoint_key": "checkpoint",
                        "candidate_files": ["candidate.py"],
                        "graph": to_jsonable(graph),
                        "results": [
                            {
                                "verifier_id": "selected",
                                "verdict": "fail",
                                "blocking": True,
                                "reproducible": True,
                                "failure_modes": ["F1"],
                                "evidence": [
                                    {
                                        "oracle_origin": "requirement_derived_runtime",
                                        "command": ["python", "temporary_check.py"],
                                        "failure_modes": ["F1"],
                                    }
                                ],
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )

            packet = feedback_packet(report_path, config_path)

            self.assertEqual(packet["status"], "no_eligible_feedback")
            self.assertEqual(packet["selected_eligible_verifiers"], [])
            self.assertEqual(packet["feedback"], "")

    def test_feedback_replay_rejects_disappearing_verifier_scripts(self) -> None:
        record = {
            "evidence": [
                {
                    "oracle_origin": "requirement_derived_runtime",
                    "command": ["python", "verifier_checks/check.py"],
                    "failure_modes": ["F1"],
                },
                {
                    "oracle_origin": "requirement_derived_runtime",
                    "command": ["python", "-c", "raise AssertionError('F1')"],
                    "failure_modes": ["F1"],
                },
                {
                    "oracle_origin": "requirement_derived_runtime",
                    "command": ["python", "candidate_check.py"],
                    "failure_modes": ["F1"],
                },
            ]
        }

        eligible = _eligible_evidence(
            record,
            {"F1"},
            frozenset({"candidate_check.py"}),
        )

        self.assertEqual(
            [item["command"] for item in eligible],
            [
                ["python", "-c", "raise AssertionError('F1')"],
                ["python", "candidate_check.py"],
            ],
        )

    def test_candidate_archive_restores_exact_tree(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            repo = root / "repo"
            archives = root / "archives"
            repo.mkdir()
            (repo / "kept.txt").write_text("before\n", encoding="utf-8")
            (repo / "deleted.txt").write_text("delete\n", encoding="utf-8")
            baseline = capture_baseline(repo, root / "baselines")

            (repo / "kept.txt").write_text("after\n", encoding="utf-8")
            (repo / "deleted.txt").unlink()
            (repo / "added.txt").write_text("added\n", encoding="utf-8")
            candidate = capture_baseline(repo, archives)
            archive = Path(candidate["archive_path"])

            (repo / "kept.txt").write_text("before\n", encoding="utf-8")
            (repo / "deleted.txt").write_text("delete\n", encoding="utf-8")
            (repo / "added.txt").unlink()
            baseline_path = root / "baseline.json"
            baseline_path.write_text(json.dumps(baseline), encoding="utf-8")

            restored = restore_candidate(
                repo,
                archive,
                archive_sha256=hashlib.sha256(archive.read_bytes()).hexdigest(),
                baseline_path=baseline_path,
                expected_tree=candidate["tree_hash"],
            )
            self.assertEqual(restored["status"], "restored")
            self.assertEqual((repo / "kept.txt").read_text(), "after\n")
            self.assertEqual((repo / "added.txt").read_text(), "added\n")
            self.assertFalse((repo / "deleted.txt").exists())

    def test_matrix_stops_before_modeling_when_candidate_is_not_replayable(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            repo = root / "repo"
            repo.mkdir()
            (repo / "source.txt").write_text("before\n", encoding="utf-8")
            baseline = capture_baseline(repo, root / "baselines")
            (repo / "state.db").symlink_to("/var/lib/task-state.db")
            config_path = root / "config.json"
            config_path.write_text(
                json.dumps(materialize_config("gpt-5.6-sol")), encoding="utf-8"
            )

            result = run_matrix(
                repo,
                ("Preserve and update the task state",),
                baseline,
                config_path=config_path,
                candidate_archive_root=root / "candidate-archives",
                max_verifiers=8,
            )

            self.assertEqual(result["status"], "candidate_not_replayable")
            self.assertEqual(result["verifier_count"], 0)
            self.assertEqual(result["candidate_archive_skipped_files"], ["state.db"])
            self.assertEqual(result["unreplayable_changed_files"], ["state.db"])
            self.assertNotIn("graph", result)

    def test_full_candidate_archive_restores_binary_file(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            repo = root / "repo"
            repo.mkdir()
            (repo / "source.txt").write_text("before\n", encoding="utf-8")
            baseline = capture_baseline(repo, root / "baselines")
            binary = b"SQLite format 3\x00" + bytes(range(256))
            (repo / "state.db").write_bytes(binary)
            _, files, hashes = hash_tree_manifest(repo)
            archive = archive_baseline(
                repo,
                files=files,
                file_hashes=hashes,
                tree_hash=hash_tree_manifest(repo)[0],
                archive_root=root / "candidate-archives",
                session_id="binary-candidate",
                task_epoch=1,
                include_binary=True,
            )
            expected_tree = hash_tree_manifest(repo)[0]
            (repo / "state.db").unlink()
            baseline_path = root / "baseline.json"
            baseline_path.write_text(json.dumps(baseline), encoding="utf-8")

            restored = restore_candidate(
                repo,
                archive,
                archive_sha256=hashlib.sha256(archive.read_bytes()).hexdigest(),
                baseline_path=baseline_path,
                expected_tree=expected_tree,
            )

            self.assertEqual(restored["status"], "restored")
            self.assertEqual((repo / "state.db").read_bytes(), binary)
            self.assertEqual(restored["skipped_file_count"], 0)

    def test_outer_container_runner_only_disables_sandbox_for_a_copy(self) -> None:
        runner = OuterContainerCopyCodexRunner(Path("/producer"))
        config = runner.copy_config(
            Path("/tmp/verifier-copy"),
            RunConfig(sandbox="read-only", network_access=True),
        )
        self.assertEqual(config.sandbox, "danger-full-access")
        self.assertFalse(config.network_access)
        with self.assertRaisesRegex(RuntimeError, "producer worktree"):
            runner.copy_config(Path("/producer"), RunConfig())

    def test_workspace_resolution_requires_one_unique_repository(self) -> None:
        self.assertEqual(
            select_unique_workspace(["/testbed", "/testbed", ""]),
            Path("/testbed"),
        )
        with self.assertRaisesRegex(RuntimeError, "discovered none"):
            select_unique_workspace([])
        with self.assertRaisesRegex(RuntimeError, "/app, /testbed"):
            select_unique_workspace(["/testbed", "/app"])

    def test_workspace_resolution_prefers_current_enclosing_git_root(self) -> None:
        self.assertEqual(
            select_workspace(
                "/workspace/project/src",
                ["/opt/controller", "/workspace/project", "/workspace/project/src/lib"],
            ),
            Path("/workspace/project"),
        )

    def test_workspace_resolution_supports_non_git_task_directory(self) -> None:
        self.assertEqual(
            select_workspace("/app", ["/opt/controller", "/tools/another-repo"]),
            Path("/app"),
        )

    def test_workspace_resolution_rejects_root_fallback(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "Unsafe task working directory"):
            select_workspace("/", [])
        with self.assertRaisesRegex(RuntimeError, "not reported"):
            select_workspace("", ["/app"])

    def test_featurebench_smoke_is_source_and_runtime_pinned(self) -> None:
        path = (
            PROJECT_ROOT
            / "experiments"
            / "coding_verifier_matrix"
            / "configs"
            / "featurebench-wav2vec2-matrix-v1.json"
        )
        config = json.loads(path.read_text(encoding="utf-8"))
        agent = config["agents"][0]
        dataset = config["datasets"][0]

        self.assertEqual(agent["model_name"], "gpt-5.6-sol")
        self.assertEqual(agent["kwargs"]["version"], "0.147.0")
        self.assertRegex(agent["kwargs"]["graft_commit"], r"^[0-9a-f]{40}$")
        self.assertEqual(dataset["name"], "featurebench-lite")
        self.assertEqual(dataset["version"], "1.0")
        self.assertEqual(
            dataset["task_names"],
            [
                "huggingface__transformers.e2e8dbed."
                "test_processing_wav2vec2.4f660c78.lv1"
            ],
        )

    def test_tb3_causal_matrix_is_source_and_runtime_pinned(self) -> None:
        path = (
            PROJECT_ROOT
            / "experiments"
            / "coding_verifier_matrix"
            / "configs"
            / "tb3-cli-simplex-matrix-v1.json"
        )
        config = json.loads(path.read_text(encoding="utf-8"))
        agent = config["agents"][0]
        dataset = config["datasets"][0]

        self.assertEqual(agent["model_name"], "gpt-5.6-sol")
        self.assertEqual(agent["kwargs"]["version"], "0.147.0")
        self.assertRegex(agent["kwargs"]["graft_commit"], r"^[0-9a-f]{40}$")
        self.assertEqual(
            dataset["repo"], "harbor-framework/terminal-bench@v3.0.0"
        )
        self.assertEqual(dataset["task_names"], ["cli-2ph-simplex"])

    def test_tb3_causal_continuation_is_source_bound(self) -> None:
        path = (
            PROJECT_ROOT
            / "experiments"
            / "coding_verifier_matrix"
            / "configs"
            / "tb3-cli-simplex-continuation-v1.json"
        )
        config = json.loads(path.read_text(encoding="utf-8"))
        agent = config["agents"][0]
        kwargs = agent["kwargs"]
        dataset = config["datasets"][0]

        self.assertEqual(
            agent["name"],
            "experiments.coding_verifier_matrix.matrix_continuation_agent:"
            "MatrixContinuationCodex",
        )
        for key in (
            "matrix_sha256",
            "candidate_archive_sha256",
            "session_sha256",
        ):
            self.assertRegex(kwargs[key], r"^[0-9a-f]{64}$")
        self.assertRegex(kwargs["graft_commit"], r"^[0-9a-f]{40}$")
        self.assertRegex(kwargs["expected_thread_id"], r"^[0-9a-f-]{36}$")
        self.assertEqual(
            dataset["repo"], "harbor-framework/terminal-bench@v3.0.0"
        )
        self.assertEqual(dataset["task_names"], ["cli-2ph-simplex"])

    def test_tb3_resource_bounded_cohort_configs_are_pinned(self) -> None:
        root = (
            PROJECT_ROOT
            / "experiments"
            / "coding_verifier_matrix"
            / "configs"
        )
        expected = {
            "tb3-production-planning-matrix-v1.json": "production-planning",
            "tb3-kv-live-surgery-matrix-v1.json": "kv-live-surgery",
        }
        for filename, task_name in expected.items():
            with self.subTest(filename=filename):
                config = json.loads((root / filename).read_text(encoding="utf-8"))
                agent = config["agents"][0]
                dataset = config["datasets"][0]
                self.assertEqual(agent["model_name"], "gpt-5.6-sol")
                self.assertEqual(agent["kwargs"]["version"], "0.147.0")
                self.assertEqual(
                    agent["kwargs"]["graft_commit"],
                    "85043df056dd9967f3f88ab036e47eda93911d0b",
                )
                self.assertEqual(
                    dataset["repo"], "harbor-framework/terminal-bench@v3.0.0"
                )
                self.assertEqual(dataset["task_names"], [task_name])

    def test_featurebench_fallback_is_source_and_runtime_pinned(self) -> None:
        path = (
            PROJECT_ROOT
            / "experiments"
            / "coding_verifier_matrix"
            / "configs"
            / "featurebench-metaflow-matrix-v1.json"
        )
        config = json.loads(path.read_text(encoding="utf-8"))
        agent = config["agents"][0]
        dataset = config["datasets"][0]

        self.assertEqual(agent["model_name"], "gpt-5.6-sol")
        self.assertEqual(agent["kwargs"]["version"], "0.147.0")
        self.assertEqual(
            agent["kwargs"]["graft_commit"],
            "9c4f565af06140c0b02f752fbeeb455f9229b4f3",
        )
        self.assertEqual(dataset["name"], "featurebench-lite")
        self.assertEqual(dataset["version"], "1.0")
        self.assertEqual(
            dataset["task_names"],
            ["netflix__metaflow.b390a8d4.test_stub_generator.7bf08c98.lv1"],
        )

    def test_featurebench_isolated_copy_rerun_is_pinned(self) -> None:
        path = (
            PROJECT_ROOT
            / "experiments"
            / "coding_verifier_matrix"
            / "configs"
            / "featurebench-metaflow-matrix-v2.json"
        )
        config = json.loads(path.read_text(encoding="utf-8"))
        agent = config["agents"][0]
        dataset = config["datasets"][0]

        self.assertEqual(config["job_name"], "featurebench-metaflow-verifier-matrix-v2")
        self.assertEqual(agent["model_name"], "gpt-5.6-sol")
        self.assertEqual(agent["kwargs"]["version"], "0.147.0")
        self.assertEqual(
            agent["kwargs"]["graft_commit"],
            "52f8974c956058409ae98c0542a61066f5964a54",
        )
        self.assertEqual(dataset["name"], "featurebench-lite")
        self.assertEqual(dataset["version"], "1.0")
        self.assertEqual(
            dataset["task_names"],
            ["netflix__metaflow.b390a8d4.test_stub_generator.7bf08c98.lv1"],
        )

    def test_featurebench_same_thread_continuation_is_pinned(self) -> None:
        path = (
            PROJECT_ROOT
            / "experiments"
            / "coding_verifier_matrix"
            / "configs"
            / "featurebench-metaflow-continuation-v1.json"
        )
        config = json.loads(path.read_text(encoding="utf-8"))
        agent = config["agents"][0]
        kwargs = agent["kwargs"]

        self.assertEqual(config["job_name"], "featurebench-metaflow-continuation-v1")
        self.assertEqual(agent["model_name"], "gpt-5.6-sol")
        self.assertEqual(kwargs["version"], "0.147.0")
        self.assertEqual(
            kwargs["graft_commit"],
            "2fce6cc29a0eb65ca349910614709374b790d6f4",
        )
        self.assertRegex(kwargs["matrix_sha256"], r"^[0-9a-f]{64}$")
        self.assertRegex(kwargs["candidate_archive_sha256"], r"^[0-9a-f]{64}$")
        self.assertRegex(kwargs["session_sha256"], r"^[0-9a-f]{64}$")
        self.assertEqual(
            config["datasets"][0]["task_names"],
            ["netflix__metaflow.b390a8d4.test_stub_generator.7bf08c98.lv1"],
        )

    def test_featurebench_pandas_prospective_trial_is_pinned(self) -> None:
        path = (
            PROJECT_ROOT
            / "experiments"
            / "coding_verifier_matrix"
            / "configs"
            / "featurebench-pandas-iceberg-prospective-v1.json"
        )
        config = json.loads(path.read_text(encoding="utf-8"))
        agent = config["agents"][0]

        self.assertEqual(
            config["job_name"], "featurebench-pandas-iceberg-prospective-v1"
        )
        self.assertEqual(agent["model_name"], "gpt-5.6-sol")
        self.assertEqual(agent["kwargs"]["version"], "0.147.0")
        self.assertEqual(
            agent["kwargs"]["graft_commit"],
            "5d3155f4bc7122fab894294985fad2fb1e4588eb",
        )
        self.assertEqual(
            config["datasets"][0]["task_names"],
            ["pandas-dev__pandas.82fa2715.test_iceberg.85771c70.lv2"],
        )

    def test_continuation_session_upload_has_no_frozen_calendar_path(self) -> None:
        source = (
            PROJECT_ROOT
            / "experiments"
            / "coding_verifier_matrix"
            / "matrix_continuation_agent.py"
        ).read_text(encoding="utf-8")

        self.assertIn("session_relative = self.local_session.relative_to", source)
        self.assertNotIn("sessions/2026/08/13", source)

    def test_featurebench_pandas_continuation_is_source_bound(self) -> None:
        path = (
            PROJECT_ROOT
            / "experiments"
            / "coding_verifier_matrix"
            / "configs"
            / "featurebench-pandas-iceberg-continuation-v1.json"
        )
        config = json.loads(path.read_text(encoding="utf-8"))
        kwargs = config["agents"][0]["kwargs"]

        self.assertEqual(
            config["job_name"], "featurebench-pandas-iceberg-continuation-v1"
        )
        self.assertEqual(
            kwargs["graft_commit"],
            "bebd552613c02c92f370e4a6b8ac71eef89059bd",
        )
        for key in ("matrix_sha256", "candidate_archive_sha256", "session_sha256"):
            self.assertRegex(kwargs[key], r"^[0-9a-f]{64}$")
        self.assertEqual(
            kwargs["expected_thread_id"],
            "019ff905-92c7-7800-af45-7b46be07848a",
        )

    def test_baseline_capture_is_external_and_detects_changed_paths(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            repo = root / "repo"
            archive_root = root / "archives"
            repo.mkdir()
            (repo / "source.txt").write_text("before\n", encoding="utf-8")

            baseline = capture_baseline(repo, archive_root)
            (repo / "source.txt").write_text("after\n", encoding="utf-8")
            (repo / "added.txt").write_text("new\n", encoding="utf-8")

            self.assertEqual(baseline["status"], "captured")
            self.assertTrue(Path(baseline["archive_path"]).is_file())
            self.assertRegex(baseline["archive_sha256"], r"^[0-9a-f]{64}$")
            self.assertNotEqual(Path(baseline["archive_path"]).parent, repo)
            self.assertEqual(
                _changed_paths(
                    {"source.txt": "new", "added.txt": "new"},
                    baseline["file_hashes"],
                ),
                ("added.txt", "source.txt"),
            )

    def test_matrix_config_pins_every_model_stage_and_verifier(self) -> None:
        payload = materialize_config("gpt-test")
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.json"
            path.write_text(json.dumps(payload), encoding="utf-8")
            config = load_config(path)

        self.assertEqual(config.behavior_modeler.model, "gpt-test")
        self.assertEqual(config.verifier_planner.model, "gpt-test")
        self.assertTrue(config.verifier_templates)
        self.assertTrue(
            all(item.model == "gpt-test" for item in config.verifier_templates)
        )


if __name__ == "__main__":
    unittest.main()
