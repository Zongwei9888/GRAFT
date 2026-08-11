from __future__ import annotations

import copy
import json
import os
import shlex
import shutil
import subprocess
import sys
import tempfile
import time
import venv
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from graft.runtime_paths import install_home, user_bin_home


INSTALLATION_ID = "graft-global-v1"
DEFAULT_DESCRIPTION = "User-level Codex lifecycle hooks, including GRAFT."


@dataclass(frozen=True)
class HookInstallResult:
    hooks_path: Path
    runtime_command: Path
    backup_path: Path | None
    installed_handlers: int


@dataclass(frozen=True)
class HookUninstallResult:
    hooks_path: Path
    backup_path: Path | None
    removed_handlers: int
    removed_file: bool


def default_codex_home() -> Path:
    override = os.environ.get("CODEX_HOME")
    if override:
        return Path(override).expanduser().resolve()
    return (Path.home() / ".codex").resolve()


def installed_hook_command() -> Path | None:
    """Return the hook entry point beside the active Python, if installed.

    This is the normal path for pipx/uv/pip installations. Source checkouts fall
    back to the managed-runtime provisioning flow.
    """

    candidate = _venv_bin(Path(sys.prefix), "graft-hook")
    if candidate.is_file():
        return candidate.resolve()
    discovered = shutil.which("graft-hook")
    return Path(discovered).resolve() if discovered else None


def installed_cli_command() -> Path | None:
    candidate = _venv_bin(Path(sys.prefix), "graft")
    if candidate.is_file():
        return candidate.resolve()
    discovered = shutil.which("graft")
    if discovered:
        return Path(discovered).resolve()
    managed = user_bin_home() / ("graft.exe" if sys.platform == "win32" else "graft")
    return managed.resolve() if managed.is_file() else None


def provision_runtime(*, source_root: Path | None = None) -> Path:
    root = (source_root or _find_source_root()).resolve()
    if not (root / "pyproject.toml").is_file():
        raise RuntimeError(f"Cannot install GRAFT runtime from {root}")
    runtime_dir = install_home() / "runtime-venv"
    python = _venv_python(runtime_dir)
    if not python.exists():
        runtime_dir.parent.mkdir(parents=True, exist_ok=True)
        venv.EnvBuilder(with_pip=True, clear=False).create(runtime_dir)
    with tempfile.TemporaryDirectory(prefix="graft-install-") as directory:
        staged_source = Path(directory) / "source"
        _stage_install_source(root, staged_source)
        completed = subprocess.run(
            [
                str(python),
                "-m",
                "pip",
                "install",
                "--upgrade",
                "--no-deps",
                str(staged_source),
            ],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=300,
            check=False,
        )
    if completed.returncode != 0:
        detail = completed.stderr.strip() or completed.stdout.strip()
        raise RuntimeError(f"Failed to install managed GRAFT runtime: {detail}")
    hook = _venv_bin(runtime_dir, "graft-hook")
    if not hook.is_file():
        raise RuntimeError(f"Managed GRAFT hook executable was not installed: {hook}")
    graft_cli = _venv_bin(runtime_dir, "graft")
    if not graft_cli.is_file():
        raise RuntimeError(f"Managed GRAFT CLI executable was not installed: {graft_cli}")
    _install_cli_link(graft_cli, "graft")
    _install_cli_link(hook, "graft-hook")
    return hook.resolve()


def _stage_install_source(root: Path, destination: Path) -> None:
    """Copy only packaging inputs, keeping build artifacts out of the checkout."""

    destination.mkdir(parents=True)
    shutil.copy2(root / "pyproject.toml", destination / "pyproject.toml")
    readme = root / "README.md"
    if readme.is_file():
        shutil.copy2(readme, destination / "README.md")
    shutil.copytree(
        root / "src",
        destination / "src",
        ignore=shutil.ignore_patterns(
            "__pycache__", "*.pyc", "*.pyo", "*.egg-info"
        ),
    )


