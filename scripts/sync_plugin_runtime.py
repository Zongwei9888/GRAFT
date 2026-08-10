#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import shutil
import tempfile
from pathlib import Path


REPOSITORY = Path(__file__).resolve().parents[1]
SOURCE = REPOSITORY / "src" / "graft"
TARGET = REPOSITORY / "plugins" / "graft" / "runtime" / "src" / "graft"


def tree_hashes(root: Path) -> dict[str, str]:
    result: dict[str, str] = {}
    if not root.is_dir():
        return result
    for path in sorted(root.rglob("*")):
        if not path.is_file() or "__pycache__" in path.parts:
            continue
        if path.suffix in {".pyc", ".pyo"}:
            continue
        result[path.relative_to(root).as_posix()] = hashlib.sha256(
            path.read_bytes()
        ).hexdigest()
    return result


def sync_runtime() -> None:
    TARGET.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=TARGET.parent) as directory:
        staged = Path(directory) / "graft"
        shutil.copytree(
            SOURCE,
            staged,
            ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "*.pyo", "*.egg-info"),
        )
        if TARGET.exists():
            shutil.rmtree(TARGET)
        shutil.copytree(staged, TARGET)


def main() -> int:
    parser = argparse.ArgumentParser(description="Synchronize GRAFT Core into the plugin")
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    if args.check:
        if tree_hashes(SOURCE) != tree_hashes(TARGET):
            print("Plugin runtime is stale. Run: python3 scripts/sync_plugin_runtime.py")
            return 1
        print("Plugin runtime matches src/graft.")
        return 0
    sync_runtime()
    print(f"Synchronized plugin runtime: {TARGET}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
