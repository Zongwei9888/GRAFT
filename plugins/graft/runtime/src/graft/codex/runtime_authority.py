from __future__ import annotations

import hashlib
import importlib.metadata
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import tomllib
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from graft import __version__


RUNTIME_PROTOCOL_VERSION = 2
STATE_SCHEMA_VERSION = 2
AUTHORITY_ENV = "GRAFT_RUNTIME_AUTHORITY"


@dataclass(frozen=True)
class RuntimeIdentity:
    installation_id: str
    distribution: str
    package_version: str
    protocol_version: int
    runtime_digest: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class RuntimeAuthorityDecision:
    identity: RuntimeIdentity
    authority_id: str
    source: str

    @property
    def authoritative(self) -> bool:
        return self.identity.installation_id == self.authority_id


def current_runtime_identity(installation_id: str) -> RuntimeIdentity:
    normalized = installation_id.strip() or "manual"
    package_root = Path(__file__).resolve().parents[1]
    digest = hashlib.sha256()
    for relative in ("__init__.py", "codex/hooks.py", "codex/runtime_authority.py"):
        path = package_root / relative
        try:
            digest.update(relative.encode("utf-8"))
            digest.update(path.read_bytes())
        except OSError:
            digest.update(f"missing:{relative}".encode("utf-8"))
    return RuntimeIdentity(
        installation_id=normalized,
        distribution=_distribution(normalized),
        package_version=_current_package_version(),
        protocol_version=RUNTIME_PROTOCOL_VERSION,
        runtime_digest=digest.hexdigest(),
    )


def resolve_runtime_authority(
    workspace: Path,
    identity: RuntimeIdentity,
    *,
    codex_home: Path | None = None,
) -> RuntimeAuthorityDecision:
    """Choose one hook runtime without relying on process scheduling.

    An explicit environment pin is the reproducible experiment path. Product mode uses a
    deterministic source order: a repository development hook, then the installed plugin,
    then compatibility user hooks. Every compatible runtime computes the same result.
    """

    pinned = os.environ.get(AUTHORITY_ENV, "").strip()
    if pinned:
        return RuntimeAuthorityDecision(identity, pinned, "environment")
    selected = _discover_authority_fast(workspace, codex_home=codex_home)
    if selected:
        return RuntimeAuthorityDecision(identity, selected, "local-config")
    return RuntimeAuthorityDecision(identity, identity.installation_id, "sole-invocation")


def inspect_runtime_sources(
    workspace: Path,
    *,
    codex_home: Path | None = None,
) -> dict[str, Any]:
    root = workspace.expanduser().resolve()
    home = (codex_home or _default_codex_home()).expanduser().resolve()
    sources: list[dict[str, Any]] = []

    repo_hooks = root / ".codex" / "hooks.json"
    sources.extend(_hook_file_sources(repo_hooks, "repo"))
    global_hooks = home / "hooks.json"
    sources.extend(_hook_file_sources(global_hooks, "global"))
    sources.extend(_plugin_sources())

    deduplicated: dict[tuple[str, str, str], dict[str, Any]] = {}
    for source in sources:
        key = (
            str(source.get("source")),
            str(source.get("installation_id")),
            str(source.get("path")),
        )
        deduplicated[key] = source
    sources = list(deduplicated.values())
    sources.sort(key=_source_sort_key)

    pinned = os.environ.get(AUTHORITY_ENV, "").strip() or None
    selected = pinned or _select_discovered_authority(sources)
    selected_sources = [
        item for item in sources if item.get("installation_id") == selected
    ]
    incompatible = [item for item in sources if not item.get("compatible", False)]
    # Protocol-v2 state remains isolated, but an incompatible hook can still
    # emit its own Codex decision. Experiments therefore fail the doctor gate
    # until every enabled GRAFT source is protocol-compatible.
    healthy = bool(selected_sources) and not incompatible
    warnings: list[str] = []
    if incompatible:
        warnings.append(
            "Incompatible or legacy GRAFT hook sources are enabled; remove or upgrade them."
        )
    if pinned and not selected_sources:
        warnings.append(f"Pinned authority {pinned!r} is not an enabled hook source.")
    if len(sources) > 1:
        warnings.append(
            f"{len(sources)} GRAFT hook sources are enabled; only {selected!r} may mutate state."
        )
    if not sources:
        warnings.append("No enabled GRAFT hook source was discovered.")

    return {
        "protocol_version": RUNTIME_PROTOCOL_VERSION,
        "state_schema_version": STATE_SCHEMA_VERSION,
        "authority_pin": pinned,
        "selected_authority": selected,
        "healthy": healthy,
        "sources": sources,
        "warnings": warnings,
    }


