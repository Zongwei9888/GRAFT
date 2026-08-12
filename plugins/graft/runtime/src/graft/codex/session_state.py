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
from graft.schema import PromotionRequirement, StageCost


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
    baseline_files: list[str] = field(default_factory=list)
    baseline_file_hashes: dict[str, str] = field(default_factory=dict)
    baseline_archive_path: str | None = None
    last_verified_checkpoint_key: str | None = None
    last_blocked_tree_hash: str | None = None
    last_feedback_hash: str | None = None
    pending_feedback_hash: str | None = None
    verification_round: int = 0
    spent_budget: float = 0.0
    spent_wall_time_s: float = 0.0
    spent_model_cost_usd: float = 0.0
    unknown_cost_stages: int = 0
    stage_costs: list[StageCost] = field(default_factory=list)
    pending_promotion: PromotionRequirement | None = None
    promotion_status: str | None = None
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
            baseline_files=[str(item) for item in raw.get("baseline_files", [])],
            baseline_file_hashes={
                str(path): str(digest)
                for path, digest in raw.get("baseline_file_hashes", {}).items()
            },
            baseline_archive_path=raw.get("baseline_archive_path"),
            last_verified_checkpoint_key=raw.get("last_verified_checkpoint_key"),
            last_blocked_tree_hash=raw.get("last_blocked_tree_hash"),
            last_feedback_hash=raw.get("last_feedback_hash"),
            pending_feedback_hash=raw.get("pending_feedback_hash"),
            verification_round=int(raw.get("verification_round", 0)),
            spent_budget=float(raw.get("spent_budget", 0.0)),
            spent_wall_time_s=float(raw.get("spent_wall_time_s", 0.0)),
            spent_model_cost_usd=float(raw.get("spent_model_cost_usd", 0.0)),
            unknown_cost_stages=int(raw.get("unknown_cost_stages", 0)),
            stage_costs=[
                StageCost(
                    stage_id=str(item["stage_id"]),
                    kind=str(item["kind"]),
                    duration_s=float(item["duration_s"]),
                    input_tokens=(
                        int(item["input_tokens"])
                        if item.get("input_tokens") is not None
                        else None
                    ),
                    cached_input_tokens=(
                        int(item["cached_input_tokens"])
                        if item.get("cached_input_tokens") is not None
                        else None
                    ),
                    output_tokens=(
                        int(item["output_tokens"])
                        if item.get("output_tokens") is not None
                        else None
                    ),
                    estimated_cost_usd=(
                        float(item["estimated_cost_usd"])
                        if item.get("estimated_cost_usd") is not None
                        else None
                    ),
                )
                for item in raw.get("stage_costs", [])
                if isinstance(item, dict)
            ],
            pending_promotion=_promotion_from_raw(raw.get("pending_promotion")),
            promotion_status=(
                str(raw["promotion_status"])
                if raw.get("promotion_status") is not None
                else None
            ),
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
        self,
        state: SessionState,
        prompt: str,
        current_tree_hash: str,
        current_files: tuple[str, ...] = (),
        current_file_hashes: dict[str, str] | None = None,
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
                state.baseline_files = list(current_files)
                state.baseline_file_hashes = dict(current_file_hashes or {})
                state.baseline_archive_path = None
                state.last_verified_checkpoint_key = None
                state.verification_round = 0
                state.spent_budget = 0.0
                state.spent_wall_time_s = 0.0
                state.spent_model_cost_usd = 0.0
                state.unknown_cost_stages = 0
                state.stage_costs = []
                state.pending_promotion = None
                state.promotion_status = None
                state.status = "active"
            if state.baseline_tree_hash is None:
                state.baseline_tree_hash = current_tree_hash
                state.baseline_files = list(current_files)
                state.baseline_file_hashes = dict(current_file_hashes or {})
        state.prompts.append(PromptRecord(prompt, digest, origin))
        self.save(state)
        return origin

    def _path(self, session_id: str) -> Path:
        safe = re.sub(r"[^A-Za-z0-9_.-]", "_", session_id)[:160]
        return self.root / f"{safe or 'unknown'}.json"


def session_state_to_dict(state: SessionState) -> dict[str, Any]:
    return asdict(state)


def _promotion_from_raw(value: Any) -> PromotionRequirement | None:
    if not isinstance(value, dict):
        return None
    try:
        return PromotionRequirement(
            feedback_checkpoint_key=str(value["feedback_checkpoint_key"]),
            report_path=(str(value["report_path"]) if value.get("report_path") else None),
            behavior_descriptions=tuple(
                str(item) for item in value.get("behavior_descriptions", [])
            ),
            failure_descriptions=tuple(
                str(item) for item in value.get("failure_descriptions", [])
            ),
            evidence_observations=tuple(
                str(item) for item in value.get("evidence_observations", [])
            ),
            reproduction_commands=tuple(
                tuple(str(part) for part in command)
                for command in value.get("reproduction_commands", [])
                if isinstance(command, list)
            ),
        )
    except (KeyError, TypeError, ValueError):
        return None
