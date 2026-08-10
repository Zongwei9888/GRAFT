from __future__ import annotations

import json
import os
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any


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
    force: bool = False,
) -> ProjectConfigResult:
    root = workspace.expanduser().resolve()
    target = root / ".graft" / "config.json"
    if target.exists() and not force:
        raise FileExistsError(
            f"GRAFT configuration already exists: {target}; use --force to replace it"
        )
    verifiers, warnings = discover_verifiers(root)
    ids = tuple(str(item["id"]) for item in verifiers)
    detections = [
        {
            "id": f"{verifier_id}-detectable-failure",
            "weight": 1.0,
            "detections": {
                candidate: 1.0 if candidate == verifier_id else 0.0
                for candidate in ids
            },
        }
        for verifier_id in ids
    ]
    false_alarms = {verifier_id: 0.0 for verifier_id in ids}
    payload: dict[str, Any] = {
        "version": 1,
        "enabled": bool(verifiers),
        "budget": sum(float(item["cost"]) for item in verifiers),
        "max_set_fpr": 0.0,
        "checkpoint_mode": checkpoint_mode,
        "max_feedback_rounds": 2,
        "failure_policy": "open",
        "environment_fingerprint": "graft-project-auto-v1",
        "verifiers": verifiers,
        "calibration": {
            "failure_scenarios": detections,
            "clean_scenarios": (
                [
                    {
                        "id": "configured-clean-checkpoint",
                        "weight": 1.0,
                        "false_alarms": false_alarms,
                    }
                ]
                if ids
                else []
            ),
        },
        "_note": (
            "Auto-discovered product configuration. Detection and false-alarm rows "
            "are operational fixtures, not paper calibration evidence."
        ),
    }
    target.parent.mkdir(parents=True, exist_ok=True)
    _atomic_json_write(target, payload)
    if not verifiers:
        warnings.append(
            "No safe verifier was discovered; GRAFT will observe this workspace without enforcement."
        )
    return ProjectConfigResult(target, True, ids, tuple(warnings))