def _hook_file_sources(path: Path, source: str) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return [
            {
                "source": source,
                "installation_id": f"graft-{source}-unknown",
                "path": str(path),
                "version": None,
                "protocol_version": None,
                "compatible": False,
                "reason": "hooks file is unreadable",
            }
        ]
    commands = _commands_from_hooks(document)
    results_by_id: dict[str, dict[str, Any]] = {}
    for command in commands:
        if not _looks_like_graft(command, path, source):
            continue
        installation_id = _installation_id(command) or f"graft-{source}-legacy"
        version = __version__ if source == "repo" else _probe_global_version(command)
        protocol = (
            RUNTIME_PROTOCOL_VERSION
            if source == "repo" or _version_at_least(version, (0, 5, 0))
            else None
        )
        record = {
            "source": source,
            "installation_id": installation_id,
            "path": str(path),
            "command": command,
            "version": version,
            "protocol_version": protocol,
            "compatible": protocol == RUNTIME_PROTOCOL_VERSION,
            "reason": (
                None
                if protocol == RUNTIME_PROTOCOL_VERSION
                else "runtime predates the authority/state protocol"
            ),
            "handlers": 1,
        }
        previous = results_by_id.get(installation_id)
        if previous is None:
            results_by_id[installation_id] = record
        else:
            previous["handlers"] = int(previous.get("handlers", 1)) + 1
    return list(results_by_id.values())


