from __future__ import annotations

import json
from pathlib import Path

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
    _turn_result,
)
from graft.codex.cli_runner import CliCodexRunner
from graft.codex.hooks import _promotion_from_decision
from graft.codex.telemetry import summarize_records
from graft.controller import GraftController
from graft.modeling import CodexFeedbackGraphBuilder
from graft.registry import load_config
from graft.replay import load_report_graph
from graft.schema import Decision, DecisionKind, RunConfig, to_jsonable


ROOT = Path(__file__).resolve().parents[2]


def main() -> int:
    original = json.loads((ARTIFACT_ROOT / "result.json").read_text(encoding="utf-8"))
    previous = json.loads(
        (ARTIFACT_ROOT / "original-path-result.json").read_text(encoding="utf-8")
    )
    manifest = json.loads((EXPERIMENT / "task.json").read_text(encoding="utf-8"))
    task = str(manifest["task"])
    model = str(manifest["model"])
    requirements = (task,)
    thread_id = str(previous["original_thread_id"])
    workspace = Path(previous["restored_workspace"])
    if not workspace.is_dir():
        raise RuntimeError(f"original workspace is unavailable: {workspace}")

    config_path = workspace.parent / "value-aware.json"
    config_path.write_text(
        json.dumps(_experiment_config(model), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    config = load_config(config_path)
    baseline_archive = Path(
        original["first_decision"]["snapshot"]["baseline_archive_path"]
    )
    baseline_tree, baseline_files, baseline_hashes = _baseline_manifest(baseline_archive)
    before = _snapshot(
        workspace,
        requirements,
        config_path,
        config.environment_fingerprint,
        baseline_tree,
        baseline_files,
        baseline_hashes,
        baseline_archive,
    )
    raw_decision = previous["rerun_decision"]
    if raw_decision["kind"] != DecisionKind.CONTINUE_WITH_EVIDENCE.value:
        raise RuntimeError("saved decision is not continuation evidence")
    if before.checkpoint_key != raw_decision["snapshot"]["checkpoint_key"]:
        raise RuntimeError(
            "workspace changed since the saved feedback decision: "
            f"expected {raw_decision['snapshot']['checkpoint_key']}, observed "
            f"{before.checkpoint_key}"
        )
    decision = _decision_from_saved(raw_decision)

    producer_turn = _turn_result(
        json.loads((ARTIFACT_ROOT / "producer-events.json").read_text(encoding="utf-8"))[
            "turn"
        ]
    )
    first_records = _records_from_turn(
        producer_turn,
        snapshot=before,
        session_id=thread_id,
        task_epoch=1,
    )
    before_evaluator = _held_out_evaluate(workspace)
    before_counterexamples = _posthoc_counterexamples(workspace)

    print("[WRITABLE-RESUME] resuming exact saved GRAFT feedback", flush=True)
    runner = CliCodexRunner()
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
    after = _snapshot(
        workspace,
        requirements,
        config_path,
        config.environment_fingerprint,
        baseline_tree,
        baseline_files,
        baseline_hashes,
        baseline_archive,
    )
    after_archive = _capture(after, thread_id, 1)
    repaired_records = _records_from_turn(
        repaired,
        snapshot=after,
        session_id=thread_id,
        task_epoch=1,
    )
    combined_evidence = summarize_records(
        (*first_records, *repaired_records), task_epoch=1
    )
    after_evaluator = _held_out_evaluate(workspace)
    after_counterexamples = _posthoc_counterexamples(workspace)
    promotion = _promotion_from_decision(decision)

    print("[WRITABLE-RESUME] building mandatory promotion verification", flush=True)
    promotion_graph = CodexFeedbackGraphBuilder().build(
        after,
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
        report_root=ARTIFACT_ROOT / "writable-resume-reports",
    )
    spent_nominal = decision.selection.total_cost if decision.selection else 0.0
    promotion_decision = promotion_controller.verify(
        workspace,
        requirements=requirements,
        session_id=thread_id,
        snapshot=after,
        producer_evidence=combined_evidence,
        available_budget=max(0.0, config.budget - spent_nominal),
        promotion=promotion,
    )

    _write_json(
        ARTIFACT_ROOT / "writable-resume-events.json",
        {"turn": to_jsonable(repaired), "records": to_jsonable(repaired_records)},
    )
    result = {
        "diagnostic": "posthoc-writable-same-thread-promotion",
        "not_preregistered": True,
        "source_commit": _run_text(ROOT, "git", "rev-parse", "HEAD"),
        "original_protocol_commit": original["repository_commit"],
        "thread_id_before": thread_id,
        "thread_id_after": repaired.thread_id,
        "same_thread": repaired.thread_id == thread_id,
        "workspace": str(workspace),
        "checkpoint_before": before.checkpoint_key,
        "checkpoint_after": after.checkpoint_key,
        "checkpoint_changed": before.checkpoint_key != after.checkpoint_key,
        "checkpoint_archive_after": _artifact(after_archive),
        "saved_feedback": raw_decision["reason"],
        "continuation": {
            "duration_s": repaired.duration_s,
            "usage": dict(repaired.usage),
            "final_response": repaired.final_response,
        },
        "original_evaluator_before": before_evaluator,
        "original_evaluator_after": after_evaluator,
        "posthoc_counterexamples_before": before_counterexamples,
        "posthoc_counterexamples_after": after_counterexamples,
        "promotion_requirement": to_jsonable(promotion),
        "promotion_graph": _graph_summary(promotion_graph),
        "promotion_decision": to_jsonable(promotion_decision),
        "interpretation_guard": (
            "This run follows multiple observed integration fixes and is mechanism evidence only, "
            "not a prospective effectiveness estimate."
        ),
    }
    _write_json(ARTIFACT_ROOT / "writable-resume-result.json", result)
    print(
        "[WRITABLE-RESUME] "
        f"same_thread={result['same_thread']}; changed={result['checkpoint_changed']}; "
        f"counterexamples={after_counterexamples['passed']}/"
        f"{after_counterexamples['total']}; "
        f"promotion={promotion_decision.promotion_outcome}",
        flush=True,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2), flush=True)
    return 0


def _decision_from_saved(raw: dict[str, Any]) -> Decision:
    report = Path(str(raw["report_path"]))
    graph = load_report_graph(report)
    # Reconstruct only the fields needed to produce the immutable promotion
    # packet; the saved JSON remains the authoritative audit record.
    from graft.replay import _feedback_graph
    from graft.schema import (
        EvidenceItem,
        Lineage,
        Selection,
        SourceSnapshot,
        Verdict,
        VerifierResult,
    )

    source_raw = raw["snapshot"]
    snapshot = SourceSnapshot(
        root=str(source_raw["root"]),
        tree_hash=str(source_raw["tree_hash"]),
        requirement_hash=str(source_raw["requirement_hash"]),
        config_hash=str(source_raw["config_hash"]),
        checkpoint_key=str(source_raw["checkpoint_key"]),
        files=tuple(source_raw["files"]),
        created_at=str(source_raw["created_at"]),
        baseline_tree_hash=source_raw.get("baseline_tree_hash"),
        baseline_files=tuple(source_raw.get("baseline_files", [])),
        file_hashes=dict(source_raw.get("file_hashes", {})),
        baseline_file_hashes=dict(source_raw.get("baseline_file_hashes", {})),
        baseline_archive_path=source_raw.get("baseline_archive_path"),
    )
    selection_raw = raw["selection"]
    selection = Selection(
        verifier_ids=tuple(selection_raw["verifier_ids"]),
        expected_utility=float(selection_raw["expected_utility"]),
        expected_coverage=float(selection_raw["expected_coverage"]),
        residual_risk=float(selection_raw["residual_risk"]),
        total_cost=float(selection_raw["total_cost"]),
        feasible=bool(selection_raw["feasible"]),
        evaluated_candidates=int(selection_raw["evaluated_candidates"]),
        policy=str(selection_raw["policy"]),
        net_value=float(selection_raw.get("net_value", 0)),
        no_op=bool(selection_raw.get("no_op", False)),
        marginal_values=dict(selection_raw.get("marginal_values", {})),
    )
    results = []
    for item in raw["results"]:
        results.append(
            VerifierResult(
                verifier_id=str(item["verifier_id"]),
                verdict=Verdict(str(item["verdict"])),
                summary=str(item["summary"]),
                source_hash=str(item["source_hash"]),
                blocking=bool(item["blocking"]),
                reproducible=bool(item["reproducible"]),
                duration_s=float(item["duration_s"]),
                failure_modes=tuple(item.get("failure_modes", [])),
                evidence=tuple(
                    EvidenceItem(
                        kind=str(e["kind"]),
                        observation=str(e["observation"]),
                        path=e.get("path"),
                        line=e.get("line"),
                        command=tuple(e.get("command", [])),
                        failure_modes=tuple(e.get("failure_modes", [])),
                        requirement_refs=tuple(e.get("requirement_refs", [])),
                        oracle_origin=str(e.get("oracle_origin", "unspecified")),
                    )
                    for e in item.get("evidence", [])
                ),
                lineage=Lineage(),
                executed_evidence=bool(item.get("executed_evidence", False)),
            )
        )
    return Decision(
        kind=DecisionKind(str(raw["kind"])),
        reason=str(raw["reason"]),
        snapshot=snapshot,
        graph=graph,
        selection=selection,
        results=tuple(results),
        report_path=str(raw["report_path"]),
    )


def _run_text(cwd: Path, *command: str) -> str:
    import subprocess

    return subprocess.run(
        command, cwd=cwd, text=True, capture_output=True, check=True
    ).stdout.strip()


if __name__ == "__main__":
    raise SystemExit(main())
