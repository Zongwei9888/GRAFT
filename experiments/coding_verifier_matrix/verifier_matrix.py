from __future__ import annotations

import argparse
import json
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Sequence

from graft.controller import GraftController
from graft.evidence.baseline_archive import archive_baseline
from graft.evidence.snapshot import hash_tree_manifest
from graft.modeling import FeedbackGraphBuildError
from graft.registry import default_original_config_payload, load_config
from graft.schema import Verdict, VerifierResult, to_jsonable


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Capture a coding baseline or run a shadow verifier matrix."
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    config = subparsers.add_parser("config")
    config.add_argument("--model", required=True)
    config.add_argument("--output", type=Path, required=True)

    capture = subparsers.add_parser("capture")
    capture.add_argument("--repo", type=Path, required=True)
    capture.add_argument("--output", type=Path, required=True)
    capture.add_argument("--archive-root", type=Path, required=True)

    run = subparsers.add_parser("run")
    run.add_argument("--repo", type=Path, required=True)
    run.add_argument("--baseline", type=Path, required=True)
    run.add_argument("--requirements", type=Path, required=True)
    run.add_argument("--config", type=Path, required=True)
    run.add_argument("--output", type=Path, required=True)
    run.add_argument("--max-verifiers", type=int, default=8)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.command == "config":
        payload = materialize_config(args.model)
    elif args.command == "capture":
        payload = capture_baseline(args.repo, args.archive_root)
    else:
        requirements = _load_requirements(args.requirements)
        baseline = json.loads(args.baseline.read_text(encoding="utf-8"))
        payload = run_matrix(
            args.repo,
            requirements,
            baseline,
            config_path=args.config,
            max_verifiers=args.max_verifiers,
        )
    _write_json(args.output, payload)
    print(
        json.dumps(
            {"status": payload.get("status", f"{args.command}_complete")},
            ensure_ascii=False,
        )
    )
    return 0


def capture_baseline(repo: Path, archive_root: Path) -> dict[str, Any]:
    root = repo.expanduser().resolve()
    tree_hash, files, file_hashes = hash_tree_manifest(root)
    archive = archive_baseline(
        root,
        files=files,
        file_hashes=file_hashes,
        tree_hash=tree_hash,
        archive_root=archive_root,
        session_id="coding-verifier-matrix",
        task_epoch=1,
    )
    return {
        "version": 1,
        "status": "captured",
        "captured_at": datetime.now(timezone.utc).isoformat(),
        "root": str(root),
        "tree_hash": tree_hash,
        "files": list(files),
        "file_hashes": file_hashes,
        "archive_path": str(archive),
    }


def materialize_config(model: str) -> dict[str, Any]:
    model_slug = model.strip()
    if not model_slug:
        raise ValueError("model must not be empty")
    payload = default_original_config_payload()
    payload["checkpoint_mode"] = "explicit"
    payload["environment_fingerprint"] = (
        f"coding-verifier-matrix:{model_slug}:v1"
    )
    payload["modeling"]["behavior_modeler"]["model"] = model_slug
    payload["modeling"]["verifier_planner"]["model"] = model_slug
    for template in payload["verifier_templates"]:
        if template["kind"] not in {"codex_agent", "codex_review"}:
            continue
        template["model"] = model_slug
        template["lineage"]["model"] = model_slug
    payload["_experiment"] = {
        "name": "coding-verifier-matrix",
        "feedback_enabled": False,
        "run_all_planned_verifiers": True,
    }
    return payload


