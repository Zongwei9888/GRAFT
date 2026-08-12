from __future__ import annotations

import hashlib
import json
import os
import subprocess
import tempfile
from dataclasses import replace
from pathlib import Path
from typing import Any

from experiments.value_aware_gate.run_selector_pilot import (
    FrozenGraphBuilder,
    _records_from_turn,
)
from graft.codex.cli_runner import CliCodexRunner
from graft.codex.hooks import _promotion_from_decision
from graft.codex.telemetry import summarize_records
from graft.controller import GraftController
from graft.evidence.baseline_archive import archive_baseline
from graft.evidence.checkpoint_archive import archive_checkpoint
from graft.evidence.snapshot import freeze_source, hash_tree_manifest
from graft.modeling import CodexFeedbackGraphBuilder
from graft.registry import default_value_aware_config_payload, load_config
from graft.schema import DecisionKind, RunConfig, SourceSnapshot, to_jsonable


ROOT = Path(__file__).resolve().parents[2]
EXPERIMENT = Path(__file__).resolve().parent
ARTIFACT_ROOT = ROOT / "artifacts" / "promotion-e2e"
SESSION_LABEL = "current-promotion-e2e"


def main() -> int:
    manifest = json.loads((EXPERIMENT / "task.json").read_text(encoding="utf-8"))
    task = str(manifest["task"])
    model = str(manifest["model"])
    requirements = (task,)
    ARTIFACT_ROOT.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory(prefix="graft-promotion-e2e-") as temporary:
        run_root = Path(temporary)
        workspace = run_root / "producer-workspace"
        workspace.mkdir()
        (workspace / ".gitignore").write_text(
            ".graft/\n__pycache__/\n", encoding="utf-8"
        )
        _git(workspace, "init")
        _git(workspace, "add", ".gitignore")
        _git(
            workspace,
            "-c",
            "user.name=GRAFT Pilot",
            "-c",
            "user.email=graft-pilot@example.invalid",
            "commit",
            "-m",
            "task baseline",
        )

        config_path = run_root / "value-aware.json"
        config_path.write_text(
            json.dumps(_experiment_config(model), ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        config = load_config(config_path)
        baseline_tree, baseline_files, baseline_hashes = hash_tree_manifest(workspace)
        baseline_archive = archive_baseline(
            workspace,
            files=baseline_files,
            file_hashes=baseline_hashes,
            tree_hash=baseline_tree,
            archive_root=ARTIFACT_ROOT / "baselines",
            session_id=SESSION_LABEL,
            task_epoch=1,
        )

        runner = CliCodexRunner()
        print("[E2E] starting Native Codex producer with hooks disabled", flush=True)
        producer = runner.start_thread(
            task,
            workspace,
            RunConfig(
                sandbox="workspace-write",
                network_access=False,
                model=model,
                timeout_s=600,
                ephemeral=False,
                isolate_config=True,
                disable_hooks=True,
            ),
        )
        if producer.return_code != 0 or not producer.thread_id:
            raise RuntimeError(
                f"producer failed: return={producer.return_code}; {producer.stderr}"
            )

        first_snapshot = _snapshot(
            workspace,
            requirements,
            config_path,
            config.environment_fingerprint,
            baseline_tree,
            baseline_files,
            baseline_hashes,
            baseline_archive,
        )
        first_archive = _capture(first_snapshot, producer.thread_id, 0)
        first_records = _records_from_turn(
            producer,
            snapshot=first_snapshot,
            session_id=producer.thread_id,
            task_epoch=1,
        )
        first_evidence = summarize_records(first_records, task_epoch=1)
        first_evaluation = _held_out_evaluate(workspace)
        _write_json(
            ARTIFACT_ROOT / "producer-events.json",
            {"turn": to_jsonable(producer), "records": to_jsonable(first_records)},
        )
        print(
            f"[E2E] first checkpoint {first_snapshot.checkpoint_key[:16]}; "
            f"held-out {first_evaluation['passed']}/{first_evaluation['total']}",
            flush=True,
        )

        print("[E2E] dynamically building the first GRAFT graph", flush=True)
        first_graph = CodexFeedbackGraphBuilder().build(
            first_snapshot,
            requirements,
            config,
            config_path=config_path,
            producer_evidence=first_evidence,
            promotion=None,
        )
        first_controller = GraftController(
            config,
            config_path=config_path,
            graph_builder=FrozenGraphBuilder(first_graph),
            report_root=ARTIFACT_ROOT / "reports",
        )
        first_decision = first_controller.verify(
            workspace,
            requirements=requirements,
            session_id=producer.thread_id,
            snapshot=first_snapshot,
            producer_evidence=first_evidence,
            available_budget=config.budget,
        )
        print(f"[E2E] first GRAFT decision: {first_decision.kind.value}", flush=True)

        continuation: dict[str, Any] | None = None
        if first_decision.kind == DecisionKind.CONTINUE_WITH_EVIDENCE:
            print("[E2E] resuming the same Codex thread with GRAFT evidence", flush=True)
            repaired = runner.continue_thread(
                producer.thread_id,
                first_decision.reason,
                workspace,
                RunConfig(
                    sandbox="workspace-write",
                    network_access=False,
                    model=model,
                    timeout_s=600,
                    isolate_config=True,
                    disable_hooks=True,
                ),
            )
            if repaired.return_code != 0:
                raise RuntimeError(
                    f"continuation failed: return={repaired.return_code}; {repaired.stderr}"
                )
            repaired_snapshot = _snapshot(
                workspace,
                requirements,
                config_path,
                config.environment_fingerprint,
                baseline_tree,
                baseline_files,
                baseline_hashes,
                baseline_archive,
            )
            repaired_archive = _capture(repaired_snapshot, producer.thread_id, 1)
            repaired_records = _records_from_turn(
                repaired,
                snapshot=repaired_snapshot,
                session_id=producer.thread_id,
                task_epoch=1,
            )
            combined_evidence = summarize_records(
                (*first_records, *repaired_records), task_epoch=1
            )
            repaired_evaluation = _held_out_evaluate(workspace)
            promotion = _promotion_from_decision(first_decision)
            print("[E2E] dynamically building the promotion graph", flush=True)
            promotion_graph = CodexFeedbackGraphBuilder().build(
                repaired_snapshot,
                requirements,
                config,
                config_path=config_path,
                producer_evidence=combined_evidence,
                promotion=promotion,
            )
            promotion_controller = GraftController(
                config,
                config_path=config_path,
                graph_builder=FrozenGraphBuilder(promotion_graph),
                report_root=ARTIFACT_ROOT / "reports",
            )
            spent_nominal = (
                first_decision.selection.total_cost
                if first_decision.selection is not None
                else 0.0
            )
            promotion_decision = promotion_controller.verify(
                workspace,
                requirements=requirements,
                session_id=producer.thread_id,
                snapshot=repaired_snapshot,
                producer_evidence=combined_evidence,
                available_budget=max(0.0, config.budget - spent_nominal),
                promotion=promotion,
            )
            _write_json(
                ARTIFACT_ROOT / "continuation-events.json",
                {"turn": to_jsonable(repaired), "records": to_jsonable(repaired_records)},
            )
            continuation = {
                "returned_thread_id": repaired.thread_id,
                "same_thread": repaired.thread_id == producer.thread_id,
                "duration_s": repaired.duration_s,
                "usage": dict(repaired.usage),
                "final_response": repaired.final_response,
                "checkpoint": repaired_snapshot.checkpoint_key,
                "checkpoint_archive": _artifact(repaired_archive),
                "held_out_after_repair": repaired_evaluation,
                "promotion_requirement": to_jsonable(promotion),
                "promotion_graph": _graph_summary(promotion_graph),
                "promotion_decision": to_jsonable(promotion_decision),
            }
            print(
                f"[E2E] repaired held-out {repaired_evaluation['passed']}/"
                f"{repaired_evaluation['total']}; promotion "
                f"{promotion_decision.promotion_outcome}",
                flush=True,
            )
        else:
            print("[E2E] no eligible continuation; none was manufactured", flush=True)

        result = {
            "protocol_version": 1,
            "repository_commit": _run_text(ROOT, "git", "rev-parse", "HEAD"),
            "codex_version": _run_text(ROOT, "codex", "--version"),
            "model": model,
            "task_sha256": _sha256(task),
            "protocol_sha256": _sha256(
                (EXPERIMENT / "PROTOCOL.md").read_text(encoding="utf-8")
            ),
            "producer": {
                "thread_id": producer.thread_id,
                "duration_s": producer.duration_s,
                "usage": dict(producer.usage),
                "final_response": producer.final_response,
            },
            "first_checkpoint": first_snapshot.checkpoint_key,
            "first_checkpoint_archive": _artifact(first_archive),
            "held_out_before_graft": first_evaluation,
            "first_graph": _graph_summary(first_graph),
            "first_decision": to_jsonable(first_decision),
            "continuation": continuation,
            "interpretation_guard": (
                "One authored task tests mechanism execution only; it is not an effectiveness "
                "estimate or evidence of benchmark-level improvement."
            ),
        }
        _write_json(ARTIFACT_ROOT / "result.json", result)
        print(json.dumps(result, ensure_ascii=False, indent=2), flush=True)
    return 0


def _experiment_config(model: str) -> dict[str, Any]:
    payload = default_value_aware_config_payload()
    payload["budget"] = 6.0
    payload["max_feedback_rounds"] = 1
    payload["environment_fingerprint"] = "promotion-e2e-v1"
    for key in ("behavior_modeler", "verifier_planner"):
        payload["modeling"][key]["model"] = model
        payload["modeling"][key]["timeout_s"] = 240
    payload["modeling"]["completion_gate"]["model"] = model
    payload["verifier_templates"] = [
        template
        for template in payload["verifier_templates"]
        if template["id"] in {"repository-evidence-agent", "test-agent"}
    ]
    for template in payload["verifier_templates"]:
        template["model"] = model
        template["timeout_s"] = 300
    payload["selection"].update(
        {
            "max_verifiers": 1,
            "min_net_value": 0.0,
            "uncertainty_penalty": 0.10,
            "repair_value": 3.0,
            "regression_cost": 0.25,
            "wall_time_budget_s": 1800.0,
            "model_cost_budget_usd": 20.0,
            "wall_time_weight": 0.05,
            "model_cost_weight": 0.05,
            "nominal_cost_weight": 0.02,
        }
    )
    payload["_note"] = (
        "Pre-registered mechanism-trigger pilot using only generic LLM evidence templates."
    )
    return payload


def _snapshot(
    workspace: Path,
    requirements: tuple[str, ...],
    config_path: Path,
    environment_fingerprint: str,
    baseline_tree: str,
    baseline_files: tuple[str, ...],
    baseline_hashes: dict[str, str],
    baseline_archive: Path,
) -> SourceSnapshot:
    return freeze_source(
        workspace,
        requirements=requirements,
        config_path=config_path,
        environment_fingerprint=environment_fingerprint,
        baseline_tree_hash=baseline_tree,
        baseline_files=baseline_files,
        baseline_file_hashes=baseline_hashes,
        baseline_archive_path=str(baseline_archive),
    )


def _capture(snapshot: SourceSnapshot, session_id: str, round_id: int) -> Path | None:
    previous = os.environ.get("GRAFT_CHECKPOINT_ARCHIVE_HOME")
    os.environ["GRAFT_CHECKPOINT_ARCHIVE_HOME"] = str(ARTIFACT_ROOT / "checkpoints")
    try:
        return archive_checkpoint(
            snapshot,
            session_id=session_id,
            task_epoch=1,
            verification_round=round_id,
        )
    finally:
        if previous is None:
            os.environ.pop("GRAFT_CHECKPOINT_ARCHIVE_HOME", None)
        else:
            os.environ["GRAFT_CHECKPOINT_ARCHIVE_HOME"] = previous


def _held_out_evaluate(workspace: Path) -> dict[str, Any]:
    program = workspace / "pathfilter.py"
    cases: list[dict[str, Any]] = []
    valid = (
        (
            "basename-at-any-depth",
            ["*.py"],
            ["a.py", "src/a.py", "src/a.js", "src/py"],
            ["a.py", "src/a.py"],
        ),
        (
            "slash-pattern-is-root-anchored",
            ["src/*.py"],
            ["src/a.py", "src/lib/a.py", "x/src/a.py"],
            ["src/a.py"],
        ),
        (
            "double-star-zero-or-more-segments",
            ["src/**/test?.py"],
            ["src/test1.py", "src/a/test2.py", "src/a/b/testx.py", "test3.py"],
            ["src/test1.py", "src/a/test2.py", "src/a/b/testx.py"],
        ),
        (
            "ordered-exclude-and-reinclude",
            ["**", "!build/**", "build/keep.txt"],
            ["a", "build/a", "build/nested/b", "build/keep.txt"],
            ["a", "build/keep.txt"],
        ),
        (
            "escaped-leading-control-characters",
            ["\\!important", "\\#notes"],
            ["!important", "docs/!important", "#notes", "other"],
            ["!important", "docs/!important", "#notes"],
        ),
        (
            "comments-empty-and-class-negation",
            ["", "# ignored", "file[!0-9].txt"],
            ["filea.txt", "file7.txt", "x/file-.txt"],
            ["filea.txt", "x/file-.txt"],
        ),
        (
            "class-range-and-question",
            ["data/[a-c]?.json"],
            ["data/a1.json", "data/cx.json", "data/d1.json", "data/a12.json"],
            ["data/a1.json", "data/cx.json"],
        ),
        (
            "duplicates-and-last-match-win",
            ["*.txt", "!secret.txt", "secret.txt"],
            ["secret.txt", "a.txt", "secret.txt", "a.bin"],
            ["secret.txt", "a.txt", "secret.txt"],
        ),
        (
            "double-star-middle-and-zero-prefix",
            ["**/cache/**"],
            ["cache/a", "src/cache/a", "src/a/cache/deep/x", "cached/a"],
            ["cache/a", "src/cache/a", "src/a/cache/deep/x"],
        ),
        (
            "regex-metacharacters-are-literal",
            ["a+b.(txt)"],
            ["a+b.(txt)", "xa+b.(txt)", "aabxtxt"],
            ["a+b.(txt)"],
        ),
    )
    invalid = (
        ("invalid-top-level", []),
        ("invalid-field-set", {"patterns": [], "paths": [], "extra": 1}),
        ("invalid-member-type", {"patterns": ["*", 1], "paths": ["a"]}),
        ("invalid-absolute-path", {"patterns": ["**"], "paths": ["/a"]}),
        ("invalid-empty-segment", {"patterns": ["**"], "paths": ["a//b"]}),
        ("invalid-dot-segment", {"patterns": ["**"], "paths": ["a/../b"]}),
        ("invalid-dangling-escape", {"patterns": ["abc\\"], "paths": ["abc"]}),
        ("invalid-unclosed-class", {"patterns": ["a[bc"], "paths": ["ab"]}),
        ("invalid-empty-class", {"patterns": ["a[]b"], "paths": ["ab"]}),
        ("invalid-embedded-double-star", {"patterns": ["ab**cd"], "paths": ["abcd"]}),
    )
    if not program.is_file():
        return {
            "passed": 0,
            "total": len(valid) + len(invalid) + 3,
            "all_passed": False,
            "cases": [{"id": "program-present", "passed": False}],
        }

    for case_id, patterns, paths, expected in valid:
        completed = _invoke(program, workspace, {"patterns": patterns, "paths": paths})
        expected_stdout = json.dumps(expected, ensure_ascii=False, separators=(",", ":")) + "\n"
        cases.append(
            {
                "id": case_id,
                "passed": completed["return_code"] == 0
                and completed["stderr"] == ""
                and completed["stdout"] == expected_stdout,
                **completed,
            }
        )
    for case_id, payload in invalid:
        completed = _invoke(program, workspace, payload)
        stderr_lines = [line for line in completed["stderr"].splitlines() if line.strip()]
        cases.append(
            {
                "id": case_id,
                "passed": completed["return_code"] == 2
                and completed["stdout"] == ""
                and len(stderr_lines) == 1
                and len(stderr_lines[0]) <= 240,
                **completed,
            }
        )

    malformed = subprocess.run(
        ["python3", str(program)],
        input="{\n",
        cwd=workspace,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=10,
        check=False,
    )
    cases.append(
        {
            "id": "invalid-json",
            "passed": malformed.returncode == 2
            and malformed.stdout == ""
            and len([line for line in malformed.stderr.splitlines() if line.strip()]) == 1,
            "return_code": malformed.returncode,
            "stdout": malformed.stdout,
            "stderr": malformed.stderr,
        }
    )
    cases.append(
        {
            "id": "readme-present",
            "passed": (workspace / "README.md").is_file()
            or (workspace / "README").is_file(),
        }
    )
    cases.append(
        {
            "id": "tests-present",
            "passed": any(workspace.glob("test*.py"))
            or any((workspace / "tests").glob("test*.py"))
            if (workspace / "tests").exists()
            else any(workspace.glob("test*.py")),
        }
    )
    passed = sum(bool(case["passed"]) for case in cases)
    return {
        "passed": passed,
        "total": len(cases),
        "all_passed": passed == len(cases),
        "cases": cases,
    }


def _invoke(program: Path, cwd: Path, payload: Any) -> dict[str, Any]:
    completed = subprocess.run(
        ["python3", str(program)],
        input=json.dumps(payload, ensure_ascii=False),
        cwd=cwd,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=10,
        check=False,
    )
    return {
        "return_code": completed.returncode,
        "stdout": completed.stdout,
        "stderr": completed.stderr,
    }


def _graph_summary(graph) -> dict[str, Any]:
    return {
        "behaviors": len(graph.behaviors),
        "failure_modes": len(graph.failure_modes),
        "verifiers": len(graph.verifiers),
        "shared_blind_spots": len(graph.shared_blind_spots),
        "uncertainties": list(graph.uncertainties),
        "stage_costs": to_jsonable(graph.stage_costs),
        "verifier_specs": to_jsonable(graph.verifiers),
    }


def _artifact(path: Path | None) -> dict[str, str] | None:
    if path is None:
        return None
    return {"path": str(path), "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}


def _git(cwd: Path, *args: str) -> None:
    completed = subprocess.run(
        ["git", *args], cwd=cwd, text=True, capture_output=True, check=False
    )
    if completed.returncode != 0:
        raise RuntimeError(completed.stderr)


def _run_text(cwd: Path, *command: str) -> str:
    return subprocess.run(
        command, cwd=cwd, text=True, capture_output=True, check=True
    ).stdout.strip()


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


if __name__ == "__main__":
    raise SystemExit(main())
