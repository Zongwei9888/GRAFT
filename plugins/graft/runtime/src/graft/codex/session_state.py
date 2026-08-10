from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from graft.runtime_paths import workspace_runtime_paths


def prompt_hash(prompt: str) -> str:
    return hashlib.sha256(prompt.encode("utf-8")).hexdigest()


@dataclass
class PromptRecord:
    text: str
    digest: str
    origin: str


@dataclass
class SessionState:
    session_id: str
    task_epoch: int = 1
    prompts: list[PromptRecord] = field(default_factory=list)
    baseline_tree_hash: str | None = None
    last_verified_checkpoint_key: str | None = None
    last_blocked_tree_hash: str | None = None
    last_feedback_hash: str | None = None
    pending_feedback_hash: str | None = None
    verification_round: int = 0
    status: str = "active"

    @property
    def requirements(self) -> tuple[str, ...]:
        return tuple(item.text for item in self.prompts if item.origin == "user")


class SessionStateStore:
    def __init__(self, workspace: Path, *, root: Path | None = None) -> None:
        resolved = workspace.resolve()
        self.root = root or workspace_runtime_paths(resolved).state_dir

    def load(self, session_id: str) -> SessionState:
        path = self._path(session_id)
        if not path.exists():
            return SessionState(session_id=session_id)
        raw = json.loads(path.read_text(encoding="utf-8"))
        return SessionState(
            session_id=str(raw["session_id"]),
            task_epoch=int(raw.get("task_epoch", 1)),
            prompts=[PromptRecord(**item) for item in raw.get("prompts", [])],
            baseline_tree_hash=raw.get("baseline_tree_hash"),
            last_verified_checkpoint_key=raw.get("last_verified_checkpoint_key"),
            last_blocked_tree_hash=raw.get("last_blocked_tree_hash"),
            last_feedback_hash=raw.get("last_feedback_hash"),
            pending_feedback_hash=raw.get("pending_feedback_hash"),
            verification_round=int(raw.get("verification_round", 0)),
            status=str(raw.get("status", "active")),
        )

    def save(self, state: SessionState) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        payload = asdict(state)
        with tempfile.NamedTemporaryFile(
            "w", encoding="utf-8", dir=self.root, delete=False
        ) as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            temporary = Path(handle.name)
        os.replace(temporary, self._path(state.session_id))

    def record_prompt(
        self, state: SessionState, prompt: str, current_tree_hash: str
    ) -> str:
        digest = prompt_hash(prompt)
        origin = "graft" if digest == state.pending_feedback_hash else "user"
        if origin == "graft":
            state.pending_feedback_hash = None
        else:
            if state.status == "accepted":
                state.task_epoch += 1
                state.prompts = []
                state.baseline_tree_hash = current_tree_hash
                state.last_verified_checkpoint_key = None
                state.verification_round = 0
                state.status = "active"
            if state.baseline_tree_hash is None:
                state.baseline_tree_hash = current_tree_hash
        state.prompts.append(PromptRecord(prompt, digest, origin))
        self.save(state)
        return origin

    def _path(self, session_id: str) -> Path:
        safe = re.sub(r"[^A-Za-z0-9_.-]", "_", session_id)[:160]
        return self.root / f"{safe or 'unknown'}.json"


def session_state_to_dict(state: SessionState) -> dict[str, Any]:
    return asdict(state)
