#!/usr/bin/env python3
"""Dependency-free launcher for the GRAFT runtime bundled with the plugin."""

from __future__ import print_function

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path


MINIMUM_PYTHON = (3, 11)


def _plugin_root():
    configured = os.environ.get("PLUGIN_ROOT")
    if configured:
        candidate = Path(configured).expanduser().resolve()
        if candidate.is_dir():
            return candidate
    return Path(__file__).resolve().parents[1]


def _supports_runtime(executable):
    try:
        completed = subprocess.run(
            [
                executable,
                "-c",
                "import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)",
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return completed.returncode == 0


def _find_runtime_python():
    if sys.version_info >= MINIMUM_PYTHON:
        return sys.executable
    configured = os.environ.get("GRAFT_PYTHON")
    candidates = [configured] if configured else []
    candidates.extend(
        shutil.which(name)
        for name in ("python3.13", "python3.12", "python3.11", "python3")
    )
    for candidate in candidates:
        if candidate and _supports_runtime(candidate):
            return candidate
    return None


def _emit_hook_warning(message):
    print(
        json.dumps(
            {
                "continue": True,
                "systemMessage": message,
            },
            ensure_ascii=False,
        )
    )
    return 0


def main(argv=None):
    arguments = list(argv if argv is not None else sys.argv[1:])
    mode = arguments[0] if arguments else None
    runtime_python = _find_runtime_python()
    if runtime_python is None:
        message = (
            "GRAFT plugin is fail-open because Python 3.11+ is unavailable. "
            "Install Python 3.11+ or set GRAFT_PYTHON."
        )
        if mode == "hook":
            return _emit_hook_warning(message)
        print(message, file=sys.stderr)
        return 2
    if Path(runtime_python).resolve() != Path(sys.executable).resolve():
        os.execv(runtime_python, [runtime_python, str(Path(__file__).resolve()), *arguments])

    runtime_src = _plugin_root() / "runtime" / "src"
    if not (runtime_src / "graft" / "__init__.py").is_file():
        message = f"GRAFT plugin runtime is missing: {runtime_src}"
        if mode == "hook":
            return _emit_hook_warning(message)
        print(message, file=sys.stderr)
        return 2
    sys.path.insert(0, str(runtime_src))

    try:
        if mode == "hook":
            from graft.codex.hooks import main as hook_main

            return hook_main(arguments[1:])
        if mode == "cli":
            from graft.cli import main as cli_main

            return cli_main(arguments[1:])
        print("Usage: graft_plugin.py {hook|cli} ...", file=sys.stderr)
        return 2
    except Exception as exc:
        if mode == "hook":
            return _emit_hook_warning(
                f"GRAFT plugin failed open: {type(exc).__name__}: {exc}"
            )
        raise


if __name__ == "__main__":
    raise SystemExit(main())
