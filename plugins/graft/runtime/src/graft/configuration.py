from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from graft.registry import GraftConfig, load_config
from graft.runtime_paths import config_home, workspace_identifier


@dataclass(frozen=True)
class ResolvedConfig:
    workspace: Path
    path: Path | None
    source: str
    reason: str

    @property
    def configured(self) -> bool:
        return self.path is not None

    def load(self) -> GraftConfig:
        if self.path is None:
            raise ValueError(f"Workspace is observe-only: {self.reason}")
        return load_config(self.path)


@dataclass(frozen=True)
class ProjectConfigTrust:
    workspace: Path
    config_path: Path
    config_hash: str | None
    trusted_hash: str | None
    trusted: bool


def resolve_config(workspace: Path) -> ResolvedConfig:
    root = workspace.expanduser().resolve()
    project = root / ".graft" / "config.json"
    if project.is_file():
        trust = project_config_trust(root)
        if trust.trusted:
            return ResolvedConfig(
                root, project, "project", "trusted project configuration"
            )

    profiles = config_home() / "profiles"
    if profiles.is_dir():
        for profile_path in sorted(profiles.glob("*.json")):
            try:
                raw = json.loads(profile_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            if not isinstance(raw, Mapping) or not _profile_matches(root, raw):
                continue
            config = raw.get("config")
            if not isinstance(config, Mapping):
                continue
            materialized = _materialize_profile(profile_path, config)
            return ResolvedConfig(
                root,
                materialized,
                f"profile:{profile_path.stem}",
                f"matched user profile {profile_path.name}",
            )

    if _is_git_workspace(root):
        safe = _safe_git_config()
        project_reason = (
            "; project config is untrusted or changed—run `graft config trust` after review"
            if project.is_file()
            else ""
        )
        return ResolvedConfig(
            root,
            safe,
            "safe-git",
            "no trusted project/profile configuration; using git diff --check only"
            + project_reason,
        )
    return ResolvedConfig(
        root,
        None,
        "observe",
        (
            "project config is untrusted or changed and workspace is not a Git repository"
            if project.is_file()
            else "no project configuration and workspace is not a Git repository"
        ),
    )


def project_config_trust(workspace: Path) -> ProjectConfigTrust:
    root = workspace.expanduser().resolve()
    project = root / ".graft" / "config.json"
    digest = _file_hash(project) if project.is_file() else None
    document = _read_trust_document()
    entry = document.get("workspaces", {}).get(workspace_identifier(root), {})
    trusted_hash = str(entry.get("config_hash")) if entry.get("config_hash") else None
    return ProjectConfigTrust(
        workspace=root,
        config_path=project,
        config_hash=digest,
        trusted_hash=trusted_hash,
        trusted=digest is not None and digest == trusted_hash,
    )


def trust_project_config(workspace: Path) -> ProjectConfigTrust:
    root = workspace.expanduser().resolve()
    project = root / ".graft" / "config.json"
    if not project.is_file():
        raise FileNotFoundError(f"No project GRAFT configuration exists: {project}")
    load_config(project)
    digest = _file_hash(project)
    document = _read_trust_document()
    workspaces = document.setdefault("workspaces", {})
    workspaces[workspace_identifier(root)] = {
        "workspace": str(root),
        "config_path": str(project),
        "config_hash": digest,
        "trusted_at": datetime.now(timezone.utc).isoformat(),
    }
    _write_trust_document(document)
    return project_config_trust(root)


def untrust_project_config(workspace: Path) -> ProjectConfigTrust:
    root = workspace.expanduser().resolve()
    document = _read_trust_document()
    workspaces = document.setdefault("workspaces", {})
    workspaces.pop(workspace_identifier(root), None)
    _write_trust_document(document)
    return project_config_trust(root)


def _trust_path() -> Path:
    return config_home() / "trusted-projects.json"


def _read_trust_document() -> dict[str, Any]:
    path = _trust_path()
    if not path.is_file():
        return {"version": 1, "workspaces": {}}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"Cannot parse GRAFT trust store {path}: {exc}") from exc
    if not isinstance(raw, dict) or int(raw.get("version", 0)) != 1:
        raise ValueError(f"Unsupported GRAFT trust store: {path}")
    if not isinstance(raw.get("workspaces", {}), dict):
        raise ValueError(f"Invalid GRAFT trust store workspaces object: {path}")
    raw.setdefault("workspaces", {})
    return raw


def _write_trust_document(document: dict[str, Any]) -> None:
    path = _trust_path()
    payload = json.dumps(document, ensure_ascii=False, indent=2) + "\n"
    _atomic_write(path, payload)


def _file_hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _profile_matches(workspace: Path, raw: Mapping[str, Any]) -> bool:
    match = raw.get("match", {})
    if not isinstance(match, Mapping):
        return False
    all_files = tuple(str(item) for item in match.get("files_all", []))
    any_files = tuple(str(item) for item in match.get("files_any", []))
    path_regex = match.get("path_regex")
    if all_files and not all((workspace / item).exists() for item in all_files):
        return False
    if any_files and not any((workspace / item).exists() for item in any_files):
        return False
    if path_regex is not None:
        try:
            if re.search(str(path_regex), str(workspace)) is None:
                return False
        except re.error:
            return False
    return bool(all_files or any_files or path_regex)


def _materialize_profile(profile_path: Path, config: Mapping[str, Any]) -> Path:
    target_dir = config_home() / "materialized"
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / f"{profile_path.stem}.json"
    payload = json.dumps(config, ensure_ascii=False, indent=2) + "\n"
    if not target.exists() or target.read_text(encoding="utf-8") != payload:
        _atomic_write(target, payload)
    return target


def _safe_git_config() -> Path:
    target = config_home() / "generated" / "safe-git-v1.json"
    payload = {
        "version": 1,
        "enabled": True,
        "budget": 0.25,
        "max_set_fpr": 0.0,
        "checkpoint_mode": "completion",
        "max_feedback_rounds": 2,
        "failure_policy": "open",
        "environment_fingerprint": "graft-global-safe-git-v1",
        "verifiers": [
            {
                "id": "git-diff-check",
                "kind": "command",
                "cost": 0.25,
                "blocking": True,
                "failure_modes": ["patch_whitespace_or_conflict_marker"],
                "timeout_s": 30,
                "command": ["git", "diff", "--check"],
                "failure_exit_codes": [1, 2],
                "lineage": {
                    "provider": "git",
                    "modality": ["source-diff"],
                    "oracle": "git-diff-check",
                },
            }
        ],
        "calibration": {
            "failure_scenarios": [
                {
                    "id": "git-diff-detectable-failure",
                    "weight": 1.0,
                    "detections": {"git-diff-check": 1.0},
                }
            ],
            "clean_scenarios": [
                {
                    "id": "git-clean-diff",
                    "weight": 1.0,
                    "false_alarms": {"git-diff-check": 0.0},
                }
            ],
        },
    }
    encoded = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
    if not target.exists() or target.read_text(encoding="utf-8") != encoded:
        target.parent.mkdir(parents=True, exist_ok=True)
        _atomic_write(target, encoded)
    return target


def _is_git_workspace(workspace: Path) -> bool:
    try:
        completed = subprocess.run(
            ["git", "-C", str(workspace), "rev-parse", "--is-inside-work-tree"],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return completed.returncode == 0 and completed.stdout.strip() == "true"


def _atomic_write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        "w", encoding="utf-8", dir=path.parent, delete=False
    ) as handle:
        handle.write(content)
        temporary = Path(handle.name)
    os.replace(temporary, path)
