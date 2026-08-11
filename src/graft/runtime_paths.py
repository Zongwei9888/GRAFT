from __future__ import annotations

import hashlib
import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class WorkspaceRuntimePaths:
    workspace: Path
    workspace_id: str
    data_home: Path
    workspace_data: Path
    state_dir: Path
    telemetry_dir: Path
    reports_dir: Path
    events_dir: Path
    baselines_dir: Path


def resolve_workspace(cwd: Path) -> Path:
    """Resolve a stable workspace root without requiring the directory to use Git."""

    resolved = cwd.expanduser().resolve()
    if not resolved.is_dir():
        raise ValueError(f"Workspace is not a directory: {resolved}")
    try:
        completed = subprocess.run(
            ["git", "-C", str(resolved), "rev-parse", "--show-toplevel"],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return resolved
    if completed.returncode != 0:
        return resolved
    candidate = Path(completed.stdout.strip()).expanduser().resolve()
    return candidate if candidate.is_dir() else resolved


def workspace_identifier(workspace: Path) -> str:
    encoded = str(workspace.expanduser().resolve()).encode(
        "utf-8", errors="surrogatepass"
    )
    return hashlib.sha256(encoded).hexdigest()[:24]


def config_home() -> Path:
    override = os.environ.get("GRAFT_CONFIG_HOME")
    if override:
        return Path(override).expanduser().resolve()
    xdg = os.environ.get("XDG_CONFIG_HOME")
    if xdg:
        return (Path(xdg).expanduser() / "graft").resolve()
    if sys.platform == "win32":
        base = os.environ.get("APPDATA")
        if base:
            return (Path(base).expanduser() / "GRAFT").resolve()
    return (Path.home() / ".config" / "graft").resolve()


def data_home() -> Path:
    override = os.environ.get("GRAFT_STATE_HOME")
    if override:
        return Path(override).expanduser().resolve()
    if sys.platform == "darwin":
        return (Path.home() / "Library" / "Application Support" / "GRAFT").resolve()
    if sys.platform == "win32":
        base = os.environ.get("LOCALAPPDATA") or os.environ.get("APPDATA")
        if base:
            return (Path(base).expanduser() / "GRAFT").resolve()
    xdg = os.environ.get("XDG_STATE_HOME")
    if xdg:
        return (Path(xdg).expanduser() / "graft").resolve()
    return (Path.home() / ".local" / "state" / "graft").resolve()


def install_home() -> Path:
    override = os.environ.get("GRAFT_INSTALL_HOME")
    if override:
        return Path(override).expanduser().resolve()
    return (Path.home() / ".local" / "share" / "graft").resolve()


def user_bin_home() -> Path:
    override = os.environ.get("GRAFT_BIN_HOME")
    if override:
        return Path(override).expanduser().resolve()
    if sys.platform == "win32":
        return install_home() / "bin"
    return (Path.home() / ".local" / "bin").resolve()


def workspace_runtime_paths(workspace: Path) -> WorkspaceRuntimePaths:
    root = workspace.expanduser().resolve()
    identifier = workspace_identifier(root)
    home = data_home()
    workspace_data = home / "workspaces" / identifier
    return WorkspaceRuntimePaths(
        workspace=root,
        workspace_id=identifier,
        data_home=home,
        workspace_data=workspace_data,
        state_dir=workspace_data / "sessions",
        telemetry_dir=workspace_data / "telemetry",
        reports_dir=workspace_data / "reports",
        events_dir=workspace_data / "events",
        baselines_dir=workspace_data / "baselines",
    )