def _plugin_sources() -> list[dict[str, Any]]:
    try:
        completed = subprocess.run(
            ["codex", "plugin", "list", "--json"],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return []
    if completed.returncode != 0:
        return []
    try:
        payload = json.loads(completed.stdout)
    except json.JSONDecodeError:
        return []
    installed = payload.get("installed", []) if isinstance(payload, dict) else []
    results: list[dict[str, Any]] = []
    for item in installed:
        if not isinstance(item, dict):
            continue
        if item.get("name") != "graft" or not item.get("installed") or not item.get("enabled"):
            continue
        version = str(item.get("version", "")).split("+", 1)[0] or None
        source = item.get("source") if isinstance(item.get("source"), dict) else {}
        compatible = _version_at_least(version, (0, 5, 0))
        results.append(
            {
                "source": "plugin",
                "installation_id": "graft-plugin-v1",
                "path": str(source.get("path", "")),
                "plugin_id": item.get("pluginId"),
                "version": version,
                "protocol_version": RUNTIME_PROTOCOL_VERSION if compatible else None,
                "compatible": compatible,
                "reason": None if compatible else "plugin predates the authority/state protocol",
            }
        )
    return results


def _current_package_version() -> str:
    try:
        return importlib.metadata.version("codex-graft")
    except importlib.metadata.PackageNotFoundError:
        return __version__


def _discover_authority_fast(
    workspace: Path,
    *,
    codex_home: Path | None = None,
) -> str | None:
    root = workspace.expanduser().resolve()
    repo_commands = _read_hook_commands(root / ".codex" / "hooks.json")
    repo_ids = [
        _installation_id(command)
        for command in repo_commands
        if _looks_like_graft(command, root / ".codex" / "hooks.json", "repo")
    ]
    repo_ids = [item for item in repo_ids if item]
    if repo_ids:
        return sorted(repo_ids)[0]
    home = (codex_home or _default_codex_home()).expanduser().resolve()
    plugin_id = _plugin_authority_from_config(home / "config.toml")
    if plugin_id:
        return plugin_id
    global_commands = _read_hook_commands(home / "hooks.json")
    global_ids = [
        _installation_id(command)
        for command in global_commands
        if _looks_like_graft(command, home / "hooks.json", "global")
    ]
    global_ids = [item for item in global_ids if item]
    return sorted(global_ids)[0] if global_ids else None


def _read_hook_commands(path: Path) -> list[str]:
    try:
        return _commands_from_hooks(json.loads(path.read_text(encoding="utf-8")))
    except (OSError, json.JSONDecodeError):
        return []


def _plugin_authority_from_config(config_path: Path) -> str | None:
    try:
        document = tomllib.loads(config_path.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError):
        return None
    plugins = document.get("plugins", {})
    if not isinstance(plugins, dict):
        return None
    enabled = sorted(
        name
        for name, settings in plugins.items()
        if name.startswith("graft@")
        and isinstance(settings, dict)
        and settings.get("enabled") is True
    )
    return "graft-plugin-v1" if enabled else None


def _commands_from_hooks(document: Any) -> list[str]:
    if not isinstance(document, dict):
        return []
    hooks = document.get("hooks", {})
    if not isinstance(hooks, dict):
        return []
    commands: list[str] = []
    for groups in hooks.values():
        if not isinstance(groups, list):
            continue
        for group in groups:
            handlers = group.get("hooks", []) if isinstance(group, dict) else []
            if not isinstance(handlers, list):
                continue
            for handler in handlers:
                if isinstance(handler, dict) and isinstance(handler.get("command"), str):
                    commands.append(handler["command"])
    return commands


def _looks_like_graft(command: str, hooks_path: Path, source: str) -> bool:
    lowered = command.lower()
    if "--installation-id" in lowered and "graft-" in lowered:
        return True
    if "graft-hook" in lowered or "graft_plugin.py" in lowered:
        return True
    return source == "repo" and ".codex/hooks/" in lowered


def _installation_id(command: str) -> str | None:
    match = re.search(r"--installation-id(?:=|\s+)([^\s\"']+)", command)
    return match.group(1) if match else None


def _probe_global_version(command: str) -> str | None:
    try:
        arguments = shlex.split(command)
    except ValueError:
        return None
    if not arguments:
        return None
    executable = Path(arguments[0]).expanduser()
    if not executable.is_absolute():
        discovered = shutil.which(arguments[0])
        if not discovered:
            return None
        executable = Path(discovered)
    if executable.name.startswith("graft-hook"):
        cli_name = "graft.exe" if sys.platform == "win32" else "graft"
        cli = executable.with_name(cli_name)
    else:
        return None
    if not cli.is_file():
        return None
    python = executable.with_name("python.exe" if sys.platform == "win32" else "python")
    if python.is_file():
        try:
            completed = subprocess.run(
                [
                    str(python),
                    "-I",
                    "-c",
                    "import graft; print(graft.__version__)",
                ],
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                timeout=5,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired):
            completed = None
        if completed is not None and completed.returncode == 0:
            match = re.search(r"(\d+\.\d+\.\d+)", completed.stdout)
            if match:
                return match.group(1)
    try:
        completed = subprocess.run(
            [str(cli), "--version"],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if completed.returncode != 0:
        return None
    match = re.search(r"(\d+\.\d+\.\d+)", completed.stdout)
    return match.group(1) if match else None


def _version_at_least(value: str | None, expected: tuple[int, int, int]) -> bool:
    if not value:
        return False
    match = re.match(r"^(\d+)\.(\d+)\.(\d+)", value)
    if not match:
        return False
    return tuple(int(item) for item in match.groups()) >= expected


def _select_discovered_authority(sources: list[dict[str, Any]]) -> str | None:
    compatible = [item for item in sources if item.get("compatible")]
    if not compatible:
        return None
    return str(min(compatible, key=_source_sort_key)["installation_id"])


def _source_sort_key(source: dict[str, Any]) -> tuple[int, str, str]:
    priority = {"repo": 0, "plugin": 1, "global": 2}.get(str(source.get("source")), 9)
    return priority, str(source.get("installation_id")), str(source.get("path"))


def _distribution(installation_id: str) -> str:
    for name in ("plugin", "repo", "global", "sdk"):
        if name in installation_id:
            return name
    return "manual"


def _default_codex_home() -> Path:
    override = os.environ.get("CODEX_HOME")
    return Path(override) if override else Path.home() / ".codex"
