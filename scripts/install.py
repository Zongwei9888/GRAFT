#!/usr/bin/env python3
"""Install the source checkout as user-level Codex hooks.

This convenience entry point keeps the documented clone-and-install path to one
command while delegating all state changes to the tested installer.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from graft.codex.global_install import install_global_hooks, provision_runtime  # noqa: E402
from graft.schema import to_jsonable  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="Install GRAFT for Codex")
    parser.add_argument("--codex-home", type=Path)
    args = parser.parse_args()
    runtime = provision_runtime(source_root=PROJECT_ROOT)
    result = install_global_hooks(runtime, codex_home=args.codex_home)
    payload = to_jsonable(result)
    payload["next"] = "Start Codex, review GRAFT in /hooks, then approve its exact hook hashes."
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
