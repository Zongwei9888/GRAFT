from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
import statistics
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Iterable


ROOT = Path(__file__).resolve().parent
DEFAULT_MANIFEST = ROOT / "manifest.json"
SUCCESS_RE = re.compile(r"<success>\s*(.*?)\s*</success>", re.I | re.S)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Audit pinned AgentRewardBench files and build a GRAFT selection matrix."
    )
    parser.add_argument("--annotations", type=Path, required=True)
    parser.add_argument("--judgments", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument(
        "--allow-incomplete",
        action="store_true",
        help="Write an explicitly incomplete smoke matrix instead of enforcing full coverage.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
    annotations = load_primary_annotations(args.annotations)
    rows, audit = build_matrix(annotations, args.judgments, manifest["judges"])
    expected_rows = int(manifest["expected"]["primary_trajectories"])
    expected_judges = int(manifest["expected"]["judges"])
    eligible_rows = sum(1 for row in rows if row["label"] is not None)
    complete = (
        len(annotations) == expected_rows
        and audit["complete_rows"] == eligible_rows
        and len(audit["judges_seen"]) == expected_judges
    )
    audit.update(
        {
            "manifest": manifest,
            "annotations": len(annotations),
            "eligible_binary_rows": eligible_rows,
            "matrix_rows": len(rows),
            "complete": complete,
        }
    )
    if not complete and not args.allow_incomplete:
        raise SystemExit(
            "AgentRewardBench audit is incomplete. Re-run with the full pinned data or "
            "use --allow-incomplete only for a schema smoke test."
        )
    write_outputs(args.output, rows, audit)
    print(json.dumps(audit, ensure_ascii=False, indent=2))
    return 0


def load_primary_annotations(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8", newline="") as handle:
        records = list(csv.DictReader(handle))
    required = {"benchmark", "task_id", "model_name", "trajectory_success"}
    if not records or not required.issubset(records[0]):
        raise ValueError(f"Annotation schema is incompatible: {path}")
    seen: set[tuple[str, str, str]] = set()
    primary: list[dict[str, str]] = []
    for record in records:
        key = (record["benchmark"], record["model_name"], record["task_id"])
        if key in seen:
            continue
        seen.add(key)
        primary.append(record)
    primary.sort(key=lambda row: (row["benchmark"], row["model_name"], row["task_id"]))
    return primary


def build_matrix(
    annotations: Iterable[dict[str, str]],
    judgments_root: Path,
    judges: Iterable[str],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    judge_ids = tuple(judges)
    rows: list[dict[str, Any]] = []
    missing = Counter()
    parse_failures = Counter()
    costs: dict[str, list[float]] = defaultdict(list)
    tokens: dict[str, list[int]] = defaultdict(list)
    seen = Counter()
    complete_rows = 0

    for annotation in annotations:
        row: dict[str, Any] = {
            "trajectory_id": _trajectory_id(annotation),
            "benchmark": annotation["benchmark"],
            "agent": annotation["model_name"],
            "task_id": annotation["task_id"],
            "label": parse_binary_label(
                annotation["trajectory_success"], allow_unsure=True
            ),
            "judgments": {},
        }
        row_complete = row["label"] is not None
        for judge in judge_ids:
            path = judgment_path(judgments_root, annotation, judge)
            if not path.is_file():
                missing[judge] += 1
                row_complete = False
                continue
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
                prediction = parse_judgment_success(payload, judge)
            except (OSError, json.JSONDecodeError, KeyError, TypeError, ValueError):
                parse_failures[judge] += 1
                row_complete = False
                continue
            response = payload.get("response") if isinstance(payload, dict) else None
            usage = response.get("usage", {}) if isinstance(response, dict) else {}
            cost = payload.get("cost", {}) if isinstance(payload, dict) else {}
            total_cost = _number(cost.get("total_price")) if isinstance(cost, dict) else None
            total_tokens = _integer(usage.get("total_tokens")) if isinstance(usage, dict) else None
            row["judgments"][judge] = {
                "prediction": prediction,
                "correct": (
                    prediction == row["label"] if row["label"] is not None else None
                ),
                "total_tokens": total_tokens,
                "total_cost_usd": total_cost,
                "model": payload.get("judge_model_name"),
                "provider": payload.get("provider"),
                "inputs": _judge_inputs(payload),
                "system_prompt_hash": _system_prompt_hash(payload),
                "oracle_family": (
                    "functional" if judge == "functional" else "model_judge"
                ),
            }
            seen[judge] += 1
            if total_cost is not None:
                costs[judge].append(total_cost)
            if total_tokens is not None:
                tokens[judge].append(total_tokens)
        if row_complete and len(row["judgments"]) == len(judge_ids):
            complete_rows += 1
        rows.append(row)

    return rows, {
        "complete_rows": complete_rows,
        "judges_seen": sorted(seen),
        "judgment_counts": dict(sorted(seen.items())),
        "missing_judgments": dict(sorted(missing.items())),
        "parse_failures": dict(sorted(parse_failures.items())),
        "median_total_tokens": {
            judge: statistics.median(values) for judge, values in sorted(tokens.items())
        },
        "median_total_cost_usd": {
            judge: statistics.median(values) for judge, values in sorted(costs.items())
        },
        "wall_time_available": False,
        "repair_delta_available": False,
    }


def judgment_path(
    root: Path,
    annotation: dict[str, str],
    judge: str,
) -> Path:
    return (
        root
        / annotation["benchmark"]
        / annotation["model_name"]
        / judge
        / f"{annotation['task_id']}.json"
    )


def parse_judgment_success(payload: dict[str, Any], judge: str) -> int:
    if judge == "functional" or payload.get("judge") == "functional":
        reward = payload["trajectory_info"]["summary_info"]["cum_reward"]
        return int(float(reward) > 0.5)
    response = payload.get("response")
    if not isinstance(response, dict):
        raise ValueError("missing response")
    choices = response.get("choices")
    if not isinstance(choices, list) or not choices:
        raise ValueError("missing response choices")
    message = choices[0].get("message", {})
    content = message.get("content") if isinstance(message, dict) else None
    if not isinstance(content, str):
        raise ValueError("missing response content")
    if judge in {"aer", "aerv"}:
        for line in content.splitlines():
            if line.strip().lower().startswith("status:"):
                return parse_binary_label(line.split(":", 1)[1])
    if judge == "nnetnav":
        match = re.search(r"Reward\s*:\s*([1-5])", content, re.I)
        if match:
            return int(int(match.group(1)) >= 4)
    match = SUCCESS_RE.search(content)
    if not match:
        raise ValueError("unparseable success judgment")
    return parse_binary_label(match.group(1))


def parse_binary_label(value: str, *, allow_unsure: bool = False) -> int | None:
    normalized = re.sub(r"[^a-z0-9]+", " ", value.strip().lower()).strip()
    if normalized in {"successful", "success", "yes", "true", "1", "passed", "pass"}:
        return 1
    if normalized in {
        "unsuccessful",
        "failure",
        "failed",
        "no",
        "false",
        "0",
        "not successful",
    }:
        return 0
    if allow_unsure and normalized in {"unsure", "unknown", "n a"}:
        return None
    raise ValueError(f"Unknown binary label: {value!r}")


def write_outputs(output: Path, rows: list[dict[str, Any]], audit: dict[str, Any]) -> None:
    output.mkdir(parents=True, exist_ok=True)
    with (output / "matrix.jsonl").open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
    (output / "audit.json").write_text(
        json.dumps(audit, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def _trajectory_id(annotation: dict[str, str]) -> str:
    return "::".join(
        (annotation["benchmark"], annotation["model_name"], annotation["task_id"])
    )


def _number(value: Any) -> float | None:
    return float(value) if isinstance(value, (int, float)) else None


def _integer(value: Any) -> int | None:
    return int(value) if isinstance(value, (int, float)) else None


def _judge_inputs(payload: dict[str, Any]) -> dict[str, bool] | None:
    raw = payload.get("judge_args")
    if not isinstance(raw, dict):
        return None
    return {
        "screenshot": bool(raw.get("use_screenshot", False)),
        "axtree": bool(raw.get("use_axtree", False)),
    }


def _system_prompt_hash(payload: dict[str, Any]) -> str | None:
    chat = payload.get("chat_messages")
    regular = chat.get("regular") if isinstance(chat, dict) else None
    if not isinstance(regular, list):
        return None
    for message in regular:
        if not isinstance(message, dict) or message.get("role") != "system":
            continue
        content = message.get("content")
        if isinstance(content, str):
            return hashlib.sha256(content.encode("utf-8")).hexdigest()
    return None


if __name__ == "__main__":
    raise SystemExit(main())
