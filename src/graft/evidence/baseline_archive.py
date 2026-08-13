from __future__ import annotations

import difflib
import io
import json
import os
import re
import tarfile
import tempfile
from pathlib import Path, PurePosixPath
from typing import Mapping, Sequence

from graft.schema import SourceSnapshot


BASELINE_METADATA = "baseline.json"
MAX_DIFF_CHARS = 48_000
MAX_DIFF_FILE_BYTES = 256 * 1024
MAX_ARCHIVE_BYTES = 8 * 1024 * 1024
MAX_FULL_ARCHIVE_BYTES = 256 * 1024 * 1024


def archive_baseline(
    workspace: Path,
    *,
    files: Sequence[str],
    file_hashes: Mapping[str, str],
    tree_hash: str,
    archive_root: Path,
    session_id: str,
    task_epoch: int,
    include_binary: bool = False,
) -> Path:
    """Persist the task-start source outside the producer workspace.

    Hash-only baselines can identify changed files but cannot show a verifier
    which semantics were retained from the broken starting state. This archive
    is immutable task history, not a contract oracle: prompts label its diff as
    implementation evidence only.
    """

    root = workspace.expanduser().resolve()
    destination_root = archive_root.expanduser().resolve()
    if destination_root == root or root in destination_root.parents:
        raise ValueError("Baseline archives must be stored outside the workspace")

    safe_session = re.sub(r"[^A-Za-z0-9_.-]", "_", session_id)[:160] or "unknown"
    destination = destination_root / safe_session
    destination.mkdir(parents=True, exist_ok=True)
    archive_path = destination / f"epoch-{task_epoch:03d}-{tree_hash[:16]}.tar.gz"
    if archive_path.exists():
        return archive_path

    skipped: list[str] = []
    archived_bytes = 0
    with tempfile.NamedTemporaryFile(
        dir=destination, prefix=".baseline-", suffix=".tar.gz", delete=False
    ) as handle:
        temporary = Path(handle.name)
    try:
        with tarfile.open(temporary, mode="w:gz", dereference=False) as archive:
            for relative in files:
                pure = _safe_relative(relative)
                if pure is None:
                    skipped.append(relative)
                    continue
                source = root.joinpath(*pure.parts)
                if not source.is_file() or source.is_symlink():
                    skipped.append(relative)
                    continue
                try:
                    content = source.read_bytes()
                    if include_binary:
                        if archived_bytes + len(content) > MAX_FULL_ARCHIVE_BYTES:
                            skipped.append(relative)
                            continue
                    else:
                        if (
                            len(content) > MAX_DIFF_FILE_BYTES
                            or archived_bytes + len(content) > MAX_ARCHIVE_BYTES
                            or b"\0" in content
                        ):
                            skipped.append(relative)
                            continue
                        content.decode("utf-8")
                    info = archive.gettarinfo(
                        str(source),
                        arcname=(PurePosixPath("baseline") / pure).as_posix(),
                    )
                    info.size = len(content)
                    archive.addfile(info, io.BytesIO(content))
                    archived_bytes += len(content)
                except (FileNotFoundError, OSError, UnicodeDecodeError):
                    skipped.append(relative)

            payload = (
                json.dumps(
                    {
                        "version": 1,
                        "session_id": session_id,
                        "task_epoch": task_epoch,
                        "tree_hash": tree_hash,
                        "files": list(files),
                        "file_hashes": dict(file_hashes),
                        "archive_mode": (
                            "full-regular-files-v1" if include_binary else "text-v1"
                        ),
                        "archived_bytes": archived_bytes,
                        "archived_text_bytes": (
                            None if include_binary else archived_bytes
                        ),
                        "skipped_files": sorted(skipped),
                    },
                    ensure_ascii=False,
                    indent=2,
                )
                + "\n"
            ).encode("utf-8")
            info = tarfile.TarInfo(BASELINE_METADATA)
            info.size = len(payload)
            info.mode = 0o600
            info.mtime = 0
            archive.addfile(info, io.BytesIO(payload))
        os.replace(temporary, archive_path)
    finally:
        temporary.unlink(missing_ok=True)
    return archive_path


