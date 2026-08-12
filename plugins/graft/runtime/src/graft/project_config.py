from __future__ import annotations

import json
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from graft.registry import (
    default_original_config_payload,
    default_value_aware_config_payload,
)


@dataclass(frozen=True)
class ProjectConfigResult:
    path: Path
    created: bool
    verifier_ids: tuple[str, ...]
    warnings: tuple[str, ...]


@dataclass(frozen=True)
class ProjectToggleResult:
    path: Path
    enabled: bool
    created: bool
    verifier_ids: tuple[str, ...]


def initialize_project(
    workspace: Path,
    *,
    checkpoint_mode: str = "completion",
    selection_policy: str = "original",
    verifier_network_access: bool = False,
    force: bool = False,
) -> ProjectConfigResult:
    """Create a domain-neutral local override for a supported GRAFT policy.

    Initialization deliberately does not discover language-specific commands. At runtime, the
    modeler derives Behaviors and Failure Modes from the raw task, and the planner instantiates
    general verifier capabilities for that checkpoint.
    """

    if checkpoint_mode not in {"completion", "strict", "explicit"}:
        raise ValueError("checkpoint_mode must be completion, strict, or explicit")
    if selection_policy not in {"original", "value-aware"}:
        raise ValueError("selection_policy must be original or value-aware")
    root = workspace.expanduser().resolve()
    target = root / ".graft" / "config.json"
    if target.exists() and not force:
        raise FileExistsError(
            f"GRAFT configuration already exists: {target}; use --force to replace it"
        )
    payload = (
        default_value_aware_config_payload()
        if selection_policy == "value-aware"
        else default_original_config_payload()
    )
    payload["checkpoint_mode"] = checkpoint_mode
    if verifier_network_access:
        for template in payload["verifier_templates"]:
            if template.get("sandbox") == "workspace-write":
                template["network_access"] = True
    target.parent.mkdir(parents=True, exist_ok=True)
    _atomic_json_write(target, payload)
    ids = tuple(str(item["id"]) for item in payload["verifier_templates"])
    warnings = (
        (
            "Workspace-write verifier agents may use outbound network access; review this "
            "project configuration before trusting it.",
        )
        if verifier_network_access
        else ()
    )
    return ProjectConfigResult(target, True, ids, warnings)


def set_project_enabled(workspace: Path, enabled: bool) -> ProjectToggleResult:
    root = workspace.expanduser().resolve()
    target = root / ".graft" / "config.json"
    created = not target.exists()
    if created:
        raw = default_original_config_payload(enabled=enabled)
        target.parent.mkdir(parents=True, exist_ok=True)
    else:
        try:
            raw = json.loads(target.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise ValueError(f"Cannot parse existing GRAFT config {target}: {exc}") from exc
        if not isinstance(raw, dict):
            raise ValueError(f"GRAFT config must contain a JSON object: {target}")
        if int(raw.get("version", 0)) != 2:
            raise ValueError(
                "Only GRAFT config version 2 can be enabled or disabled; "
                "regenerate legacy configs with `graft init --force`"
            )
        raw["enabled"] = enabled
        if enabled and not raw.get("verifier_templates"):
            defaults = default_original_config_payload()
            raw["verifier_templates"] = defaults["verifier_templates"]
            raw["budget"] = defaults["budget"]
    _atomic_json_write(target, raw)
    verifier_ids = tuple(
        str(item.get("id"))
        for item in raw.get("verifier_templates", [])
        if isinstance(item, dict) and item.get("id") is not None
    )
    return ProjectToggleResult(target, enabled, created, verifier_ids)


def _atomic_json_write(path: Path, payload: dict[str, Any]) -> None:
    with tempfile.NamedTemporaryFile(
        "w", encoding="utf-8", dir=path.parent, delete=False
    ) as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
        temporary = Path(handle.name)
    os.replace(temporary, path)
