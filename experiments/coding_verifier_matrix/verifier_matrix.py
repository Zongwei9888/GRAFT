from __future__ import annotations

import argparse
import hashlib
import json
import re
import tarfile
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Sequence

from graft.codex.cli_runner import CliCodexRunner
from graft.controller import GraftController
from graft.evidence.baseline_archive import BASELINE_METADATA, archive_baseline
from graft.evidence.snapshot import hash_tree_manifest
from graft.modeling import FeedbackGraphBuildError
from graft.registry import default_original_config_payload, load_config
from graft.replay import load_report_graph
from graft.schema import RunConfig, Verdict, VerifierResult, to_jsonable
from graft.verifiers import VerifierExecutor


def select_workspace(current_directory: str, git_candidates: Sequence[str]) -> Path:
    """Resolve the task workspace without assuming that it is a Git worktree.

    A benchmark container can contain tool or controller repositories unrelated to
    the task. The current task directory therefore has authority: prefer its
    closest enclosing Git worktree, otherwise use the directory itself. A root
    working directory is not a safe fallback because snapshotting it could traverse
    the whole container.
    """

    current_text = current_directory.strip()
    if not current_text:
        raise RuntimeError("Task working directory was not reported")
    current = Path(current_text).resolve()
    if not current.is_absolute() or current == Path(current.anchor):
        raise RuntimeError(f"Unsafe task working directory: {current}")

    roots = {
        Path(candidate.strip()).resolve()
        for candidate in git_candidates
        if candidate.strip()
    }
    enclosing = tuple(
        root for root in roots if root == current or root in current.parents
    )
    if enclosing:
        # Nested worktrees are legitimate. The closest enclosing root owns the
        # current task directory even if unrelated repositories also exist.
        return max(enclosing, key=lambda path: len(path.parts))
    return current


def select_unique_workspace(candidates: Sequence[str]) -> Path:
    """Return the single discovered Git worktree or fail the infrastructure row.

    Workspace discovery is intentionally independent of benchmark task text and
    verifier planning.  Silently choosing among multiple repositories could make
    the producer and the shadow verifiers inspect different source trees.
    """

    roots = {
        str(Path(candidate.strip()).resolve())
        for candidate in candidates
        if candidate.strip()
    }
    if len(roots) != 1:
        rendered = ", ".join(sorted(roots)) if roots else "none"
        raise RuntimeError(
            "Expected exactly one task Git worktree; discovered " + rendered
        )
    return Path(next(iter(roots)))


class OuterContainerCopyCodexRunner(CliCodexRunner):
    """Run verifier tools in an expendable copy when nested seccomp is unavailable.

    FeatureBench's x86 image runs under Docker Desktop emulation on this host. The
    nested Codex Linux seccomp sandbox cannot initialize there. Harbor already
    supplies a disposable outer container, and the matrix executor additionally
    gives every verifier a fresh source copy. This experiment-only runner removes
    the unsupported inner sandbox; it must never receive the producer worktree.
    """

    def __init__(self, producer_root: Path) -> None:
        super().__init__()
        self.producer_root = producer_root.expanduser().resolve()

    def copy_config(self, repo: Path, config: RunConfig) -> RunConfig:
        resolved = repo.expanduser().resolve()
        if resolved == self.producer_root:
            raise RuntimeError(
                "Unrestricted nested execution is forbidden in the producer worktree"
            )
        return replace(
            config,
            sandbox="danger-full-access",
            network_access=False,
        )

    def start_thread(
        self, task: str, repo: Path, config: RunConfig = RunConfig()
    ):
        return super().start_thread(task, repo, self.copy_config(repo, config))


