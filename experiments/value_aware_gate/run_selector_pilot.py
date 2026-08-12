from __future__ import annotations

import argparse
import ast
import hashlib
import json
import os
import subprocess
import sys
import tarfile
import tempfile
from dataclasses import replace
from pathlib import Path
from typing import Any, Mapping

from graft.codex.cli_runner import CliCodexRunner
from graft.codex.hooks import _promotion_from_decision
from graft.codex.telemetry import summarize_records
from graft.controller import GraftController
from graft.evidence.baseline_archive import archive_baseline
from graft.evidence.checkpoint_archive import (
    CHECKPOINT_ARCHIVE_ENV,
    archive_checkpoint,
)
from graft.evidence.snapshot import freeze_source, hash_tree_manifest
from graft.modeling import CodexFeedbackGraphBuilder
from graft.registry import default_value_aware_config_payload, load_config
from graft.schema import (
    DecisionKind,
    FeedbackGraph,
    ProducerEvidenceRecord,
    ProducerEvidenceSummary,
    RunConfig,
    SourceSnapshot,
    TurnResult,
    to_jsonable,
)
from graft.selection import OriginalHypergraphSelector, ValueAwareSelector


ROOT = Path(__file__).resolve().parents[2]
EXPERIMENT = Path(__file__).resolve().parent
MODEL = "gpt-5.6-sol"
SESSION_ID = "value-aware-mechanism-pilot"