def run_matrix(
    repo: Path,
    requirements: tuple[str, ...],
    baseline: dict[str, Any],
    *,
    config_path: Path,
    max_verifiers: int,
) -> dict[str, Any]:
    root = repo.expanduser().resolve()
    if max_verifiers < 1:
        raise ValueError("max_verifiers must be positive")
    if not requirements:
        raise ValueError("Raw requirements are required")
    if Path(str(baseline.get("root", ""))).resolve() != root:
        raise ValueError("Baseline belongs to a different workspace")

    resolved_config = config_path.expanduser().resolve()
    config = load_config(resolved_config)
    controller = GraftController(
        config,
        config_path=resolved_config,
        report_root=None,
    )
    snapshot = controller.snapshot(
        root,
        requirements,
        baseline_tree_hash=str(baseline["tree_hash"]),
        baseline_files=tuple(str(item) for item in baseline["files"]),
        baseline_file_hashes={
            str(path): str(digest)
            for path, digest in dict(baseline["file_hashes"]).items()
        },
        baseline_archive_path=str(baseline["archive_path"]),
    )
    changed = _changed_paths(snapshot.file_hashes, snapshot.baseline_file_hashes)
    common = {
        "version": 1,
        "experiment": "coding-verifier-matrix",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "status": "pending",
        "requirements_hash": snapshot.requirement_hash,
        "baseline_tree_hash": snapshot.baseline_tree_hash,
        "candidate_tree_hash": snapshot.tree_hash,
        "checkpoint_key": snapshot.checkpoint_key,
        "changed_paths": list(changed),
        "config_source": "frozen_experiment_config",
        "config_hash": snapshot.config_hash,
        "official_evaluator_visible": False,
        "feedback_sent_to_producer": False,
    }
    if not changed:
        return {**common, "status": "no_candidate_change", "verifier_count": 0}

    try:
        graph = controller.graph_builder.build(
            snapshot,
            requirements,
            config,
            config_path=resolved_config,
        )
    except (FeedbackGraphBuildError, ValueError) as exc:
        return {
            **common,
            "status": "graph_error",
            "error": str(exc),
            "verifier_count": 0,
        }
    if graph.source_hash != snapshot.checkpoint_key:
        return {
            **common,
            "status": "stale_graph",
            "graph": to_jsonable(graph),
            "verifier_count": len(graph.verifiers),
        }
    verifiers = tuple(sorted(graph.verifiers, key=lambda item: item.verifier_id))
    if len(verifiers) > max_verifiers:
        return {
            **common,
            "status": "resource_censored",
            "reason": "planner_exceeded_frozen_max_verifiers",
            "graph": to_jsonable(graph),
            "verifier_count": len(verifiers),
        }

    def execute(spec) -> VerifierResult:
        return controller.executor.run(
            spec,
            snapshot,
            requirements=requirements,
            graph=graph,
            config_path=resolved_config,
            environment_fingerprint=config.environment_fingerprint,
        )

    with ThreadPoolExecutor(max_workers=min(4, len(verifiers))) as pool:
        results = tuple(pool.map(execute, verifiers))

    after = controller.snapshot(
        root,
        requirements,
        baseline_tree_hash=snapshot.baseline_tree_hash,
        baseline_files=snapshot.baseline_files,
        baseline_file_hashes=snapshot.baseline_file_hashes,
        baseline_archive_path=snapshot.baseline_archive_path,
    )
    source_stable = after.tree_hash == snapshot.tree_hash
    eligible = tuple(
        result.verifier_id
        for result in results
        if result.verdict == Verdict.FAIL and result.blocking and result.reproducible
    )
    return {
        **common,
        "status": "complete" if source_stable else "producer_workspace_mutated",
        "source_stable": source_stable,
        "after_tree_hash": after.tree_hash,
        "graph": to_jsonable(graph),
        "verifier_count": len(verifiers),
        "results": [to_jsonable(item) for item in results],
        "eligible_reproducible_failure_verifiers": list(eligible),
    }


def _load_requirements(path: Path) -> tuple[str, ...]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, list) or not all(isinstance(item, str) for item in raw):
        raise ValueError("Requirements file must contain a JSON array of strings")
    return tuple(item for item in raw if item.strip())


def _changed_paths(
    current: dict[str, str] | Any, baseline: dict[str, str] | Any
) -> tuple[str, ...]:
    current_map = dict(current)
    baseline_map = dict(baseline)
    return tuple(
        sorted(
            path
            for path in set(current_map) | set(baseline_map)
            if current_map.get(path) != baseline_map.get(path)
        )
    )


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


if __name__ == "__main__":
    raise SystemExit(main())
