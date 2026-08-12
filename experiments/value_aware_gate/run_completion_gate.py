from __future__ import annotations

import argparse
import json
import subprocess
import tempfile
from pathlib import Path

from graft.codex.completion import CodexCompletionGate, CompletionGateError
from graft.evidence.snapshot import freeze_source, hash_tree_manifest
from graft.registry import default_value_aware_config_payload, load_config
from graft.schema import ProducerEvidenceSummary, to_jsonable


ROOT = Path(__file__).resolve().parents[2]
EXPERIMENT = Path(__file__).resolve().parent


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--output",
        type=Path,
        default=EXPERIMENT / "results" / "m1_completion_pilot.json",
    )
    args = parser.parse_args()
    manifest = json.loads(
        (EXPERIMENT / "completion_cases.json").read_text(encoding="utf-8")
    )
    rows: list[dict] = []
    with tempfile.TemporaryDirectory(prefix="graft-m1-") as temporary:
        run_root = Path(temporary)
        config_path = run_root / "value-aware.json"
        config_payload = default_value_aware_config_payload()
        config_payload["modeling"]["completion_gate"]["model"] = "gpt-5.6-sol"
        config_path.write_text(
            json.dumps(config_payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        config = load_config(config_path)
        assert config.completion_gate is not None
        for raw in manifest["cases"]:
            workspace = run_root / raw["id"]
            workspace.mkdir()
            artifact = workspace / "artifact.txt"
            artifact.write_text("baseline\n", encoding="utf-8")
            baseline_tree, baseline_files, baseline_hashes = hash_tree_manifest(workspace)
            artifact.write_text(str(raw["candidate_text"]), encoding="utf-8")
            requirements = tuple(str(item) for item in raw["requirements"])
            snapshot = freeze_source(
                workspace,
                requirements=requirements,
                config_path=config_path,
                environment_fingerprint=config.environment_fingerprint,
                baseline_tree_hash=baseline_tree,
                baseline_files=baseline_files,
                baseline_file_hashes=baseline_hashes,
            )
            evidence_raw = raw["evidence"]
            evidence = ProducerEvidenceSummary(
                task_epoch=1,
                event_count=int(evidence_raw["succeeded"])
                + int(evidence_raw["failed"]),
                succeeded=int(evidence_raw["succeeded"]),
                failed=int(evidence_raw["failed"]),
                unknown=0,
                total_duration_s=None,
                command_previews=tuple(
                    str(item) for item in evidence_raw.get("commands", [])
                ),
                failure_previews=(
                    ("The recorded producer check failed.",)
                    if int(evidence_raw["failed"])
                    else ()
                ),
                changed_paths=("artifact.txt",),
            )
            try:
                assessment = CodexCompletionGate().assess(
                    snapshot,
                    requirements,
                    last_assistant_message=str(raw["last_message"]),
                    producer_evidence=evidence,
                    config=config.completion_gate,
                    config_path=config_path,
                    environment_fingerprint=config.environment_fingerprint,
                )
                row = {
                    "id": raw["id"],
                    "expected_state": raw["expected_state"],
                    "predicted_state": assessment.state.value,
                    "confidence": assessment.confidence,
                    "reason": assessment.reason,
                    "stage_cost": to_jsonable(assessment.stage_cost),
                    "error": None,
                }
            except CompletionGateError as exc:
                row = {
                    "id": raw["id"],
                    "expected_state": raw["expected_state"],
                    "predicted_state": "error",
                    "confidence": None,
                    "reason": None,
                    "stage_cost": None,
                    "error": str(exc),
                }
            rows.append(row)

    metrics = _metrics(rows)
    payload = {
        "protocol_version": 1,
        "commit": _command("git", "rev-parse", "HEAD"),
        "codex_version": _command("codex", "--version"),
        "model": "gpt-5.6-sol",
        "case_count": len(rows),
        "rows": rows,
        "metrics": metrics,
        "pilot_success": (
            metrics["question_or_blocked_false_triggers"] == 0
            and metrics["binary_precision"] >= 0.75
            and metrics["binary_recall"] >= 0.75
            and metrics["structured_result_rate"] == 1.0
        ),
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


def _metrics(rows: list[dict]) -> dict[str, float | int | None]:
    expected_positive = [row["expected_state"] == "candidate_complete" for row in rows]
    predicted_positive = [row["predicted_state"] == "candidate_complete" for row in rows]
    true_positive = sum(a and b for a, b in zip(expected_positive, predicted_positive))
    false_positive = sum(not a and b for a, b in zip(expected_positive, predicted_positive))
    false_negative = sum(a and not b for a, b in zip(expected_positive, predicted_positive))
    true_negative = sum(not a and not b for a, b in zip(expected_positive, predicted_positive))
    durations = [
        float(row["stage_cost"]["duration_s"])
        for row in rows
        if row["stage_cost"] is not None
    ]
    tokens = [
        (row["stage_cost"].get("input_tokens") or 0)
        + (row["stage_cost"].get("output_tokens") or 0)
        for row in rows
        if row["stage_cost"] is not None
        and (
            row["stage_cost"].get("input_tokens") is not None
            or row["stage_cost"].get("output_tokens") is not None
        )
    ]
    return {
        "true_positive": true_positive,
        "false_positive": false_positive,
        "false_negative": false_negative,
        "true_negative": true_negative,
        "binary_precision": _divide(true_positive, true_positive + false_positive),
        "binary_recall": _divide(true_positive, true_positive + false_negative),
        "binary_accuracy": _divide(true_positive + true_negative, len(rows)),
        "exact_state_accuracy": _divide(
            sum(row["expected_state"] == row["predicted_state"] for row in rows),
            len(rows),
        ),
        "question_or_blocked_false_triggers": sum(
            row["expected_state"] in {"question", "blocked"}
            and row["predicted_state"] == "candidate_complete"
            for row in rows
        ),
        "structured_result_rate": _divide(
            sum(row["error"] is None for row in rows), len(rows)
        ),
        "total_duration_s": sum(durations),
        "mean_duration_s": _divide(sum(durations), len(durations)),
        "total_known_tokens": sum(tokens) if tokens else None,
    }


def _divide(numerator: int | float, denominator: int) -> float:
    return float(numerator / denominator) if denominator else 0.0


def _command(*command: str) -> str:
    completed = subprocess.run(
        command,
        cwd=ROOT,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        check=False,
        timeout=10,
    )
    return completed.stdout.strip()


if __name__ == "__main__":
    raise SystemExit(main())