class FrozenGraphBuilder:
    """Return one already-built graph so online execution cannot silently re-plan it."""

    def __init__(self, graph: FeedbackGraph) -> None:
        self.graph = graph

    def build(
        self,
        snapshot: SourceSnapshot,
        requirements: tuple[str, ...],
        config,
        *,
        config_path: Path,
        producer_evidence: ProducerEvidenceSummary | None = None,
        promotion=None,
    ) -> FeedbackGraph:
        if snapshot.checkpoint_key != self.graph.source_hash:
            raise ValueError("frozen graph does not match the requested checkpoint")
        if promotion != self.graph.promotion:
            raise ValueError("frozen graph does not match the requested promotion state")
        return self.graph


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--output",
        type=Path,
        default=EXPERIMENT / "results" / "m2_selector_pilot.json",
    )
    parser.add_argument(
        "--restore-checkpoint",
        type=Path,
        help="Restore an archived first checkpoint instead of rerunning the producer.",
    )
    parser.add_argument(
        "--producer-events",
        type=Path,
        help="Producer TurnResult JSON saved before an infrastructure-only retry.",
    )
    parser.add_argument(
        "--baseline-archive",
        type=Path,
        help="Immutable task-start baseline archive paired with a restored checkpoint.",
    )
    parser.add_argument(
        "--expected-checkpoint",
        help="Fail before graph construction unless the restored checkpoint matches this key.",
    )
    args = parser.parse_args()
    restore_inputs = (
        bool(args.restore_checkpoint),
        bool(args.producer_events),
        bool(args.baseline_archive),
    )
    if any(restore_inputs) and not all(restore_inputs):
        parser.error(
            "--restore-checkpoint, --producer-events, and --baseline-archive "
            "must be supplied together"
        )
    task_manifest = json.loads(
        (EXPERIMENT / "selector_task.json").read_text(encoding="utf-8")
    )
    requirements = (str(task_manifest["task"]),)
    artifact_root = ROOT / "artifacts" / "value-aware-gate"
    artifact_root.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory(prefix="graft-m2-") as temporary:
        run_root = Path(temporary)
        workspace = run_root / "producer-workspace"
        workspace.mkdir()
        if args.restore_checkpoint:
            _restore_checkpoint(args.restore_checkpoint, workspace)
        else:
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
        config_payload = _experiment_config()
        config_path.write_text(
            json.dumps(config_payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        config = load_config(config_path)
        if args.baseline_archive:
            baseline_tree, baseline_files, baseline_hashes = _baseline_manifest(
                args.baseline_archive
            )
            baseline_archive = args.baseline_archive.expanduser().resolve()
        else:
            baseline_tree, baseline_files, baseline_hashes = hash_tree_manifest(workspace)
            baseline_archive = archive_baseline(
                workspace,
                files=baseline_files,
                file_hashes=baseline_hashes,
                tree_hash=baseline_tree,
                archive_root=artifact_root / "baselines",
                session_id=SESSION_ID,
                task_epoch=1,
            )

        runner = CliCodexRunner()
        if args.restore_checkpoint and args.producer_events:
            producer_payload = json.loads(
                args.producer_events.read_text(encoding="utf-8")
            )
            producer = _turn_result(producer_payload["turn"])
            print(
                "[M2] restored the archived Native Codex checkpoint; producer not rerun",
                flush=True,
            )
        else:
            print(
                "[M2] starting Native Codex producer with GRAFT hooks disabled",
                flush=True,
            )
            producer = runner.start_thread(
                requirements[0],
                workspace,
                RunConfig(
                    sandbox="workspace-write",
                    network_access=False,
                    model=MODEL,
                    timeout_s=float(task_manifest["producer_timeout_s"]),
                    ephemeral=False,
                    isolate_config=True,
                    disable_hooks=True,
                ),
            )
        if producer.return_code != 0:
            raise RuntimeError(
                f"Native producer exited with {producer.return_code}: {producer.stderr}"
            )

        snapshot = _snapshot(
            workspace,
            requirements,
            config_path,
            config.environment_fingerprint,
            baseline_tree,
            baseline_files,
            baseline_hashes,
            baseline_archive,
        )
        if (
            args.expected_checkpoint
            and snapshot.checkpoint_key != args.expected_checkpoint
        ):
            raise RuntimeError(
                "restored checkpoint mismatch: "
                f"expected {args.expected_checkpoint}, observed {snapshot.checkpoint_key}"
            )
        previous_archive_home = os.environ.get(CHECKPOINT_ARCHIVE_ENV)
        os.environ[CHECKPOINT_ARCHIVE_ENV] = str(artifact_root / "checkpoints")
        try:
            first_checkpoint_archive = archive_checkpoint(
                snapshot,
                session_id=SESSION_ID,
                task_epoch=1,
                verification_round=0,
            )
        finally:
            if previous_archive_home is None:
                os.environ.pop(CHECKPOINT_ARCHIVE_ENV, None)
            else:
                os.environ[CHECKPOINT_ARCHIVE_ENV] = previous_archive_home

        producer_records = _records_from_turn(
            producer,
            snapshot=snapshot,
            session_id=producer.thread_id or SESSION_ID,
            task_epoch=1,
        )
        producer_evidence = summarize_records(producer_records, task_epoch=1)
        first_evaluation = _held_out_evaluate(workspace)
        _write_json(
            artifact_root / "m2-producer-events.json",
            {
                "turn": to_jsonable(producer),
                "evidence_records": to_jsonable(producer_records),
            },
        )
        print(
            f"[M2] producer frozen; held-out evaluator: "
            f"{first_evaluation['passed']}/{first_evaluation['total']} passed",
            flush=True,
        )

        print("[M2] building one value-aware feedback graph", flush=True)
        graph = CodexFeedbackGraphBuilder().build(
            snapshot,
            requirements,
            config,
            config_path=config_path,
            producer_evidence=producer_evidence,
            promotion=None,
        )
        original_selection = OriginalHypergraphSelector().select(
            graph,
            budget=config.budget,
            policy=replace(
                config.selection,
                strategy="original",
                algorithm="lazy-greedy-hypergraph",
            ),
        )
        graph_wall = sum(item.duration_s for item in graph.stage_costs)
        graph_model_cost = sum(
            item.estimated_cost_usd
            for item in graph.stage_costs
            if item.estimated_cost_usd is not None
        )
        value_selection = ValueAwareSelector().select(
            graph,
            budget=config.budget,
            policy=config.selection,
            available_wall_time_s=max(
                0.0, config.selection.wall_time_budget_s - graph_wall
            ),
            available_model_cost_usd=max(
                0.0, config.selection.model_cost_budget_usd - graph_model_cost
            ),
        )
        print(
            "[M2] selections frozen: "
            f"Original={list(original_selection.verifier_ids)}, "
            f"value-aware={list(value_selection.verifier_ids)}",
            flush=True,
        )

        report_root = artifact_root / "reports"
        controller = GraftController(
            config,
            config_path=config_path,
            graph_builder=FrozenGraphBuilder(graph),
            report_root=report_root,
        )
        print("[M2] executing the online value-aware decision", flush=True)
        decision = controller.verify(
            workspace,
            requirements=requirements,
            session_id=SESSION_ID,
            snapshot=snapshot,
            producer_evidence=producer_evidence,
            available_budget=config.budget,
            available_wall_time_s=config.selection.wall_time_budget_s,
            available_model_cost_usd=config.selection.model_cost_budget_usd,
        )
        print(f"[M2] online decision: {decision.kind.value}", flush=True)

        continuation: dict[str, Any] | None = None
        if (
            decision.kind == DecisionKind.CONTINUE_WITH_EVIDENCE
            and producer.thread_id
        ):
            continuation = _run_promotion_round(
                runner=runner,
                workspace=workspace,
                thread_id=producer.thread_id,
                feedback=decision.reason,
                requirements=requirements,
                config_path=config_path,
                config=config,
                baseline_tree=baseline_tree,
                baseline_files=baseline_files,
                baseline_hashes=baseline_hashes,
                baseline_archive=baseline_archive,
                first_records=producer_records,
                first_decision=decision,
                artifact_root=artifact_root,
            )
        else:
            print(
                "[M3] not exercised because M2 produced no reproducible continuation",
                flush=True,
            )

        payload = {
            "protocol_version": 1,
            "product_candidate_commit": "d5da0d18562dd7b1dd8182c05f7e34f92c24d3c1",
            "experiment_commit": _command(ROOT, "git", "rev-parse", "HEAD"),
            "codex_version": _command(ROOT, "codex", "--version"),
            "model": MODEL,
            "task_hash": _sha256(requirements[0]),
            "workspace_checkpoint": snapshot.checkpoint_key,
            "checkpoint_archive": _artifact_reference(first_checkpoint_archive),
            "producer": {
                "thread_id": producer.thread_id,
                "duration_s": producer.duration_s,
                "usage": dict(producer.usage),
                "final_response": producer.final_response,
                "evidence_summary": to_jsonable(producer_evidence),
            },
            "held_out_before_graft": first_evaluation,
            "graph": {
                "behavior_count": len(graph.behaviors),
                "failure_mode_count": len(graph.failure_modes),
                "verifier_count": len(graph.verifiers),
                "blind_spot_count": len(graph.shared_blind_spots),
                "uncertainties": list(graph.uncertainties),
                "stage_costs": to_jsonable(graph.stage_costs),
                "verifiers": to_jsonable(graph.verifiers),
            },
            "same_graph_selector_comparison": {
                "original": to_jsonable(original_selection),
                "value_aware": to_jsonable(value_selection),
            },
            "online_value_aware_decision": to_jsonable(decision),
            "m3_continuation": continuation,
            "interpretation_guard": (
                "This one-task mechanism pilot estimates neither causal utility nor "
                "benchmark-level effectiveness."
            ),
        }
        _write_json(args.output, payload)
        print(json.dumps(payload, ensure_ascii=False, indent=2), flush=True)
    return 0


def _run_promotion_round(
    *,
    runner: CliCodexRunner,
    workspace: Path,
    thread_id: str,
    feedback: str,
    requirements: tuple[str, ...],
    config_path: Path,
    config,
    baseline_tree: str,
    baseline_files: tuple[str, ...],
    baseline_hashes: Mapping[str, str],
    baseline_archive: Path,
    first_records: tuple[ProducerEvidenceRecord, ...],
    first_decision,
    artifact_root: Path,
) -> dict[str, Any]:
    print("[M3] resuming the same Codex thread with GRAFT evidence", flush=True)
    repaired = runner.continue_thread(
        thread_id,
        feedback,
        workspace,
        RunConfig(
            sandbox="workspace-write",
            network_access=False,
            model=MODEL,
            timeout_s=600,
            isolate_config=True,
            disable_hooks=True,
        ),
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
    repaired_records = _records_from_turn(
        repaired,
        snapshot=repaired_snapshot,
        session_id=thread_id,
        task_epoch=1,
    )
    combined_evidence = summarize_records(
        (*first_records, *repaired_records), task_epoch=1
    )
    repaired_evaluation = _held_out_evaluate(workspace)
    promotion = _promotion_from_decision(first_decision)
    print("[M3] building the promotion graph", flush=True)
    graph = CodexFeedbackGraphBuilder().build(
        repaired_snapshot,
        requirements,
        config,
        config_path=config_path,
        producer_evidence=combined_evidence,
        promotion=promotion,
    )
    spent_nominal = (
        first_decision.selection.total_cost
        if first_decision.selection is not None
        else 0.0
    )
    spent_wall = sum(item.duration_s for item in first_decision.graph.stage_costs) + sum(
        item.duration_s for item in first_decision.results
    )
    spent_model = sum(
        item.estimated_cost_usd
        for item in first_decision.graph.stage_costs
        if item.estimated_cost_usd is not None
    ) + sum(
        item.estimated_cost_usd
        for item in first_decision.results
        if item.estimated_cost_usd is not None
    )
    controller = GraftController(
        config,
        config_path=config_path,
        graph_builder=FrozenGraphBuilder(graph),
        report_root=artifact_root / "reports",
    )
    promotion_decision = controller.verify(
        workspace,
        requirements=requirements,
        session_id=SESSION_ID,
        snapshot=repaired_snapshot,
        producer_evidence=combined_evidence,
        available_budget=max(0.0, config.budget - spent_nominal),
        promotion=promotion,
        available_wall_time_s=max(
            0.0, config.selection.wall_time_budget_s - spent_wall
        ),
        available_model_cost_usd=max(
            0.0, config.selection.model_cost_budget_usd - spent_model
        ),
    )
    _write_json(
        artifact_root / "m3-continuation-events.json",
        {
            "turn": to_jsonable(repaired),
            "evidence_records": to_jsonable(repaired_records),
        },
    )
    return {
        "thread_id": repaired.thread_id,
        "duration_s": repaired.duration_s,
        "usage": dict(repaired.usage),
        "final_response": repaired.final_response,
        "checkpoint": repaired_snapshot.checkpoint_key,
        "held_out_after_repair": repaired_evaluation,
        "promotion_requirement": to_jsonable(promotion),
        "promotion_decision": to_jsonable(promotion_decision),
    }


def _experiment_config() -> dict[str, Any]:
    payload = default_value_aware_config_payload()
    for key in ("behavior_modeler", "verifier_planner", "completion_gate"):
        payload["modeling"][key]["model"] = MODEL
    for template in payload["verifier_templates"]:
        if template["kind"] != "command":
            template["model"] = MODEL
    return payload


def _restore_checkpoint(archive_path: Path, workspace: Path) -> None:
    archive_path = archive_path.expanduser().resolve()
    workspace = workspace.resolve()
    with tarfile.open(archive_path, mode="r:gz") as archive:
        for member in archive.getmembers():
            parts = Path(member.name).parts
            if not parts or parts[0] != "workspace" or len(parts) == 1:
                continue
            relative = Path(*parts[1:])
            if relative.is_absolute() or ".." in relative.parts or not member.isfile():
                raise ValueError(f"unsafe checkpoint member: {member.name}")
            extracted = archive.extractfile(member)
            if extracted is None:
                raise ValueError(f"checkpoint member has no content: {member.name}")
            destination = workspace / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(extracted.read())
            destination.chmod(member.mode & 0o777)


def _baseline_manifest(
    archive_path: Path,
) -> tuple[str, tuple[str, ...], dict[str, str]]:
    archive_path = archive_path.expanduser().resolve()
    with tarfile.open(archive_path, mode="r:gz") as archive:
        metadata_file = archive.extractfile("baseline.json")
        if metadata_file is None:
            raise ValueError("baseline archive has no baseline.json")
        raw = json.load(metadata_file)
    if not isinstance(raw, Mapping) or not isinstance(raw.get("file_hashes"), Mapping):
        raise ValueError("invalid baseline archive metadata")
    tree_hash = str(raw.get("tree_hash", ""))
    files = tuple(str(item) for item in raw.get("files", []))
    hashes = {str(path): str(digest) for path, digest in raw["file_hashes"].items()}
    if not tree_hash or set(files) != set(hashes):
        raise ValueError("inconsistent baseline archive manifest")
    return tree_hash, files, hashes


def _turn_result(raw: Mapping[str, Any]) -> TurnResult:
    events = raw.get("events", [])
    usage = raw.get("usage", {})
    if not isinstance(events, list) or not isinstance(usage, Mapping):
        raise ValueError("invalid archived producer TurnResult")
    return TurnResult(
        thread_id=(str(raw["thread_id"]) if raw.get("thread_id") else None),
        final_response=str(raw.get("final_response", "")),
        events=tuple(item for item in events if isinstance(item, Mapping)),
        usage=dict(usage),
        return_code=int(raw.get("return_code", 1)),
        stderr=str(raw.get("stderr", "")),
        duration_s=float(raw.get("duration_s", 0.0)),
    )


def _snapshot(
    workspace: Path,
    requirements: tuple[str, ...],
    config_path: Path,
    environment_fingerprint: str,
    baseline_tree: str,
    baseline_files: tuple[str, ...],
    baseline_hashes: Mapping[str, str],
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


def _records_from_turn(
    turn: TurnResult,
    *,
    snapshot: SourceSnapshot,
    session_id: str,
    task_epoch: int,
) -> tuple[ProducerEvidenceRecord, ...]:
    changed_paths = tuple(
        sorted(
            path
            for path, digest in snapshot.file_hashes.items()
            if snapshot.baseline_file_hashes.get(path) != digest
        )
    )
    records: list[ProducerEvidenceRecord] = []
    for event in turn.events:
        if event.get("type") != "item.completed":
            continue
        item = event.get("item")
        if not isinstance(item, Mapping):
            continue
        item_type = str(item.get("type", ""))
        if item_type not in {"command_execution", "command"}:
            continue
        command = _command_text(item.get("command"))
        exit_code = item.get("exit_code")
        if isinstance(exit_code, bool) or not isinstance(exit_code, int):
            exit_code = None
        outcome = (
            "succeeded"
            if exit_code == 0
            else "failed"
            if exit_code is not None
            else "unknown"
        )
        output = str(
            item.get("aggregated_output")
            or item.get("output")
            or item.get("text")
            or ""
        )
        records.append(
            ProducerEvidenceRecord(
                timestamp=snapshot.created_at,
                session_id=session_id,
                turn_id=None,
                task_epoch=task_epoch,
                tool_name="codex_exec_command",
                family="execution",
                outcome=outcome,
                input_hash=_sha256(command),
                response_hash=_sha256(output),
                command_preview=command[:2000] if command else None,
                result_preview=output[-2000:] if output else None,
                changed_paths=(),
                exit_code=exit_code,
                duration_s=None,
            )
        )
    if changed_paths:
        records.append(
            ProducerEvidenceRecord(
                timestamp=snapshot.created_at,
                session_id=session_id,
                turn_id=None,
                task_epoch=task_epoch,
                tool_name="workspace_snapshot",
                family="workspace_change",
                outcome="succeeded",
                input_hash=snapshot.baseline_tree_hash or "",
                response_hash=snapshot.tree_hash,
                changed_paths=changed_paths,
            )
        )
    return tuple(records)


def _held_out_evaluate(workspace: Path) -> dict[str, Any]:
    program = workspace / "aggregate.py"
    readme_present = any(
        path.is_file() for path in (workspace / "README.md", workspace / "README")
    )
    tests_present = any(
        path.is_file()
        for candidate in (workspace / "tests", workspace)
        if candidate.exists()
        for path in candidate.glob("test*.py")
    )
    cases: list[dict[str, Any]] = []
    valid = (
        (
            "valid-basic",
            '{"key":"b","delta":2}\n{"key":"a","delta":3}\n'
            '{"key":"b","delta":-1}\n',
            {"a": 3, "b": 1},
        ),
        (
            "valid-blanks-negative",
            '\n  \n{"key":"zero","delta":0}\n'
            '{"key":"negative","delta":-7}\n\t\n',
            {"negative": -7, "zero": 0},
        ),
        (
            "valid-unicode",
            '{"key":"汉","delta":2}\n{"key":"é","delta":1}\n',
            {"é": 1, "汉": 2},
        ),
        ("valid-empty", " \n\t\n", {}),
    )
    invalid = (
        ("invalid-malformed", '{"key":"a","delta":1\n'),
        ("invalid-array", '["a", 1]\n'),
        ("invalid-missing", '{"key":"a"}\n'),
        ("invalid-extra", '{"key":"a","delta":1,"note":0}\n'),
        ("invalid-bool", '{"key":"a","delta":true}\n'),
        ("invalid-float", '{"key":"a","delta":1.0}\n'),
        ("invalid-key-type", '{"key":1,"delta":2}\n'),
        (
            "invalid-no-partial-output",
            '{"key":"a","delta":1}\n{"key":"b","delta":false}\n',
        ),
    )
    if not program.is_file():
        return {
            "passed": 0,
            "total": len(valid) + len(invalid) + 3,
            "all_passed": False,
            "cases": [{"id": "program-present", "passed": False}],
        }

    for case_id, stdin, expected in valid:
        result = _invoke_program(program, workspace, stdin)
        acceptable = {
            json.dumps(expected, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
            + "\n",
            json.dumps(expected, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
            + "\n",
        }
        cases.append(
            {
                "id": case_id,
                "passed": (
                    result["return_code"] == 0
                    and result["stderr"] == ""
                    and result["stdout"] in acceptable
                ),
                **result,
            }
        )
    for case_id, stdin in invalid:
        result = _invoke_program(program, workspace, stdin)
        stderr_lines = [
            line for line in result["stderr"].splitlines() if line.strip()
        ]
        cases.append(
            {
                "id": case_id,
                "passed": (
                    result["return_code"] == 2
                    and result["stdout"] == ""
                    and len(stderr_lines) == 1
                    and len(stderr_lines[0]) <= 240
                ),
                **result,
            }
        )

    cases.extend(
        (
            {"id": "readme-present", "passed": readme_present},
            {"id": "automated-tests-present", "passed": tests_present},
            {
                "id": "stdlib-only",
                "passed": _uses_only_standard_library(program),
            },
        )
    )
    passed = sum(bool(case["passed"]) for case in cases)
    return {
        "passed": passed,
        "total": len(cases),
        "all_passed": passed == len(cases),
        "cases": cases,
    }


def _invoke_program(program: Path, workspace: Path, stdin: str) -> dict[str, Any]:
    try:
        completed = subprocess.run(
            [sys.executable, str(program)],
            input=stdin,
            cwd=workspace,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
            timeout=5,
        )
        return {
            "return_code": completed.returncode,
            "stdout": completed.stdout,
            "stderr": completed.stderr,
        }
    except subprocess.TimeoutExpired:
        return {"return_code": None, "stdout": "", "stderr": "<timeout>"}


def _uses_only_standard_library(program: Path) -> bool:
    try:
        tree = ast.parse(program.read_text(encoding="utf-8"))
    except (OSError, SyntaxError, UnicodeDecodeError):
        return False
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name.split(".", 1)[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module.split(".", 1)[0])
    return all(name in sys.stdlib_module_names for name in imported)


def _git(workspace: Path, *args: str) -> str:
    return _command(workspace, "git", *args)


def _command(cwd: Path, *command: str) -> str:
    completed = subprocess.run(
        command,
        cwd=cwd,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        check=False,
        timeout=30,
    )
    if completed.returncode != 0:
        raise RuntimeError(
            f"command failed ({completed.returncode}): {' '.join(command)}\n"
            f"{completed.stdout}"
        )
    return completed.stdout.strip()


def _command_text(value: Any) -> str:
    if isinstance(value, list):
        return " ".join(str(item) for item in value)
    return str(value or "")


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


def _artifact_reference(path: Path | None) -> dict[str, str] | None:
    if path is None:
        return None
    return {"path": str(path.resolve()), "sha256": _sha256_bytes(path.read_bytes())}


def _sha256(value: str) -> str:
    return _sha256_bytes(value.encode("utf-8"))


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


if __name__ == "__main__":
    raise SystemExit(main())
