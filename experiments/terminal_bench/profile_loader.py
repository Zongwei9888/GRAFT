from __future__ import annotations

import json
import re
from pathlib import Path


PROFILE_NAME = re.compile(r"^[a-z0-9][a-z0-9-]*$")
PROFILE_ROOT = Path(__file__).resolve().parent / "profiles"


def load_public_profile(name: str) -> tuple[str, dict[str, str]]:
    """Load a public-contract GRAFT profile and its verifier assets.

    Profiles live in the experiment source tree, not in benchmark task tests.
    Keeping the two separate makes accidental hidden-verifier leakage visible.
    """

    if PROFILE_NAME.fullmatch(name) is None:
        raise ValueError(f"Invalid GRAFT experiment profile name: {name!r}")
    root = PROFILE_ROOT / name
    config_path = root / "config.json"
    if not config_path.is_file():
        raise ValueError(f"Unknown GRAFT experiment profile: {name}")
    raw = json.loads(config_path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError(f"Profile config must be a JSON object: {config_path}")
    config = json.dumps(raw, ensure_ascii=False, indent=2) + "\n"
    assets = {
        path.name: path.read_text(encoding="utf-8")
        for path in sorted(root.glob("*.py"))
        if path.is_file()
    }
    if not assets:
        raise ValueError(f"Profile contains no public verifier assets: {root}")
    return config, assets


def build_external_profile(name: str, config: str) -> str:
    """Wrap a verifier config as a centrally stored GRAFT user profile.

    Benchmark treatments must not place their control-plane configuration in
    the task workspace.  Besides changing the workspace snapshot, a local
    ``.graft/config.json`` advertises verifier commands to the coding agent and
    confounds Stop-boundary evaluation.  The plugin already supports matching
    user profiles from ``GRAFT_CONFIG_HOME``; this helper materializes the
    checked-in match rule without exposing it under ``/app``.
    """

    if PROFILE_NAME.fullmatch(name) is None:
        raise ValueError(f"Invalid GRAFT experiment profile name: {name!r}")
    root = PROFILE_ROOT / name
    match_path = root / "match.json"
    if not match_path.is_file():
        raise ValueError(f"Profile contains no external match rule: {root}")
    match = json.loads(match_path.read_text(encoding="utf-8"))
    parsed_config = json.loads(config)
    if not isinstance(match, dict) or not isinstance(parsed_config, dict):
        raise ValueError(f"Profile match and config must be JSON objects: {root}")
    document = {"match": match, "config": parsed_config}
    return json.dumps(document, ensure_ascii=False, indent=2) + "\n"
