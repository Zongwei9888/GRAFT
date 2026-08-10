from __future__ import annotations

import hashlib
import json
import os
import time
from pathlib import Path
from typing import Any


def claim_event(events_dir: Path, event_name: str, event: dict[str, Any]) -> bool:
    """Atomically claim an event so project and global hooks cannot run it twice."""

    identity = {
        "event": event_name,
        "session_id": event.get("session_id"),
        "turn_id": event.get("turn_id"),
        "tool_use_id": event.get("tool_use_id"),
        "stop_hook_active": event.get("stop_hook_active"),
        "prompt": event.get("prompt") if event_name == "UserPromptSubmit" else None,
    }
    encoded = json.dumps(
        identity, ensure_ascii=False, sort_keys=True, default=str
    ).encode("utf-8")
    digest = hashlib.sha256(encoded).hexdigest()
    events_dir.mkdir(parents=True, exist_ok=True)
    marker = events_dir / digest
    try:
        descriptor = os.open(marker, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError:
        return False
    with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
        json.dump(identity, handle, ensure_ascii=False, sort_keys=True)
        handle.write("\n")
    _prune_old_markers(events_dir)
    return True


def _prune_old_markers(events_dir: Path, *, max_age_s: float = 7 * 24 * 3600) -> None:
    cutoff = time.time() - max_age_s
    try:
        entries = tuple(events_dir.iterdir())
    except OSError:
        return
    for path in entries:
        try:
            if path.is_file() and path.stat().st_mtime < cutoff:
                path.unlink()
        except OSError:
            continue
