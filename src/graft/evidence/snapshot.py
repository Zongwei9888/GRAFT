from __future__ import annotations

import hashlib
import os
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Mapping, Sequence

from graft.schema import SourceSnapshot


DEFAULT_EXCLUDED_DIRS = frozenset(
    {
        ".git",
        ".graft",
        ".mypy_cache",
        ".pytest_cache",
        ".ruff_cache",
        ".tox",
        ".venv",
        "__pycache__",
        "artifacts",
        "build",
        "dist",
        "node_modules",
        "venv",
    }
)
DEFAULT_EXCLUDED_FILES = frozenset({".DS_Store"})


def hash_texts(values: Iterable[str]) -> str:
    digest = hashlib.sha256()
    for value in values:
        encoded = value.encode("utf-8", errors="surrogatepass")
        digest.update(len(encoded).to_bytes(8, "big"))
        digest.update(encoded)
    return digest.hexdigest()


def hash_file(path: Path | None) -> str:
    if path is None or not path.exists():
        return hashlib.sha256(b"").hexdigest()
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _iter_source_entries(
    root: Path,
    excluded_dirs: frozenset[str],
    excluded_files: frozenset[str],
) -> Iterable[tuple[str, Path]]:
    for current, dirs, files in os.walk(root, topdown=True, followlinks=False):
        dirs[:] = sorted(
            name
            for name in dirs
            if name not in excluded_dirs and not name.endswith(".egg-info")
        )
        current_path = Path(current)
        for name in sorted(files):
            if name in excluded_files or name.endswith((".pyc", ".pyo")):
                continue
            path = current_path / name
            relative = path.relative_to(root).as_posix()
            yield relative, path


def _iter_git_entries(
    root: Path,
    excluded_dirs: frozenset[str],
    excluded_files: frozenset[str],
) -> Iterable[tuple[str, Path]] | None:
    """Use repository ignore policy when ``root`` is the Git worktree root."""

    try:
        top = subprocess.run(
            ["git", "-C", str(root), "rev-parse", "--show-toplevel"],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            check=False,
            timeout=5,
        )
        if top.returncode != 0 or Path(os.fsdecode(top.stdout).strip()).resolve() != root:
            return None
        listed = subprocess.run(
            [
                "git",
                "-C",
                str(root),
                "ls-files",
                "--cached",
                "--others",
                "--exclude-standard",
                "-z",
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            check=False,
            timeout=15,
        )
    except (OSError, subprocess.TimeoutExpired, ValueError):
        return None
    if listed.returncode != 0:
        return None
    entries: list[tuple[str, Path]] = []
    for encoded in listed.stdout.split(b"\0"):
        if not encoded:
            continue
        relative_path = Path(os.fsdecode(encoded))
        if relative_path.is_absolute() or ".." in relative_path.parts:
            continue
        if any(
            part in excluded_dirs or part.endswith(".egg-info")
            for part in relative_path.parts[:-1]
        ):
            continue
        if (
            relative_path.name in excluded_files
            or relative_path.suffix in {".pyc", ".pyo"}
        ):
            continue
        entries.append((relative_path.as_posix(), root / relative_path))
    return tuple(sorted(entries, key=lambda item: item[0]))


def hash_tree(
    root: Path,
    *,
    excluded_dirs: frozenset[str] = DEFAULT_EXCLUDED_DIRS,
    excluded_files: frozenset[str] = DEFAULT_EXCLUDED_FILES,
) -> tuple[str, tuple[str, ...]]:
    tree_hash, files, _ = hash_tree_manifest(
        root,
        excluded_dirs=excluded_dirs,
        excluded_files=excluded_files,
    )
    return tree_hash, files


def hash_tree_manifest(
    root: Path,
    *,
    excluded_dirs: frozenset[str] = DEFAULT_EXCLUDED_DIRS,
    excluded_files: frozenset[str] = DEFAULT_EXCLUDED_FILES,
) -> tuple[str, tuple[str, ...], dict[str, str]]:
    resolved = root.resolve()
    if not resolved.is_dir():
        raise ValueError(f"Workspace is not a directory: {resolved}")

    digest = hashlib.sha256()
    names: list[str] = []
    manifest: dict[str, str] = {}
    entries = _iter_git_entries(resolved, excluded_dirs, excluded_files)
    if entries is None:
        entries = _iter_source_entries(resolved, excluded_dirs, excluded_files)
    for relative, path in entries:
        names.append(relative)
        entry_digest = hashlib.sha256()
        relative_bytes = relative.encode("utf-8", errors="surrogatepass")
        digest.update(len(relative_bytes).to_bytes(8, "big"))
        digest.update(relative_bytes)
        if path.is_symlink():
            target = os.readlink(path).encode("utf-8", errors="surrogatepass")
            digest.update(b"L")
            digest.update(len(target).to_bytes(8, "big"))
            digest.update(target)
            entry_digest.update(b"L")
            entry_digest.update(target)
            manifest[relative] = entry_digest.hexdigest()
            continue
        try:
            stat = path.stat()
            digest.update(b"F")
            digest.update(stat.st_size.to_bytes(8, "big"))
            entry_digest.update(b"F")
            entry_digest.update(stat.st_size.to_bytes(8, "big"))
            with path.open("rb") as handle:
                for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                    digest.update(chunk)
                    entry_digest.update(chunk)
        except (FileNotFoundError, PermissionError, OSError) as exc:
            marker = f"UNREADABLE:{type(exc).__name__}".encode("utf-8")
            digest.update(marker)
            entry_digest.update(marker)
        manifest[relative] = entry_digest.hexdigest()
    return digest.hexdigest(), tuple(names), manifest


def freeze_source(
    root: Path,
    *,
    requirements: Sequence[str] = (),
    config_path: Path | None = None,
    environment_fingerprint: str = "local",
    baseline_tree_hash: str | None = None,
    baseline_files: Sequence[str] = (),
    baseline_file_hashes: Mapping[str, str] | None = None,
) -> SourceSnapshot:
    tree_hash, files, file_hashes = hash_tree_manifest(root)
    requirement_hash = hash_texts(requirements)
    config_hash = hash_file(config_path)
    checkpoint_key = hash_texts(
        (
            tree_hash,
            requirement_hash,
            environment_fingerprint,
            config_hash,
            baseline_tree_hash or "",
        )
    )
    return SourceSnapshot(
        root=str(root.resolve()),
        tree_hash=tree_hash,
        requirement_hash=requirement_hash,
        config_hash=config_hash,
        checkpoint_key=checkpoint_key,
        files=files,
        created_at=datetime.now(timezone.utc).isoformat(),
        baseline_tree_hash=baseline_tree_hash,
        baseline_files=tuple(baseline_files),
        file_hashes=file_hashes,
        baseline_file_hashes=dict(baseline_file_hashes or {}),
    )
