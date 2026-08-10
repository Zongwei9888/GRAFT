from __future__ import annotations

from pathlib import Path
from typing import Protocol

from graft.schema import RunConfig, TurnResult


class CodexRunner(Protocol):
    def start_thread(
        self, task: str, repo: Path, config: RunConfig
    ) -> TurnResult: ...

    def continue_thread(
        self, thread_id: str, feedback: str, repo: Path, config: RunConfig
    ) -> TurnResult: ...
