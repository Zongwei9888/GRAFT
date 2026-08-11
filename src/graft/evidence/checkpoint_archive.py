from __future__ import annotations

import hashlib
import io
import json
import os
import re
import tarfile
import tempfile
from pathlib import Path, PurePosixPath

from graft.schema import SourceSnapshot


CHECKPOINT_ARCHIVE_ENV = "GRAFT_CHECKPOINT_ARCHIVE_HOME"


def archive_checkpoint(
    snapshot: SourceSnapshot,
    *,
    session_id: str,
    task_epoch: int,
    verification_round: int,
) -> Path | None:
    """Archive an observable Stop checkpoint when experiment capture is enabled.

    Production installations do nothing unless ``GRAFT_CHECKPOINT_ARCHIVE_HOME``
    is set explicitly. The archive is stored outside the producer workspace so
    capture cannot change the source checkpoint that GRAFT verifies.
    """

    configured = os.environ.get(CHECKPOINT_ARCHIVE_ENV)
    if not configured:
        return None

    workspace = Path(snapshot.root).resolve()
    archive_root = Path(configured).expanduser().resolve()
    if archive_root == workspace or workspace in archive_root.parents:
        raise ValueError("Checkpoint archives must be stored outside the workspace")

    safe_session = re.sub(r"[^A-Za-z0-9_.-]", "_", session_id)[:160] or "unknown"
    destination = archive_root / safe_session
    destination.mkdir(parents=True, exist_ok=True)
    archive_path = destination / (
        f"epoch-{task_epoch:03d}-round-{verification_round:03d}-"
        f"{snapshot.checkpoint_key[:16]}.tar.gz"
    )
    if archive_path.exists():
        return archive_path

    skipped: list[str] = []
    with tempfile.NamedTemporaryFile(
        dir=destination, prefix=".checkpoint-", suffix=".tar.gz", delete=False
    ) as handle:
        temporary = Path(handle.name)
    try:
        with tarfile.open(temporary, mode="w:gz", dereference=False) as archive:
            for relative in snapshot.files:
                pure = PurePosixPath(relative)
                if pure.is_absolute() or not pure.parts or ".." in pure.parts:
                    skipped.append(relative)
                    continue
                source = workspace.joinpath(*pure.parts)
                if not source.is_file() and not source.is_symlink():
                    skipped.append(relative)
                    continue
                try:
                    archive.add(
                        source,
                        arcname=(PurePosixPath("workspace") / pure).as_posix(),
                        recursive=False,
                    )
                except (FileNotFoundError, OSError):
                    skipped.append(relative)

            metadata = {
                "version": 1,
                "session_id": session_id,
                "task_epoch": task_epoch,
                "verification_round": verification_round,
                "checkpoint_key": snapshot.checkpoint_key,
                "tree_hash": snapshot.tree_hash,
                "requirement_hash": snapshot.requirement_hash,
                "config_hash": snapshot.config_hash,
                "created_at": snapshot.created_at,
                "files": list(snapshot.files),
                "file_hashes": dict(snapshot.file_hashes),
                "baseline_tree_hash": snapshot.baseline_tree_hash,
                "baseline_files": list(snapshot.baseline_files),
                "baseline_file_hashes": dict(snapshot.baseline_file_hashes),
                "deleted_baseline_files": sorted(
                    set(snapshot.baseline_files) - set(snapshot.files)
                ),
                "skipped_files": sorted(skipped),
            }
            payload = (
                json.dumps(metadata, ensure_ascii=False, indent=2) + "\n"
            ).encode("utf-8")
            info = tarfile.TarInfo("checkpoint.json")
            info.size = len(payload)
            info.mode = 0o644
            info.mtime = 0
            archive.addfile(info, io.BytesIO(payload))
        os.replace(temporary, archive_path)
    finally:
        temporary.unlink(missing_ok=True)

    digest = hashlib.sha256(archive_path.read_bytes()).hexdigest()
    checksum_path = archive_path.with_suffix(archive_path.suffix + ".sha256")
    _atomic_text(checksum_path, f"{digest}  {archive_path.name}\n")
    return archive_path


def _atomic_text(path: Path, content: str) -> None:
    with tempfile.NamedTemporaryFile(
        "w", encoding="utf-8", dir=path.parent, delete=False
    ) as handle:
        handle.write(content)
        temporary = Path(handle.name)
    os.replace(temporary, path)