def baseline_diff_excerpt(
    snapshot: SourceSnapshot,
    *,
    max_chars: int = MAX_DIFF_CHARS,
    max_file_bytes: int = MAX_DIFF_FILE_BYTES,
) -> str:
    """Return a bounded baseline-to-candidate text diff for model prompts."""

    if not snapshot.baseline_archive_path:
        return "<immutable baseline content unavailable>"
    archive_path = Path(snapshot.baseline_archive_path).expanduser().resolve()
    if not archive_path.is_file():
        return "<immutable baseline archive missing>"

    try:
        with tarfile.open(archive_path, mode="r:gz") as archive:
            metadata_file = archive.extractfile(BASELINE_METADATA)
            if metadata_file is None:
                return "<invalid baseline archive: metadata missing>"
            metadata = json.load(metadata_file)
            if metadata.get("tree_hash") != snapshot.baseline_tree_hash:
                return "<baseline archive tree hash does not match this checkpoint>"
            archived_hashes = {
                str(path): str(digest)
                for path, digest in metadata.get("file_hashes", {}).items()
            }
            if archived_hashes != dict(snapshot.baseline_file_hashes):
                return "<baseline archive manifest does not match this checkpoint>"

            changed = sorted(
                path
                for path in set(archived_hashes) | set(snapshot.file_hashes)
                if archived_hashes.get(path) != snapshot.file_hashes.get(path)
            )
            if not changed:
                return "<no baseline-to-candidate source changes>"
            skipped = {str(path) for path in metadata.get("skipped_files", [])}

            sections: list[str] = []
            for relative in changed:
                pure = _safe_relative(relative)
                if pure is None:
                    sections.append(f"[skipped unsafe path: {relative}]")
                    continue
                if relative in skipped and relative in archived_hashes:
                    sections.append(
                        f"[baseline text unavailable because it was binary, oversized, "
                        f"non-UTF-8, or beyond the archive budget: {relative}]"
                    )
                    continue
                before = _archived_bytes(archive, pure)
                after = _current_bytes(Path(snapshot.root), pure)
                sections.append(
                    _file_diff(
                        relative,
                        before,
                        after,
                        max_file_bytes=max_file_bytes,
                    )
                )
                joined = "\n".join(sections)
                if len(joined) >= max_chars:
                    return joined[:max_chars] + "\n...<baseline diff truncated>"
    except (OSError, tarfile.TarError, json.JSONDecodeError, TypeError, ValueError):
        return "<immutable baseline archive could not be read>"
    return "\n".join(sections)


def _safe_relative(value: str) -> PurePosixPath | None:
    pure = PurePosixPath(value)
    if pure.is_absolute() or not pure.parts or ".." in pure.parts:
        return None
    return pure


def _archived_bytes(archive: tarfile.TarFile, relative: PurePosixPath) -> bytes | None:
    name = (PurePosixPath("baseline") / relative).as_posix()
    try:
        member = archive.getmember(name)
    except KeyError:
        return None
    if not member.isfile() or member.size > MAX_DIFF_FILE_BYTES:
        return None
    extracted = archive.extractfile(member)
    return extracted.read() if extracted is not None else None


def _current_bytes(root: Path, relative: PurePosixPath) -> bytes | None:
    candidate = root.resolve().joinpath(*relative.parts)
    try:
        resolved = candidate.resolve()
        resolved.relative_to(root.resolve())
        if not resolved.is_file() or candidate.is_symlink():
            return None
        if resolved.stat().st_size > MAX_DIFF_FILE_BYTES:
            return None
        return resolved.read_bytes()
    except (OSError, ValueError):
        return None


def _file_diff(
    relative: str,
    before: bytes | None,
    after: bytes | None,
    *,
    max_file_bytes: int,
) -> str:
    if before is None and after is None:
        return f"[binary, symlink, oversized, or unavailable change: {relative}]"
    if before is not None and len(before) > max_file_bytes:
        before = None
    if after is not None and len(after) > max_file_bytes:
        after = None
    if (before is not None and b"\0" in before) or (after is not None and b"\0" in after):
        return f"[binary change: {relative}]"
    try:
        before_text = before.decode("utf-8") if before is not None else ""
        after_text = after.decode("utf-8") if after is not None else ""
    except UnicodeDecodeError:
        return f"[non-UTF-8 change: {relative}]"
    return "".join(
        difflib.unified_diff(
            before_text.splitlines(keepends=True),
            after_text.splitlines(keepends=True),
            fromfile=f"baseline/{relative}" if before is not None else "/dev/null",
            tofile=f"candidate/{relative}" if after is not None else "/dev/null",
            n=3,
        )
    ).rstrip()