def install_global_hooks(
    runtime_command: Path,
    *,
    codex_home: Path | None = None,
) -> HookInstallResult:
    executable = runtime_command.expanduser().resolve()
    if not executable.is_file():
        raise FileNotFoundError(f"GRAFT hook executable does not exist: {executable}")
    home = (codex_home or default_codex_home()).expanduser().resolve()
    hooks_path = home / "hooks.json"
    document = _read_hooks_document(hooks_path)
    previous_document = copy.deepcopy(document)
    _remove_graft_handlers(document)
    hooks = document.setdefault("hooks", {})
    if not isinstance(hooks, dict):
        raise ValueError(f"Invalid hooks object in {hooks_path}")
    hooks.setdefault("UserPromptSubmit", []).append(
        {"hooks": [_handler(executable, "user-prompt", timeout=10)]}
    )
    hooks.setdefault("PostToolUse", []).append(
        {
            "matcher": "Bash|apply_patch|Edit|Write",
            "hooks": [_handler(executable, "post-tool", timeout=15)],
        }
    )
    hooks.setdefault("Stop", []).append(
        {
            "hooks": [
                _handler(
                    executable,
                    "stop",
                    timeout=600,
                    status_message="GRAFT is verifying this checkpoint",
                )
            ]
        }
    )
    document.setdefault("description", DEFAULT_DESCRIPTION)
    if hooks_path.is_file() and document == previous_document:
        return HookInstallResult(hooks_path, executable, None, 3)
    backup = _backup(hooks_path)
    _atomic_json_write(hooks_path, document)
    return HookInstallResult(hooks_path, executable, backup, 3)


def uninstall_global_hooks(
    *, codex_home: Path | None = None
) -> HookUninstallResult:
    home = (codex_home or default_codex_home()).expanduser().resolve()
    hooks_path = home / "hooks.json"
    if not hooks_path.exists():
        return HookUninstallResult(hooks_path, None, 0, False)
    document = _read_hooks_document(hooks_path)
    removed = _remove_graft_handlers(document)
    if removed == 0:
        return HookUninstallResult(hooks_path, None, 0, False)
    backup = _backup(hooks_path)
    hooks = document.get("hooks", {})
    if not hooks and document.get("description") == DEFAULT_DESCRIPTION:
        hooks_path.unlink()
        return HookUninstallResult(hooks_path, backup, removed, True)
    _atomic_json_write(hooks_path, document)
    return HookUninstallResult(hooks_path, backup, removed, False)


def inspect_global_hooks(*, codex_home: Path | None = None) -> dict[str, Any]:
    home = (codex_home or default_codex_home()).expanduser().resolve()
    hooks_path = home / "hooks.json"
    cli = installed_cli_command()
    result: dict[str, Any] = {
        "codex_home": str(home),
        "hooks_path": str(hooks_path),
        "hooks_file_exists": hooks_path.is_file(),
        "graft_handlers": 0,
        "runtime_commands": [],
        "runtime_commands_exist": False,
        "cli_command": str(cli) if cli else None,
        "cli_command_exists": cli is not None,
        "codex_version": None,
        "hook_trust": "inspect with /hooks; Codex trust hashes are intentionally not reimplemented",
    }
    try:
        completed = subprocess.run(
            ["codex", "--version"],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=10,
            check=False,
        )
        if completed.returncode == 0:
            result["codex_version"] = completed.stdout.strip()
    except (OSError, subprocess.TimeoutExpired):
        pass
    if not hooks_path.is_file():
        return result
    document = _read_hooks_document(hooks_path)
    commands: list[str] = []
    for handler in _iter_handlers(document):
        command = str(handler.get("command", ""))
        if INSTALLATION_ID in command:
            commands.append(command)
    executables = [_command_executable(command) for command in commands]
    result["graft_handlers"] = len(commands)
    result["runtime_commands"] = commands
    result["runtime_commands_exist"] = bool(executables) and all(
        path is not None and path.is_file() for path in executables
    )
    return result


def _install_cli_link(target: Path, name: str) -> Path:
    """Expose a managed entry point without replacing an unrelated user command."""

    directory = user_bin_home()
    directory.mkdir(parents=True, exist_ok=True)
    link = directory / name
    if link.is_symlink():
        existing_target = link.resolve(strict=False)
        if existing_target == target.resolve():
            return link
        managed_root = install_home().resolve()
        if managed_root not in existing_target.parents:
            raise FileExistsError(
                f"Refusing to replace unrelated symbolic link: {link} -> {existing_target}"
            )
        link.unlink()
    elif link.exists():
        try:
            if link.samefile(target):
                return link
        except OSError:
            pass
        raise FileExistsError(f"Refusing to replace unrelated command: {link}")
    link.symlink_to(target.resolve())
    return link


