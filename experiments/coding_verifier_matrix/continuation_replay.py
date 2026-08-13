from __future__ import annotations

import argparse
import hashlib
import json
import os
import shlex
import shutil
import tarfile
import tempfile
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from pathlib import Path, PurePosixPath
from typing import Any, Mapping, Sequence

from experiments.coding_verifier_matrix.verifier_matrix import (
    OuterContainerCopyCodexRunner,
)
from graft.controller import GraftController
from graft.evidence.baseline_archive import BASELINE_METADATA, archive_baseline
from graft.evidence.snapshot import hash_tree_manifest
from graft.registry import load_config
from graft.replay import load_report_graph, replay_selection
from graft.schema import (
    PromotionOutcome,
    PromotionRequirement,
    Verdict,
    VerifierResult,
    to_jsonable,
)
from graft.verifiers import VerifierExecutor


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Restore and continue a frozen coding verifier-matrix checkpoint."
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    restore = subparsers.add_parser("restore")
    restore.add_argument("--repo", type=Path, required=True)
    restore.add_argument("--archive", type=Path, required=True)
    restore.add_argument("--archive-sha256", required=True)
    restore.add_argument("--baseline", type=Path, required=True)
    restore.add_argument("--expected-tree", required=True)

    feedback = subparsers.add_parser("feedback")
    feedback.add_argument("--matrix", type=Path, required=True)
    feedback.add_argument("--config", type=Path, required=True)
    feedback.add_argument("--output", type=Path, required=True)

    promote = subparsers.add_parser("promote")
    promote.add_argument("--repo", type=Path, required=True)
    promote.add_argument("--matrix", type=Path, required=True)
    promote.add_argument("--config", type=Path, required=True)
    promote.add_argument("--baseline", type=Path, required=True)
    promote.add_argument("--baseline-archive", type=Path, required=True)
    promote.add_argument("--requirements", type=Path, required=True)
    promote.add_argument("--archive-root", type=Path, required=True)
    promote.add_argument("--output", type=Path, required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.command == "restore":
        payload = restore_candidate(
            args.repo,
            args.archive,
            archive_sha256=args.archive_sha256,
            baseline_path=args.baseline,
            expected_tree=args.expected_tree,
        )
    elif args.command == "feedback":
        payload = feedback_packet(args.matrix, args.config)
        _write_json(args.output, payload)
    else:
        payload = run_promotion(
            args.repo,
            matrix_path=args.matrix,
            config_path=args.config,
            baseline_path=args.baseline,
            baseline_archive=args.baseline_archive,
            requirements_path=args.requirements,
            archive_root=args.archive_root,
        )
        _write_json(args.output, payload)
    print(json.dumps(payload, ensure_ascii=False))
    return 0


def restore_candidate(
    repo: Path,
    archive_path: Path,
    *,
    archive_sha256: str,
    baseline_path: Path,
    expected_tree: str,
) -> dict[str, Any]:
    root = repo.expanduser().resolve()
    archive = archive_path.expanduser().resolve()
    baseline = json.loads(baseline_path.read_text(encoding="utf-8"))
    observed_archive_sha = _sha256_file(archive)
    if observed_archive_sha != archive_sha256:
        raise ValueError(
            f"Candidate archive digest mismatch: {observed_archive_sha}"
        )
    before_tree, _, _ = hash_tree_manifest(root)
    if before_tree != str(baseline["tree_hash"]):
        raise ValueError(
            f"Fresh task tree mismatch: expected {baseline['tree_hash']}, "
            f"observed {before_tree}"
        )

    with tarfile.open(archive, "r:gz") as handle:
        members = _validate_candidate_archive(handle)
        metadata_file = handle.extractfile(BASELINE_METADATA)
        if metadata_file is None:
            raise ValueError("Candidate archive metadata is unreadable")
        metadata = json.load(metadata_file)
        if metadata.get("tree_hash") != expected_tree:
            raise ValueError("Candidate archive tree does not match the frozen config")
        candidate_files = _safe_string_list(metadata.get("files"), "files")
        baseline_files = _safe_string_list(baseline.get("files"), "baseline files")

        for relative in sorted(set(baseline_files) - set(candidate_files)):
            target = _target(root, relative)
            if target.is_dir() and not target.is_symlink():
                shutil.rmtree(target)
            else:
                target.unlink(missing_ok=True)

        by_name = {member.name: member for member in members}
        for relative in candidate_files:
            name = (PurePosixPath("baseline") / PurePosixPath(relative)).as_posix()
            member = by_name.get(name)
            if member is None:
                continue
            if not member.isfile():
                raise ValueError(f"Unexpected non-file candidate member: {name}")
            source = handle.extractfile(member)
            if source is None:
                raise ValueError(f"Candidate member is unreadable: {name}")
            target = _target(root, relative)
            if target.is_dir() and not target.is_symlink():
                shutil.rmtree(target)
            target.parent.mkdir(parents=True, exist_ok=True)
            with target.open("wb") as output:
                shutil.copyfileobj(source, output)
            os.chmod(target, member.mode & 0o777)

    after_tree, files, file_hashes = hash_tree_manifest(root)
    if after_tree != expected_tree:
        raise ValueError(
            f"Restored candidate tree mismatch: expected {expected_tree}, "
            f"observed {after_tree}"
        )
    expected_hashes = {
        str(path): str(digest)
        for path, digest in dict(metadata.get("file_hashes", {})).items()
    }
    if tuple(files) != tuple(candidate_files) or file_hashes != expected_hashes:
        raise ValueError("Restored candidate manifest does not match archive metadata")
    return {
        "status": "restored",
        "tree_hash": after_tree,
        "archive_sha256": observed_archive_sha,
        "file_count": len(files),
        "skipped_file_count": len(metadata.get("skipped_files", [])),
    }


def feedback_packet(matrix_path: Path, config_path: Path) -> dict[str, Any]:
    matrix = json.loads(matrix_path.read_text(encoding="utf-8"))
    graph = load_report_graph(matrix_path)
    config = load_config(config_path)
    selection = replay_selection(matrix_path, config)
    selected = set(selection.verifier_ids)
    eligible = tuple(
        item
        for item in matrix.get("results", [])
        if isinstance(item, Mapping)
        and str(item.get("verifier_id")) in selected
        and item.get("verdict") == Verdict.FAIL.value
        and bool(item.get("blocking"))
        and bool(item.get("reproducible"))
    )
    if not eligible:
        raise ValueError("Frozen selection contains no eligible executable feedback")

    behaviors = {item.behavior_id: item for item in graph.behaviors}
    failures = {item.failure_mode_id: item for item in graph.failure_modes}
    lines = [
        "[GRAFT Verification Failure — frozen shadow replay]",
        f"Checkpoint: {matrix['checkpoint_key']}",
        "Reproducible blocking evidence selected by the frozen Original policy:",
    ]
    for result in eligible:
        verifier_id = str(result["verifier_id"])
        lines.append(f"- Verifier {verifier_id}: {result.get('summary', '')}")
        result_modes = tuple(str(item) for item in result.get("failure_modes", []))
        for failure_id in result_modes:
            failure = failures.get(failure_id)
            if failure is None:
                continue
            behavior = behaviors.get(failure.behavior_id)
            if behavior is not None:
                lines.append(f"  Violated behavior: {behavior.description}")
            lines.append(f"  Failure mode: {failure.description}")
        reproductions = _eligible_evidence(result, set(result_modes))
        for evidence in reproductions:
            lines.append(f"  Observation: {evidence.get('observation', '')}")
            lines.append(
                "  Reproduce: "
                + shlex.join(tuple(str(part) for part in evidence["command"]))
            )
    lines.append(
        "Inspect and resolve only the evidenced behavior; choose the repair strategy yourself. "
        "Preserve all other behavior. Rerun the exact reproductions after the repair. "
        "Do not inspect benchmark solution or evaluator files."
    )
    feedback = "\n".join(lines)
    return {
        "version": 1,
        "status": "feedback_ready",
        "checkpoint_key": str(matrix["checkpoint_key"]),
        "selection": to_jsonable(selection),
        "selected_eligible_verifiers": [str(item["verifier_id"]) for item in eligible],
        "feedback": feedback,
        "feedback_sha256": hashlib.sha256(feedback.encode("utf-8")).hexdigest(),
    }


def run_promotion(
    repo: Path,
    *,
    matrix_path: Path,
    config_path: Path,
    baseline_path: Path,
    baseline_archive: Path,
    requirements_path: Path,
    archive_root: Path,
) -> dict[str, Any]:
    root = repo.expanduser().resolve()
    matrix = json.loads(matrix_path.read_text(encoding="utf-8"))
    baseline = json.loads(baseline_path.read_text(encoding="utf-8"))
    requirements_raw = json.loads(requirements_path.read_text(encoding="utf-8"))
    requirements = tuple(str(item) for item in requirements_raw)
    config = load_config(config_path)
    executor = VerifierExecutor(
        codex_runner=OuterContainerCopyCodexRunner(root),
    )
    controller = GraftController(
        config,
        config_path=config_path,
        executor=executor,
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
        baseline_archive_path=str(baseline_archive.resolve()),
    )
    packet = feedback_packet(matrix_path, config_path)
    selected_ids = set(packet["selected_eligible_verifiers"])
    records = {
        str(item["verifier_id"]): item
        for item in matrix.get("results", [])
        if isinstance(item, Mapping) and str(item.get("verifier_id")) in selected_ids
    }
    original_graph = load_report_graph(matrix_path)
    target_modes = tuple(
        dict.fromkeys(
            str(mode)
            for record in records.values()
            for mode in record.get("failure_modes", [])
        )
    )
    failures = {
        item.failure_mode_id: item for item in original_graph.failure_modes
    }
    behaviors = {item.behavior_id: item for item in original_graph.behaviors}
    evidence_items = tuple(
        evidence
        for record in records.values()
        for evidence in _eligible_evidence(record, set(target_modes))
    )
    promotion = PromotionRequirement(
        feedback_checkpoint_key=str(matrix["checkpoint_key"]),
        report_path=str(matrix_path.resolve()),
        behavior_descriptions=tuple(
            dict.fromkeys(
                behaviors[failures[mode].behavior_id].description
                for mode in target_modes
                if mode in failures and failures[mode].behavior_id in behaviors
            )
        ),
        failure_descriptions=tuple(
            failures[mode].description for mode in target_modes if mode in failures
        ),
        evidence_observations=tuple(
            str(item.get("observation", "")) for item in evidence_items
        ),
        reproduction_commands=tuple(
            tuple(str(part) for part in item["command"]) for item in evidence_items
        ),
    )
    graph = replace(
        original_graph,
        source_hash=snapshot.checkpoint_key,
        promotion=promotion,
    )
    by_id = {item.verifier_id: item for item in graph.verifiers}
    specs = tuple(
        replace(
            by_id[verifier_id],
            failure_modes=tuple(
                str(item) for item in records[verifier_id].get("failure_modes", [])
            ),
            isolation="temporary-copy",
            revalidates_feedback=True,
            objective=(
                by_id[verifier_id].objective
                + " Re-run the frozen feedback reproductions and verify that the repair "
                "fixes them while preserving the public task behaviors."
            ),
        )
        for verifier_id in packet["selected_eligible_verifiers"]
        if verifier_id in by_id
    )
    if not specs:
        raise ValueError("No frozen selected verifier is available for promotion")

    def execute(spec) -> VerifierResult:
        return executor.run(
            spec,
            snapshot,
            requirements=requirements,
            graph=graph,
            config_path=config_path,
            environment_fingerprint=config.environment_fingerprint,
        )

    with ThreadPoolExecutor(max_workers=min(4, len(specs))) as pool:
        results = tuple(pool.map(execute, specs))
    after = controller.snapshot(
        root,
        requirements,
        baseline_tree_hash=snapshot.baseline_tree_hash,
        baseline_files=snapshot.baseline_files,
        baseline_file_hashes=snapshot.baseline_file_hashes,
        baseline_archive_path=snapshot.baseline_archive_path,
    )
    source_stable = after.tree_hash == snapshot.tree_hash
    promoted = source_stable and all(
        item.verdict == Verdict.PASS
        and item.executed_evidence
        and item.promotion_outcome == PromotionOutcome.FIXED_AND_PRESERVED
        for item in results
    )
    repaired_archive = archive_baseline(
        root,
        files=snapshot.files,
        file_hashes=snapshot.file_hashes,
        tree_hash=snapshot.tree_hash,
        archive_root=archive_root,
        session_id="coding-verifier-matrix-repaired",
        task_epoch=1,
    )
    return {
        "version": 1,
        "status": "promoted" if promoted else "unresolved",
        "source_stable": source_stable,
        "feedback_checkpoint_key": str(matrix["checkpoint_key"]),
        "repaired_checkpoint_key": snapshot.checkpoint_key,
        "repaired_tree_hash": snapshot.tree_hash,
        "after_tree_hash": after.tree_hash,
        "repaired_archive_path": str(repaired_archive),
        "repaired_archive_sha256": _sha256_file(repaired_archive),
        "selected_eligible_verifiers": list(packet["selected_eligible_verifiers"]),
        "target_failure_modes": list(target_modes),
        "promotion_requirement": to_jsonable(promotion),
        "results": [to_jsonable(item) for item in results],
        "official_evaluator_visible": False,
    }


def _eligible_evidence(
    record: Mapping[str, Any], valid_modes: set[str]
) -> tuple[Mapping[str, Any], ...]:
    allowed = {
        "authoritative_runtime",
        "baseline_repository",
        "requirement_derived_runtime",
    }
    return tuple(
        item
        for item in record.get("evidence", [])
        if isinstance(item, Mapping)
        and item.get("oracle_origin") in allowed
        and isinstance(item.get("command"), list)
        and bool(item.get("command"))
        and bool(set(str(mode) for mode in item.get("failure_modes", [])) & valid_modes)
    )


def _validate_candidate_archive(
    archive: tarfile.TarFile,
) -> tuple[tarfile.TarInfo, ...]:
    members = tuple(archive.getmembers())
    names = {item.name for item in members}
    if BASELINE_METADATA not in names:
        raise ValueError("Candidate archive is missing metadata")
    for member in members:
        pure = PurePosixPath(member.name)
        if pure.is_absolute() or ".." in pure.parts:
            raise ValueError(f"Unsafe candidate archive member: {member.name}")
        if member.name == BASELINE_METADATA:
            continue
        if not pure.parts or pure.parts[0] != "baseline":
            raise ValueError(f"Unexpected candidate archive member: {member.name}")
        if member.issym() or member.islnk():
            raise ValueError(f"Candidate archive links are forbidden: {member.name}")
    return members


def _safe_string_list(value: Any, label: str) -> tuple[str, ...]:
    if not isinstance(value, list) or not all(
        isinstance(item, str) and _valid_relative(item) for item in value
    ):
        raise ValueError(f"Invalid {label}")
    return tuple(value)


def _valid_relative(value: str) -> bool:
    pure = PurePosixPath(value)
    return bool(pure.parts) and not pure.is_absolute() and ".." not in pure.parts


def _target(root: Path, relative: str) -> Path:
    if not _valid_relative(relative):
        raise ValueError(f"Unsafe candidate path: {relative}")
    target = root.joinpath(*PurePosixPath(relative).parts)
    target.resolve(strict=False).relative_to(root)
    return target


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _write_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


if __name__ == "__main__":
    raise SystemExit(main())
