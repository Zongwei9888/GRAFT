from __future__ import annotations

import argparse
import hashlib
import json
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
            candidate_archive_root=args.candidate_archive_root,
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
    )
    skipped_candidate_files = _archive_skipped_files(candidate_archive)
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
    if skipped_candidate_files:
        return {
            **common,
            "status": "candidate_not_replayable",
            "reason": "candidate_archive_skipped_files",
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
