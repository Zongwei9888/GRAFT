from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

from experiments.promotion_e2e.rerun_after_match_fix import _posthoc_counterexamples
from experiments.promotion_e2e.run import (
    ARTIFACT_ROOT,
    EXPERIMENT,
    _artifact,
    _capture,
    _experiment_config,
    _graph_summary,
    _held_out_evaluate,
    _snapshot,
    _write_json,
)
from experiments.value_aware_gate.run_selector_pilot import (
    FrozenGraphBuilder,
    _baseline_manifest,
    _records_from_turn,
    _restore_checkpoint,
    _turn_result,
)
from graft.codex.cli_runner import CliCodexRunner
from graft.codex.hooks import _promotion_from_decision
from graft.codex.telemetry import summarize_records
from graft.controller import GraftController
from graft.modeling import CodexFeedbackGraphBuilder
from graft.registry import load_config
from graft.replay import load_report_graph
from graft.schema import DecisionKind, RunConfig, to_jsonable


ROOT = Path(__file__).resolve().parents[2]


def main() -> int:
    original = json.loads((ARTIFACT_ROOT / "result.json").read_text(encoding="utf-8"))
    manifest = json.loads((EXPERIMENT / "task.json").read_text(encoding="utf-8"))
    task = str(manifest["task"])
    model = str(manifest["model"])
    requirements = (task,)
    thread_id = str(original["producer"]["thread_id"])
    workspace = Path(original["first_decision"]["snapshot"]["root"])
    if workspace.exists() or workspace.parent.exists():
        raise RuntimeError(
            "refusing to overwrite an existing original workspace or its task root: "
            f"{workspace}"
        )
    workspace.mkdir(parents=True)

    checkpoint_archive = Path(original["first_checkpoint_archive"]["path"])
    original_report = Path(original["first_decision"]["report_path"])
    baseline_archive = Path(
        original["first_decision"]["snapshot"]["baseline_archive_path"]
    )
    baseline_tree, baseline_files, baseline_hashes = _baseline_manifest(baseline_archive)
    producer_turn = _turn_result(
        json.loads((ARTIFACT_ROOT / "producer-events.json").read_text(encoding="utf-8"))[
            "turn"
        ]
    )
    _restore_checkpoint(checkpoint_archive, workspace)
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

    config_path = workspace.parent / "value-aware.json"
    config_path.write_text(
        json.dumps(_experiment_config(model), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    config = load_config(config_path)
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
    if first_snapshot.checkpoint_key != original["first_checkpoint"]:
        raise RuntimeError(
            "restored original-path checkpoint mismatch: "
            f"expected {original['first_checkpoint']}, observed "
            f"{first_snapshot.checkpoint_key}"
        )
    first_graph = load_report_graph(original_report)
    first_records = _records_from_turn(
        producer_turn,
        snapshot=first_snapshot,
        session_id=thread_id,
        task_epoch=1,
    )
    first_evidence = summarize_records(first_records, task_epoch=1)
    original_evaluator_before = _held_out_evaluate(workspace)
    counterexamples_before = _posthoc_counterexamples(workspace)

    controller = GraftController(
        config,
        config_path=config_path,
        graph_builder=FrozenGraphBuilder(first_graph),
        report_root=ARTIFACT_ROOT / "original-path-reports",
    )
    print("[ORIGINAL-PATH] rerunning GRAFT on the exact checkpoint", flush=True)
    decision = controller.verify(
        workspace,
        requirements=requirements,
        session_id=thread_id,
        snapshot=first_snapshot,
        producer_evidence=first_evidence,
        available_budget=config.budget,
    )
    print(f"[ORIGINAL-PATH] decision: {decision.kind.value}", flush=True)
    if decision.kind != DecisionKind.CONTINUE_WITH_EVIDENCE:
        result = _base_result(
            original,
            workspace,
            first_snapshot.checkpoint_key,
            original_evaluator_before,
            counterexamples_before,
            decision,
        )
        result["continuation"] = None
        _write_json(ARTIFACT_ROOT / "original-path-result.json", result)
        print(json.dumps(result, ensure_ascii=False, indent=2), flush=True)
        return 0

    runner = CliCodexRunner()
    print("[ORIGINAL-PATH] resuming the original Codex thread", flush=True)
    repaired = runner.continue_thread(
        thread_id,
        decision.reason,
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
    repaired_archive = _capture(repaired_snapshot, thread_id, 1)
    repaired_records = _records_from_turn(
        repaired,
        snapshot=repaired_snapshot,
        session_id=thread_id,
        task_epoch=1,
    )
    combined_evidence = summarize_records(
        (*first_records, *repaired_records), task_epoch=1
    )
    original_evaluator_after = _held_out_evaluate(workspace)
    counterexamples_after = _posthoc_counterexamples(workspace)
    promotion = _promotion_from_decision(decision)

    print("[ORIGINAL-PATH] building and executing promotion verification", flush=True)
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
        report_root=ARTIFACT_ROOT / "original-path-reports",
    )
    spent_nominal = decision.selection.total_cost if decision.selection else 0.0
    promotion_decision = promotion_controller.verify(
        workspace,
        requirements=requirements,
        session_id=thread_id,
        snapshot=repaired_snapshot,
        producer_evidence=combined_evidence,
        available_budget=max(0.0, config.budget - spent_nominal),
        promotion=promotion,
    )

    _write_json(
        ARTIFACT_ROOT / "original-path-continuation-events.json",
        {"turn": to_jsonable(repaired), "records": to_jsonable(repaired_records)},
    )
    result = _base_result(
        original,
        workspace,
        first_snapshot.checkpoint_key,
        original_evaluator_before,
        counterexamples_before,
        decision,
    )
    result["continuation"] = {
        "returned_thread_id": repaired.thread_id,
        "same_thread": repaired.thread_id == thread_id,
        "workspace_checkpoint_changed": (
            repaired_snapshot.checkpoint_key != first_snapshot.checkpoint_key
        ),
        "duration_s": repaired.duration_s,
        "usage": dict(repaired.usage),
        "final_response": repaired.final_response,
        "checkpoint": repaired_snapshot.checkpoint_key,
        "checkpoint_archive": _artifact(repaired_archive),
        "original_evaluator_after": original_evaluator_after,
        "posthoc_counterexamples_after": counterexamples_after,
        "promotion_requirement": to_jsonable(promotion),
        "promotion_graph": _graph_summary(promotion_graph),
        "promotion_decision": to_jsonable(promotion_decision),
    }
    _write_json(ARTIFACT_ROOT / "original-path-result.json", result)
    print(
        "[ORIGINAL-PATH] "
        f"same_thread={result['continuation']['same_thread']}; "
        f"changed={result['continuation']['workspace_checkpoint_changed']}; "
        f"counterexamples={counterexamples_after['passed']}/"
        f"{counterexamples_after['total']}; "
        f"promotion={promotion_decision.promotion_outcome}",
        flush=True,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2), flush=True)
    return 0


def _base_result(
    original: dict[str, Any],
    workspace: Path,
    checkpoint: str,
    evaluator: dict[str, Any],
    counterexamples: dict[str, Any],
    decision,
) -> dict[str, Any]:
    return {
        "diagnostic": "posthoc-original-session-workspace",
        "not_preregistered": True,
        "source_commit": _run_text(ROOT, "git", "rev-parse", "HEAD"),
        "original_protocol_commit": original["repository_commit"],
        "original_thread_id": original["producer"]["thread_id"],
        "restored_workspace": str(workspace),
        "restored_checkpoint": checkpoint,
        "original_evaluator_before": evaluator,
        "posthoc_counterexamples_before": counterexamples,
        "rerun_decision": to_jsonable(decision),
        "interpretation_guard": (
            "This original-path retry follows observed runner and implementation defects and "
            "cannot be credited as a prospective effectiveness result."
        ),
    }


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


if __name__ == "__main__":
    raise SystemExit(main())
