#!/usr/bin/env python3
from __future__ import annotations

import json
import sys
import tomllib
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from graft import __version__  # noqa: E402
from graft.registry import load_config  # noqa: E402


def main() -> int:
    errors: list[str] = []
    project = tomllib.loads((PROJECT_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    package_version = project["project"]["version"]
    manifest = json.loads(
        (PROJECT_ROOT / "plugins/graft/.codex-plugin/plugin.json").read_text(
            encoding="utf-8"
        )
    )
    versions = {
        "package": package_version,
        "runtime": __version__,
        "plugin": str(manifest.get("version", "")).split("+", 1)[0],
    }
    if len(set(versions.values())) != 1:
        errors.append(f"Base version mismatch: {versions}")

    root_schema = PROJECT_ROOT / "schemas/graft_config.schema.json"
    packaged_schema = PROJECT_ROOT / "src/graft/resources/graft_config.schema.json"
    if root_schema.read_bytes() != packaged_schema.read_bytes():
        errors.append("Packaged GRAFT config schema does not match schemas/graft_config.schema.json")

    required = (
        "README.md",
        "LICENSE",
        "CHANGELOG.md",
        "CONTRIBUTING.md",
        "SECURITY.md",
        "docs/installation.md",
        "docs/configuration.md",
        "docs/codex-compatibility.md",
        ".agents/plugins/marketplace.json",
        "plugins/graft/hooks/hooks.json",
    )
    for relative in required:
        if not (PROJECT_ROOT / relative).is_file():
            errors.append(f"Missing release file: {relative}")

    for relative in (".graft/config.json", "configs/codex-review-enabled.example.json"):
        path = PROJECT_ROOT / relative
        if path.is_file():
            try:
                load_config(path)
            except (OSError, ValueError, json.JSONDecodeError) as exc:
                errors.append(f"Invalid config {relative}: {exc}")

    if errors:
        for error in errors:
            print(f"ERROR: {error}", file=sys.stderr)
        return 1
    print(f"Release checks passed for GRAFT {__version__}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
