from __future__ import annotations

import json
import subprocess
import tempfile
from pathlib import Path
from typing import Any

from experiments.promotion_e2e.run import (
    ARTIFACT_ROOT,
    EXPERIMENT,
    SESSION_LABEL,
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
    checkpoint_archive = Path(original["first_checkpoint_archive"]["path"])
    original_report = Path(original["first_decision"]["report_path"])
    producer_turn = _turn_result(
        json.loads((ARTIFACT_ROOT / "producer-events.json").read_text(encoding="utf-8"))[
            "turn"
        ]
    )
    baseline_archive = Path(
        original["first_decision"]["snapshot"]["baseline_archive_path"]
    )
    baseline_tree, baseline_files, baseline_hashes = _baseline_manifest(baseline_archive)

    with tempfile.TemporaryDirectory(prefix="graft-promotion-posthoc-") as temporary:
        run_root = Path(temporary)
        workspace = run_root / "producer-workspace"
        workspace.mkdir()
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

        config_path = run_root / "value-aware.json"
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
                "restored candidate mismatch: "
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
        counterexamples_before = _posthoc_counterexamples(workspace)

        controller = GraftController(
            config,
            config_path=config_path,
            graph_builder=FrozenGraphBuilder(first_graph),
            report_root=ARTIFACT_ROOT / "diagnostic-reports",
        )
        print("[POSTHOC] rerunning GRAFT on the exact first checkpoint", flush=True)
        decision = controller.verify(
            workspace,
            requirements=requirements,
            session_id=thread_id,
            snapshot=first_snapshot,
            producer_evidence=first_evidence,
            available_budget=config.budget,
        )
        print(f"[POSTHOC] decision: {decision.kind.value}", flush=True)

        continuation: dict[str, Any] | None = None
        if decision.kind == DecisionKind.CONTINUE_WITH_EVIDENCE:
            runner = CliCodexRunner()
            print("[POSTHOC] resuming the original producer thread", flush=True)
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
            promotion = _promotion_from_decision(decision)
            print("[POSTHOC] building and executing the promotion graph", flush=True)
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
                report_root=ARTIFACT_ROOT / "diagnostic-reports",
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
            continuation = {
                "returned_thread_id": repaired.thread_id,
                "same_thread": repaired.thread_id == thread_id,
                "duration_s": repaired.duration_s,
                "usage": dict(repaired.usage),
                "final_response": repaired.final_response,
                "checkpoint": repaired_snapshot.checkpoint_key,
                "checkpoint_archive": _artifact(repaired_archive),
                "original_evaluator_after": _held_out_evaluate(workspace),
                "posthoc_counterexamples_after": _posthoc_counterexamples(workspace),
                "promotion_requirement": to_jsonable(promotion),
                "promotion_graph": _graph_summary(promotion_graph),
                "promotion_decision": to_jsonable(promotion_decision),
            }
            _write_json(
                ARTIFACT_ROOT / "posthoc-continuation-events.json",
                {"turn": to_jsonable(repaired), "records": to_jsonable(repaired_records)},
            )
            print(
                f"[POSTHOC] same_thread={continuation['same_thread']}; "
                f"promotion={promotion_decision.promotion_outcome}",
                flush=True,
            )
        else:
            print("[POSTHOC] no continuation was forced", flush=True)

        result = {
            "diagnostic": "posthoc-after-shell-evidence-match-fix",
            "not_preregistered": True,
            "source_commit": _run_text(ROOT, "git", "rev-parse", "HEAD"),
            "original_protocol_commit": original["repository_commit"],
            "original_thread_id": thread_id,
            "restored_checkpoint": first_snapshot.checkpoint_key,
            "original_evaluator_before": _held_out_evaluate(workspace)
            if continuation is None
            else original["held_out_before_graft"],
            "posthoc_counterexamples_before": counterexamples_before,
            "rerun_decision": to_jsonable(decision),
            "continuation": continuation,
            "interpretation_guard": (
                "This diagnostic follows an observed implementation defect and cannot be "
                "credited as a prospective effectiveness result."
            ),
        }
        _write_json(ARTIFACT_ROOT / "posthoc-result.json", result)
        print(json.dumps(result, ensure_ascii=False, indent=2), flush=True)
    return 0


def _posthoc_counterexamples(workspace: Path) -> dict[str, Any]:
    program = workspace / "pathfilter.py"
    invalid_utf8 = subprocess.run(
        ["python3", str(program)],
        input=b'{"patterns":[],"paths":["\xff"]}',
        cwd=workspace,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=10,
        check=False,
    )
    escaped_hyphen_payload = json.dumps(
        {"patterns": [r"[a\-c]"], "paths": ["a", "-", "b", "c"]}
    )
    escaped_hyphen = subprocess.run(
        ["python3", str(program)],
        input=escaped_hyphen_payload,
        cwd=workspace,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=10,
        check=False,
    )
    cases = (
        {
            "id": "invalid-raw-utf8",
            "passed": invalid_utf8.returncode == 2
            and invalid_utf8.stdout == b""
            and len([line for line in invalid_utf8.stderr.splitlines() if line.strip()])
            == 1,
            "return_code": invalid_utf8.returncode,
            "stdout_hex": invalid_utf8.stdout.hex(),
            "stderr": invalid_utf8.stderr.decode("utf-8", errors="replace"),
        },
        {
            "id": "escaped-hyphen-is-literal",
            "passed": escaped_hyphen.returncode == 0
            and escaped_hyphen.stderr == ""
            and escaped_hyphen.stdout == '["a","-","c"]\n',
            "return_code": escaped_hyphen.returncode,
            "stdout": escaped_hyphen.stdout,
            "stderr": escaped_hyphen.stderr,
        },
    )
    return {
        "passed": sum(bool(item["passed"]) for item in cases),
        "total": len(cases),
        "cases": list(cases),
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