def set_project_enabled(workspace: Path, enabled: bool) -> ProjectToggleResult:
    """Create or update the explicit project-level enablement override.

    A disabled project configuration intentionally shadows user profiles and the
    safe Git fallback. Enabling an unconfigured project performs the same safe
    discovery as ``graft init`` rather than inventing commands.
    """

    root = workspace.expanduser().resolve()
    target = root / ".graft" / "config.json"
    created = not target.exists()
    if created and enabled:
        initialized = initialize_project(root)
        raw = json.loads(initialized.path.read_text(encoding="utf-8"))
    elif created:
        raw = _disabled_config()
        target.parent.mkdir(parents=True, exist_ok=True)
    else:
        try:
            raw = json.loads(target.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise ValueError(f"Cannot parse existing GRAFT config {target}: {exc}") from exc
        if not isinstance(raw, dict):
            raise ValueError(f"GRAFT config must contain a JSON object: {target}")
        if int(raw.get("version", 0)) != 1:
            raise ValueError("Only GRAFT config version 1 can be enabled or disabled")
        if enabled and raw.get("environment_fingerprint") == "graft-project-disabled-v1":
            initialized = initialize_project(root, force=True)
            raw = json.loads(initialized.path.read_text(encoding="utf-8"))
    raw["enabled"] = enabled
    _atomic_json_write(target, raw)
    verifier_ids = tuple(
        str(item.get("id"))
        for item in raw.get("verifiers", [])
        if isinstance(item, dict) and item.get("id") is not None
    )
    return ProjectToggleResult(target, enabled, created, verifier_ids)


def _disabled_config() -> dict[str, Any]:
    return {
        "version": 1,
        "enabled": False,
        "budget": 0.0,
        "max_set_fpr": 0.0,
        "checkpoint_mode": "completion",
        "max_feedback_rounds": 2,
        "failure_policy": "open",
        "environment_fingerprint": "graft-project-disabled-v1",
        "verifiers": [],
        "calibration": {
            "failure_scenarios": [],
            "clean_scenarios": [],
        },
        "_note": "Explicit project-level off switch. Run `graft config enable` to re-enable.",
    }


def discover_verifiers(workspace: Path) -> tuple[list[dict[str, Any]], list[str]]:
    root = workspace.resolve()
    verifiers: list[dict[str, Any]] = []
    warnings: list[str] = []

    if _is_git_workspace(root):
        verifiers.append(
            _command_verifier(
                "git-diff-check",
                ["git", "diff", "--check"],
                cost=0.25,
                timeout=30,
                provider="git",
                oracle="git-diff-check",
                modality=["source-diff"],
                failure_modes=["patch_whitespace_or_conflict_marker"],
                failure_exit_codes=[1, 2],
            )
        )

    if _looks_like_python(root):
        compile_targets = [
            name for name in ("src", "tests") if (root / name).exists()
        ]
        if not compile_targets:
            compile_targets = [path.name for path in sorted(root.glob("*.py"))]
        if compile_targets:
            verifiers.append(
                _command_verifier(
                    "python-compile",
                    ["python3", "-m", "compileall", "-q", *compile_targets],
                    cost=0.5,
                    timeout=60,
                    provider="cpython",
                    oracle="python-parser",
                    modality=["source"],
                    failure_modes=["syntax_import_failure"],
                )
            )
        if (root / "scripts" / "run_tests.py").is_file():
            test_command = ["python3", "scripts/run_tests.py"]
        elif _has_python_tests(root):
            test_command = (
                ["python3", "-m", "pytest", "-q"]
                if _pytest_is_configured(root)
                else ["python3", "-m", "unittest", "discover", "-s", "tests", "-v"]
            )
        else:
            test_command = []
        if test_command:
            verifiers.append(
                _command_verifier(
                    "python-tests",
                    test_command,
                    cost=1.5,
                    timeout=180,
                    provider="python-project",
                    oracle="project-tests",
                    modality=["execution"],
                    failure_modes=[
                        "direct_requirement_failure",
                        "backward_compatibility_failure",
                    ],
                )
            )

    package_json = root / "package.json"
    if package_json.is_file():
        try:
            package = json.loads(package_json.read_text(encoding="utf-8"))
            scripts = package.get("scripts", {})
        except (OSError, json.JSONDecodeError):
            scripts = {}
            warnings.append("package.json could not be parsed; Node scripts were skipped.")
        if isinstance(scripts, dict):
            for name in ("test", "typecheck", "lint"):
                if name not in scripts:
                    continue
                verifiers.append(
                    _command_verifier(
                        f"npm-{name}",
                        ["npm", "run", "--silent", name],
                        cost=1.5 if name == "test" else 0.75,
                        timeout=240,
                        provider="npm-project",
                        oracle=f"package-script:{name}",
                        modality=["execution"],
                        failure_modes=[f"node_{name}_failure"],
                    )
                )

    if (root / "Cargo.toml").is_file():
        verifiers.extend(
            [
                _command_verifier(
                    "cargo-check",
                    ["cargo", "check", "--all-targets"],
                    cost=1.0,
                    timeout=300,
                    provider="cargo",
                    oracle="rust-compiler",
                    modality=["source", "build"],
                    failure_modes=["rust_build_or_type_failure"],
                ),
                _command_verifier(
                    "cargo-test",
                    ["cargo", "test", "--all-targets"],
                    cost=2.0,
                    timeout=600,
                    provider="cargo",
                    oracle="rust-tests",
                    modality=["execution"],
                    failure_modes=["rust_behavior_failure"],
                ),
            ]
        )

    if (root / "go.mod").is_file():
        verifiers.append(
            _command_verifier(
                "go-test",
                ["go", "test", "./..."],
                cost=1.5,
                timeout=300,
                provider="go",
                oracle="go-tests",
                modality=["source", "execution"],
                failure_modes=["go_build_or_behavior_failure"],
            )
        )

    return verifiers, warnings


def _command_verifier(
    verifier_id: str,
    command: list[str],
    *,
    cost: float,
    timeout: int,
    provider: str,
    oracle: str,
    modality: list[str],
    failure_modes: list[str],
    failure_exit_codes: list[int] | None = None,
) -> dict[str, Any]:
    return {
        "id": verifier_id,
        "kind": "command",
        "cost": cost,
        "blocking": True,
        "failure_modes": failure_modes,
        "timeout_s": timeout,
        "command": command,
        "failure_exit_codes": failure_exit_codes or [1],
        "lineage": {
            "provider": provider,
            "modality": modality,
            "oracle": oracle,
        },
    }


def _looks_like_python(root: Path) -> bool:
    markers = ("pyproject.toml", "setup.py", "setup.cfg", "requirements.txt")
    return any((root / marker).exists() for marker in markers) or any(
        root.glob("*.py")
    ) or (root / "src").is_dir()


def _has_python_tests(root: Path) -> bool:
    tests = root / "tests"
    return tests.is_dir() and any(tests.rglob("test*.py"))


def _pytest_is_configured(root: Path) -> bool:
    if (root / "pytest.ini").is_file() or (root / "conftest.py").is_file():
        return True
    for name in ("pyproject.toml", "setup.cfg", "requirements.txt"):
        path = root / name
        try:
            if path.is_file() and "pytest" in path.read_text(
                encoding="utf-8", errors="ignore"
            ).lower():
                return True
        except OSError:
            continue
    return False


def _is_git_workspace(root: Path) -> bool:
    try:
        completed = subprocess.run(
            ["git", "-C", str(root), "rev-parse", "--is-inside-work-tree"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return completed.returncode == 0


def _atomic_json_write(path: Path, payload: dict[str, Any]) -> None:
    with tempfile.NamedTemporaryFile(
        "w", encoding="utf-8", dir=path.parent, delete=False
    ) as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
        temporary = Path(handle.name)
    os.replace(temporary, path)
