from __future__ import annotations

import json
import os
import re
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from graft.registry import load_config
from graft.runtime_paths import config_home


_PROFILE_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")


@dataclass(frozen=True)
class ProfileResult:
    name: str
    path: Path
    valid: bool
    match: dict[str, Any]
    error: str | None = None


def create_profile(
    name: str,
    source_config: Path,
    *,
    files_all: tuple[str, ...] = (),
    files_any: tuple[str, ...] = (),
    path_regex: str | None = None,
    force: bool = False,
) -> ProfileResult:
    if not _PROFILE_NAME.fullmatch(name):
        raise ValueError(
            "Profile name must be 1-64 characters using letters, digits, '.', '_' or '-'"
        )
    if not (files_all or files_any or path_regex):
        raise ValueError(
            "A profile needs at least one matcher: --files-all, --files-any, or --path-regex"
        )
    if path_regex is not None:
        re.compile(path_regex)

    source = source_config.expanduser().resolve()
    load_config(source)
    raw = json.loads(source.read_text(encoding="utf-8"))
    match: dict[str, Any] = {}
    if files_all:
        match["files_all"] = list(dict.fromkeys(files_all))
    if files_any:
        match["files_any"] = list(dict.fromkeys(files_any))
    if path_regex:
        match["path_regex"] = path_regex

    target = config_home() / "profiles" / f"{name}.json"
    if target.exists() and not force:
        raise FileExistsError(
            f"GRAFT profile already exists: {target}; use --force to replace it"
        )
    payload = {
        "version": 1,
        "match": match,
        "config": raw,
    }
    target.parent.mkdir(parents=True, exist_ok=True)
    _atomic_json_write(target, payload)
    return ProfileResult(name, target, True, match)


def list_profiles() -> tuple[ProfileResult, ...]:
    directory = config_home() / "profiles"
    if not directory.is_dir():
        return ()
    results: list[ProfileResult] = []
    for path in sorted(directory.glob("*.json")):
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(raw, dict):
                raise ValueError("profile is not a JSON object")
            match = raw.get("match", {})
            config = raw.get("config")
            if not isinstance(match, dict) or not isinstance(config, dict):
                raise ValueError("profile requires object-valued match and config fields")
            with tempfile.TemporaryDirectory(prefix="graft-profile-check-") as directory_name:
                candidate = Path(directory_name) / "config.json"
                candidate.write_text(
                    json.dumps(config, ensure_ascii=False), encoding="utf-8"
                )
                load_config(candidate)
            results.append(ProfileResult(path.stem, path, True, dict(match)))
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            results.append(ProfileResult(path.stem, path, False, {}, str(exc)))
    return tuple(results)


def _atomic_json_write(path: Path, payload: dict[str, Any]) -> None:
    with tempfile.NamedTemporaryFile(
        "w", encoding="utf-8", dir=path.parent, delete=False
    ) as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
        temporary = Path(handle.name)
    os.replace(temporary, path)