class DisposableBranchCodexRunner(CliCodexRunner):
    """Run one verifier inside its own expendable benchmark environment.

    Unlike :class:`OuterContainerCopyCodexRunner`, the supplied repository is the
    authoritative root of a fresh Harbor task branch. Absolute task paths such as
    ``/app`` therefore resolve inside the branch. The orchestrator must never reuse
    this environment for a producer, another verifier, or an official candidate
    score.
    """

    def __init__(self, branch_root: Path) -> None:
        super().__init__()
        self.branch_root = branch_root.expanduser().resolve()

    def branch_config(self, repo: Path, config: RunConfig) -> RunConfig:
        resolved = repo.expanduser().resolve()
        if resolved != self.branch_root:
            raise RuntimeError(
                "Disposable verifier runner received a non-branch workspace"
            )
        return replace(
            config,
            sandbox="danger-full-access",
            network_access=False,
        )

    def start_thread(
        self, task: str, repo: Path, config: RunConfig = RunConfig()
    ):
        return super().start_thread(task, repo, self.branch_config(repo, config))


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
    run.add_argument("--candidate-archive-root", type=Path, required=True)
    run.add_argument("--max-verifiers", type=int, default=8)

    candidate = subparsers.add_parser("candidate")
    candidate.add_argument("--repo", type=Path, required=True)
    candidate.add_argument("--baseline", type=Path, required=True)
    candidate.add_argument("--requirements", type=Path, required=True)
    candidate.add_argument("--config", type=Path, required=True)
    candidate.add_argument("--output", type=Path, required=True)
    candidate.add_argument("--archive-root", type=Path, required=True)

    plan = subparsers.add_parser("plan")
    plan.add_argument("--repo", type=Path, required=True)
    plan.add_argument("--baseline", type=Path, required=True)
    plan.add_argument("--candidate", type=Path, required=True)
    plan.add_argument("--requirements", type=Path, required=True)
    plan.add_argument("--config", type=Path, required=True)
    plan.add_argument("--output", type=Path, required=True)
    plan.add_argument("--max-verifiers", type=int, default=8)

    branch = subparsers.add_parser("branch")
    branch.add_argument("--repo", type=Path, required=True)
    branch.add_argument("--baseline", type=Path, required=True)
    branch.add_argument("--plan", type=Path, required=True)
    branch.add_argument("--requirements", type=Path, required=True)
    branch.add_argument("--config", type=Path, required=True)
    branch.add_argument("--verifier-id", required=True)
    branch.add_argument("--output", type=Path, required=True)

    assemble = subparsers.add_parser("assemble")
    assemble.add_argument("--plan", type=Path, required=True)
    assemble.add_argument("--result", type=Path, action="append", required=True)
    assemble.add_argument("--output", type=Path, required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.command == "config":
        payload = materialize_config(args.model)
    elif args.command == "capture":
        payload = capture_baseline(args.repo, args.archive_root)
    elif args.command == "run":
        requirements = _load_requirements(args.requirements)
        baseline = json.loads(args.baseline.read_text(encoding="utf-8"))
        payload = run_matrix(
            args.repo,
            requirements,
            baseline,
            config_path=args.config,
            candidate_archive_root=args.candidate_archive_root,
            max_verifiers=args.max_verifiers,
        )
    elif args.command == "candidate":
        payload = capture_candidate(
            args.repo,
            _load_requirements(args.requirements),
            json.loads(args.baseline.read_text(encoding="utf-8")),
            config_path=args.config,
            archive_root=args.archive_root,
        )
    elif args.command == "plan":
        payload = plan_matrix(
            args.repo,
            _load_requirements(args.requirements),
            json.loads(args.baseline.read_text(encoding="utf-8")),
            json.loads(args.candidate.read_text(encoding="utf-8")),
            config_path=args.config,
            max_verifiers=args.max_verifiers,
        )
    elif args.command == "branch":
        payload = run_verifier_branch(
            args.repo,
            _load_requirements(args.requirements),
            json.loads(args.baseline.read_text(encoding="utf-8")),
            plan_path=args.plan,
            config_path=args.config,
            verifier_id=args.verifier_id,
        )
    else:
        payload = assemble_branch_matrix(args.plan, tuple(args.result))
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
        "archive_sha256": _sha256_file(archive),
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


def capture_candidate(
    repo: Path,
    requirements: tuple[str, ...],
    baseline: dict[str, Any],
    *,
    config_path: Path,
    archive_root: Path,
) -> dict[str, Any]:
    """Freeze a producer candidate without constructing or running GRAFT.

    This phase finishes before the benchmark's official evaluator runs. Later
    graph planning and verifier execution consume only this content-addressed
    checkpoint in separate task environments.
    """

    root = repo.expanduser().resolve()
    _validate_baseline_root(root, baseline)
    if not requirements:
        raise ValueError("Raw requirements are required")
    resolved_config = config_path.expanduser().resolve()
    config = load_config(resolved_config)
    controller = GraftController(
        config,
        config_path=resolved_config,
        report_root=None,
    )
    snapshot = _candidate_snapshot(
        controller,
        root,
        requirements,
        baseline,
    )
    changed = _changed_paths(snapshot.file_hashes, snapshot.baseline_file_hashes)
    archive = archive_baseline(
        root,
        files=snapshot.files,
        file_hashes=snapshot.file_hashes,
        tree_hash=snapshot.tree_hash,
        archive_root=archive_root,
        session_id="coding-verifier-candidate-capture",
        task_epoch=1,
        include_binary=True,
    )
    skipped = _archive_skipped_files(archive)
    unreplayable = tuple(sorted(set(skipped) & set(changed)))
    common = {
        "version": 1,
        "experiment": "coding-verifier-environment-branches",
        "phase": "candidate",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "root": str(root),
        "requirements_hash": snapshot.requirement_hash,
        "baseline_tree_hash": snapshot.baseline_tree_hash,
        "candidate_tree_hash": snapshot.tree_hash,
        "candidate_files": list(snapshot.files),
        "checkpoint_key": snapshot.checkpoint_key,
        "changed_paths": list(changed),
        "candidate_archive_path": str(archive),
        "candidate_archive_sha256": _sha256_file(archive),
        "candidate_archive_skipped_files": list(skipped),
        "unreplayable_changed_files": list(unreplayable),
        "config_hash": snapshot.config_hash,
        "official_evaluator_visible": False,
        "graft_modeling_started": False,
        "verifier_count": 0,
    }
    if not changed:
        return {**common, "status": "no_candidate_change"}
    if unreplayable:
        return {
            **common,
            "status": "candidate_not_replayable",
            "reason": "candidate_archive_skipped_files",
        }
    return {**common, "status": "candidate_captured"}


def plan_matrix(
    repo: Path,
    requirements: tuple[str, ...],
    baseline: dict[str, Any],
    candidate: dict[str, Any],
    *,
    config_path: Path,
    max_verifiers: int,
) -> dict[str, Any]:
    """Build the dynamic graph on a restored candidate, without verification."""

    root = repo.expanduser().resolve()
    _validate_baseline_root(root, baseline)
    if candidate.get("status") != "candidate_captured":
        raise ValueError("Candidate is not eligible for graph planning")
    if Path(str(candidate.get("root", ""))).resolve() != root:
        raise ValueError("Candidate belongs to a different workspace")
    if max_verifiers < 1:
        raise ValueError("max_verifiers must be positive")
    resolved_config = config_path.expanduser().resolve()
    config = load_config(resolved_config)
    controller = GraftController(
        config,
        config_path=resolved_config,
        report_root=None,
    )
    snapshot = _candidate_snapshot(
        controller,
        root,
        requirements,
        baseline,
    )
    _validate_restored_candidate(snapshot, candidate)
    common = {
        **candidate,
        "phase": "plan",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "status": "pending",
        "graft_modeling_started": True,
        "official_evaluator_visible": False,
        "feedback_sent_to_producer": False,
        "execution_policy": {
            "producer": "closed-before-planning",
            "planner": "fresh-restored-task-environment",
            "verifier": "one-fresh-restored-task-environment-per-verifier",
            "official_score": "producer-candidate-only",
        },
    }
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
    return {
        **common,
        "status": "planned",
        "graph": to_jsonable(graph),
        "verifier_count": len(verifiers),
        "verifier_ids": [item.verifier_id for item in verifiers],
    }


def run_verifier_branch(
    repo: Path,
    requirements: tuple[str, ...],
    baseline: dict[str, Any],
    *,
    plan_path: Path,
    config_path: Path,
    verifier_id: str,
) -> dict[str, Any]:
    """Run exactly one planned verifier in a whole-environment branch."""

    root = repo.expanduser().resolve()
    _validate_baseline_root(root, baseline)
    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    if plan.get("status") != "planned":
        raise ValueError("Verifier branch requires a completed matrix plan")
    graph = load_report_graph(plan_path)
    by_id = {item.verifier_id: item for item in graph.verifiers}
    if verifier_id not in by_id:
        raise ValueError(f"Unknown planned verifier: {verifier_id}")
    resolved_config = config_path.expanduser().resolve()
    config = load_config(resolved_config)
    executor = VerifierExecutor(
        codex_runner=DisposableBranchCodexRunner(root),
        protect_source_workspace=False,
        disposable_environment=True,
    )
    controller = GraftController(
        config,
        config_path=resolved_config,
        executor=executor,
        report_root=None,
    )
    snapshot = _candidate_snapshot(
        controller,
        root,
        requirements,
        baseline,
    )
    _validate_restored_candidate(snapshot, plan)
    spec = replace(by_id[verifier_id], isolation="ephemeral")
    result = executor.run(
        spec,
        snapshot,
        requirements=requirements,
        graph=graph,
        config_path=resolved_config,
        environment_fingerprint=config.environment_fingerprint,
    )
    after = _candidate_snapshot(
        controller,
        root,
        requirements,
        baseline,
    )
    return {
        "version": 1,
        "experiment": "coding-verifier-environment-branches",
        "phase": "verifier",
        "status": "complete",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "verifier_id": verifier_id,
        "checkpoint_key": snapshot.checkpoint_key,
        "candidate_tree_hash": snapshot.tree_hash,
        "after_tree_hash": after.tree_hash,
        "branch_mutated": after.tree_hash != snapshot.tree_hash,
        "result": to_jsonable(result),
        "official_evaluator_visible": False,
        "feedback_sent_to_producer": False,
        "execution_policy": {
            "environment": "single-use-full-task-branch",
            "candidate": "content-addressed-restore",
            "shared_with_producer": False,
            "shared_with_other_verifiers": False,
            "inner_sandbox": "disabled-due-to-nested-seccomp-incompatibility",
        },
    }


def assemble_branch_matrix(plan_path: Path, result_paths: tuple[Path, ...]) -> dict[str, Any]:
    """Join independently executed verifier branches into one replayable matrix."""

    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    if plan.get("status") != "planned":
        raise ValueError("Branch assembly requires a completed matrix plan")
    expected = tuple(str(item) for item in plan.get("verifier_ids", []))
    records: dict[str, dict[str, Any]] = {}
    for path in result_paths:
        branch = json.loads(path.read_text(encoding="utf-8"))
        verifier_id = str(branch.get("verifier_id", ""))
        if branch.get("status") != "complete":
            raise ValueError(f"Verifier branch is incomplete: {path}")
        if branch.get("checkpoint_key") != plan.get("checkpoint_key"):
            raise ValueError(f"Verifier branch checkpoint mismatch: {path}")
        if verifier_id not in expected:
            raise ValueError(f"Unexpected verifier branch: {verifier_id}")
        if verifier_id in records:
            raise ValueError(f"Duplicate verifier branch: {verifier_id}")
        raw_result = branch.get("result")
        if not isinstance(raw_result, dict):
            raise ValueError(f"Verifier branch has no result: {path}")
        if str(raw_result.get("verifier_id", "")) != verifier_id:
            raise ValueError(f"Verifier result identity mismatch: {path}")
        records[verifier_id] = raw_result
    missing = tuple(item for item in expected if item not in records)
    if missing:
        raise ValueError("Missing verifier branches: " + ", ".join(missing))
    ordered = tuple(records[item] for item in expected)
    eligible = tuple(
        str(item["verifier_id"])
        for item in ordered
        if item.get("verdict") == Verdict.FAIL.value
        and bool(item.get("blocking"))
        and bool(item.get("reproducible"))
    )
    return {
        **plan,
        "phase": "matrix",
        "status": "complete",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "results": list(ordered),
        "eligible_reproducible_failure_verifiers": list(eligible),
        "producer_untouched_by_design": True,
        "branch_result_count": len(ordered),
    }


def _candidate_snapshot(
    controller: GraftController,
    root: Path,
    requirements: tuple[str, ...],
    baseline: dict[str, Any],
):
    return controller.snapshot(
        root,
        requirements,
        baseline_tree_hash=str(baseline["tree_hash"]),
        baseline_files=tuple(str(item) for item in baseline["files"]),
        baseline_file_hashes={
            str(path): str(digest)
            for path, digest in dict(baseline["file_hashes"]).items()
        },
        baseline_archive_path=str(baseline.get("archive_path", "")) or None,
    )


def _validate_baseline_root(root: Path, baseline: dict[str, Any]) -> None:
    if Path(str(baseline.get("root", ""))).resolve() != root:
        raise ValueError("Baseline belongs to a different workspace")


def _validate_restored_candidate(snapshot, expected: dict[str, Any]) -> None:
    if snapshot.tree_hash != str(expected.get("candidate_tree_hash", "")):
        raise ValueError("Restored candidate tree does not match the frozen checkpoint")
    if snapshot.checkpoint_key != str(expected.get("checkpoint_key", "")):
        raise ValueError("Restored candidate checkpoint does not match the frozen checkpoint")


def run_matrix(
    repo: Path,
    requirements: tuple[str, ...],
    baseline: dict[str, Any],
    *,
    config_path: Path,
    candidate_archive_root: Path,
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
        executor=VerifierExecutor(
            codex_runner=OuterContainerCopyCodexRunner(root),
        ),
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
    candidate_archive = archive_baseline(
        root,
        files=snapshot.files,
        file_hashes=snapshot.file_hashes,
        tree_hash=snapshot.tree_hash,
        archive_root=candidate_archive_root,
        session_id="coding-verifier-matrix-candidate",
        task_epoch=1,
        include_binary=True,
    )
    skipped_candidate_files = _archive_skipped_files(candidate_archive)
    unreplayable_changed_files = tuple(
        sorted(set(skipped_candidate_files) & set(changed))
    )
    common = {
        "version": 1,
        "experiment": "coding-verifier-matrix",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "status": "pending",
        "requirements_hash": snapshot.requirement_hash,
        "baseline_tree_hash": snapshot.baseline_tree_hash,
        "candidate_tree_hash": snapshot.tree_hash,
        "candidate_files": list(snapshot.files),
        "checkpoint_key": snapshot.checkpoint_key,
        "changed_paths": list(changed),
        "candidate_archive_path": str(candidate_archive),
        "candidate_archive_sha256": _sha256_file(candidate_archive),
        "candidate_archive_skipped_files": list(skipped_candidate_files),
        "unreplayable_changed_files": list(unreplayable_changed_files),
        "config_source": "frozen_experiment_config",
        "config_hash": snapshot.config_hash,
        "official_evaluator_visible": False,
        "feedback_sent_to_producer": False,
        "execution_policy": {
            "producer_workspace": "immutable",
            "verifier_workspace": "fresh-temporary-copy",
            "inner_sandbox": "disabled-due-to-nested-seccomp-incompatibility",
            "outer_isolation": "disposable-harbor-container",
        },
    }
    if not changed:
        return {**common, "status": "no_candidate_change", "verifier_count": 0}
    if unreplayable_changed_files:
        return {
            **common,
            "status": "candidate_not_replayable",
            "reason": "candidate_archive_skipped_files",
            "verifier_count": 0,
        }
    absolute_root_requirements = _absolute_workspace_requirement_refs(
        requirements, root
    )
    if absolute_root_requirements:
        return {
            **common,
            "status": "isolation_not_supported",
            "reason": "temporary_copy_cannot_virtualize_absolute_workspace_paths",
            "absolute_workspace_requirement_refs": list(
                absolute_root_requirements
            ),
            "verifier_count": 0,
        }

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
        isolated_spec = replace(spec, isolation="temporary-copy")
        return controller.executor.run(
            isolated_spec,
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


def _absolute_workspace_requirement_refs(
    requirements: tuple[str, ...], root: Path
) -> tuple[str, ...]:
    """Identify raw requirements that can escape a temporary workspace copy."""

    rendered = re.escape(root.resolve().as_posix().rstrip("/"))
    boundary = re.compile(rendered + r"(?=$|[/\s`'\"),;:\]])")
    return tuple(
        f"R{index}"
        for index, requirement in enumerate(requirements, start=1)
        if boundary.search(requirement)
    )


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _archive_skipped_files(path: Path) -> tuple[str, ...]:
    """Read the immutable archive manifest without extracting candidate files."""

    try:
        with tarfile.open(path, mode="r:gz") as archive:
            metadata_file = archive.extractfile(BASELINE_METADATA)
            if metadata_file is None:
                raise ValueError("Candidate archive metadata is missing")
            metadata = json.load(metadata_file)
    except (OSError, tarfile.TarError, json.JSONDecodeError, TypeError) as exc:
        raise ValueError("Candidate archive metadata is invalid") from exc
    raw = metadata.get("skipped_files", [])
    if not isinstance(raw, list) or not all(isinstance(item, str) for item in raw):
        raise ValueError("Candidate archive skipped_files is invalid")
    return tuple(sorted(set(raw)))


if __name__ == "__main__":
    raise SystemExit(main())