def _handler(
    executable: Path,
    event: str,
    *,
    timeout: int,
    status_message: str | None = None,
) -> dict[str, Any]:
    command = " ".join(
        shlex.quote(item)
        for item in (
            str(executable),
            event,
            "--installation-id",
            INSTALLATION_ID,
        )
    )
    handler: dict[str, Any] = {
        "type": "command",
        "command": command,
        "commandWindows": subprocess.list2cmdline(
            [str(executable), event, "--installation-id", INSTALLATION_ID]
        ),
        "timeout": timeout,
    }
    if status_message:
        handler["statusMessage"] = status_message
    return handler


def _read_hooks_document(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"description": DEFAULT_DESCRIPTION, "hooks": {}}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"Cannot parse existing Codex hooks file {path}: {exc}") from exc
    if not isinstance(raw, dict):
        raise ValueError(f"Codex hooks file must contain a JSON object: {path}")
    return raw


def _iter_handlers(document: dict[str, Any]):
    hooks = document.get("hooks", {})
    if not isinstance(hooks, dict):
        return
    for groups in hooks.values():
        if not isinstance(groups, list):
            continue
        for group in groups:
            if not isinstance(group, dict):
                continue
            handlers = group.get("hooks", [])
            if not isinstance(handlers, list):
                continue
            for handler in handlers:
                if isinstance(handler, dict):
                    yield handler


def _remove_graft_handlers(document: dict[str, Any]) -> int:
    hooks = document.get("hooks", {})
    if not isinstance(hooks, dict):
        return 0
    removed = 0
    for event_name in tuple(hooks):
        groups = hooks.get(event_name)
        if not isinstance(groups, list):
            continue
        retained_groups = []
        for group in groups:
            if not isinstance(group, dict):
                retained_groups.append(group)
                continue
            handlers = group.get("hooks")
            if not isinstance(handlers, list):
                retained_groups.append(group)
                continue
            retained_handlers = []
            for handler in handlers:
                if isinstance(handler, dict) and INSTALLATION_ID in str(
                    handler.get("command", "")
                ):
                    removed += 1
                else:
                    retained_handlers.append(handler)
            if retained_handlers:
                updated = dict(group)
                updated["hooks"] = retained_handlers
                retained_groups.append(updated)
        if retained_groups:
            hooks[event_name] = retained_groups
        else:
            hooks.pop(event_name, None)
    return removed


def _backup(path: Path) -> Path | None:
    if not path.exists():
        return None
    timestamp = time.strftime("%Y%m%d-%H%M%S")
    candidate = path.with_name(f"{path.name}.graft-backup-{timestamp}")
    counter = 1
    while candidate.exists():
        candidate = path.with_name(
            f"{path.name}.graft-backup-{timestamp}-{counter:02d}"
        )
        counter += 1
    shutil.copy2(path, candidate)
    return candidate


def _atomic_json_write(path: Path, document: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        "w", encoding="utf-8", dir=path.parent, delete=False
    ) as handle:
        json.dump(document, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
        temporary = Path(handle.name)
    os.replace(temporary, path)


def _command_executable(command: str) -> Path | None:
    try:
        parts = shlex.split(command)
    except ValueError:
        return None
    return Path(parts[0]).expanduser() if parts else None


def _find_source_root() -> Path:
    for parent in Path(__file__).resolve().parents:
        if (parent / "pyproject.toml").is_file() and (
            parent / "src" / "graft"
        ).is_dir():
            return parent
    raise RuntimeError(
        "Could not locate a GRAFT source checkout; pass an explicit source root"
    )


def _venv_python(runtime_dir: Path) -> Path:
    return _venv_bin(runtime_dir, "python")


def _venv_bin(runtime_dir: Path, name: str) -> Path:
    if sys.platform == "win32":
        suffix = ".exe" if name in {"python", "graft-hook", "graft"} else ""
        return runtime_dir / "Scripts" / f"{name}{suffix}"
    return runtime_dir / "bin" / name
