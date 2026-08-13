from __future__ import annotations

import hashlib
import json
import re
import shlex
from pathlib import Path
from typing import Any, override

from harbor.environments.base import BaseEnvironment
from harbor.models.agent.context import AgentContext
from harbor.models.trial.paths import EnvironmentPaths

from experiments.coding_verifier_matrix.matrix_codex_agent import (
    VerifierMatrixCodex,
)
from experiments.terminal_bench.graft_codex_agent import NativeCodex


class MatrixContinuationCodex(VerifierMatrixCodex):
    """Restore a frozen matrix checkpoint and resume its original Codex thread."""

    INPUT_ROOT = "/logs/agent/matrix-continuation-input"
    FEEDBACK_OUTPUT = "/logs/agent/matrix-continuation-feedback.json"
    PROMOTION_OUTPUT = "/logs/agent/matrix-continuation-promotion.json"
    REPAIRED_ARCHIVES = "/logs/agent/matrix-continuation-repaired"
    PROMOTION_CODEX_HOME = "/tmp/graft-promotion-codex-home"

    @staticmethod
    @override
    def name() -> str:
        return "codex-matrix-continuation"

    def __init__(
        self,
        *args: Any,
        source_job_dir: str,
        matrix_sha256: str,
        candidate_archive_sha256: str,
        session_sha256: str,
        expected_thread_id: str,
        **kwargs: Any,
    ) -> None:
        super().__init__(*args, **kwargs)
        self.source_job_dir = Path(source_job_dir).expanduser().resolve()
        if not self.source_job_dir.is_dir():
            raise FileNotFoundError(f"Source matrix job not found: {self.source_job_dir}")
        trial_dirs = tuple(
            path.parent.parent
            for path in self.source_job_dir.glob("*/agent/verifier-matrix.json")
        )
        if len(trial_dirs) != 1:
            raise ValueError("Source matrix job must contain exactly one completed trial")
        self.source_trial = trial_dirs[0]
        agent_dir = self.source_trial / "agent"
        self.local_matrix = agent_dir / "verifier-matrix.json"
        self.local_config = agent_dir / "verifier-matrix-config.json"
        self.local_baseline = agent_dir / "verifier-matrix-baseline.json"
        self.local_requirements = agent_dir / "verifier-matrix-requirements.json"
        self.local_baseline_archive = _single_file(
            agent_dir / "verifier-matrix-baselines", "*.tar.gz"
        )
        self.local_candidate_archive = _single_file(
            agent_dir / "verifier-matrix-candidates", "*.tar.gz"
        )
        self.local_session = _single_file(agent_dir / "sessions", "*.jsonl")
        for value, label in (
            (matrix_sha256, "matrix_sha256"),
            (candidate_archive_sha256, "candidate_archive_sha256"),
            (session_sha256, "session_sha256"),
        ):
            if re.fullmatch(r"[0-9a-f]{64}", value) is None:
                raise ValueError(f"{label} must be a lowercase SHA-256 digest")
        _require_sha(self.local_matrix, matrix_sha256)
        _require_sha(self.local_candidate_archive, candidate_archive_sha256)
        _require_sha(self.local_session, session_sha256)
        self.matrix_sha256 = matrix_sha256
        self.candidate_archive_sha256 = candidate_archive_sha256
        self.session_sha256 = session_sha256
        self.expected_thread_id = expected_thread_id
        session_header = json.loads(
            self.local_session.read_text(encoding="utf-8").splitlines()[0]
        )
        if session_header.get("payload", {}).get("id") != expected_thread_id:
            raise ValueError("Saved Codex session does not match expected_thread_id")
        self.matrix = json.loads(self.local_matrix.read_text(encoding="utf-8"))

    @override
    async def run(
        self,
        instruction: str,
        environment: BaseEnvironment,
        context: AgentContext,
    ) -> None:
        del instruction
        await self._resolve_workspace(environment)
        remote = await self._upload_inputs(environment)
        pythonpath = f"{self.GRAFT_SOURCE}:{self.GRAFT_SOURCE}/src"
        restore = await self.exec_as_agent(
            environment,
            command=(
                f"PYTHONPATH={shlex.quote(pythonpath)} python3 -m "
                "experiments.coding_verifier_matrix.continuation_replay restore "
                f"--repo {shlex.quote(self.WORKSPACE)} "
                f"--archive {shlex.quote(remote['candidate_archive'])} "
                f"--archive-sha256 {shlex.quote(self.candidate_archive_sha256)} "
                f"--baseline {shlex.quote(remote['baseline'])} "
                f"--expected-tree {shlex.quote(str(self.matrix['candidate_tree_hash']))}"
            ),
        )
        restore_payload = json.loads((restore.stdout or "").splitlines()[-1])
        if restore_payload.get("status") != "restored":
            raise RuntimeError("Frozen candidate was not restored")

        feedback_result = await self.exec_as_agent(
            environment,
            command=(
                f"PYTHONPATH={shlex.quote(pythonpath)} python3 -m "
                "experiments.coding_verifier_matrix.continuation_replay feedback "
                f"--matrix {shlex.quote(remote['matrix'])} "
                f"--config {shlex.quote(remote['config'])} "
                f"--output {shlex.quote(self.FEEDBACK_OUTPUT)}"
            ),
        )
        feedback_payload = json.loads((feedback_result.stdout or "").splitlines()[-1])
        if feedback_payload.get("status") == "no_eligible_feedback":
            context.metadata = {
                "source_matrix_sha256": self.matrix_sha256,
                "feedback_checkpoint_key": self.matrix["checkpoint_key"],
                "original_thread_id": self.expected_thread_id,
                "promotion_status": "not_requested_no_eligible_feedback",
                "repaired_checkpoint_key": self.matrix["checkpoint_key"],
            }
            return
        if feedback_payload.get("status") != "feedback_ready":
            raise RuntimeError("Frozen feedback packet has an unknown status")
        feedback = str(feedback_payload["feedback"])

        previous_workdir = environment.task_env_config.workdir
        environment.task_env_config.workdir = self.WORKSPACE
        self._resume = True
        try:
            await NativeCodex.run(self, feedback, environment, context)
        finally:
            self._resume = False
            environment.task_env_config.workdir = previous_workdir

        promotion_env = await self._prepare_promotion_auth(environment)
        try:
            promotion = await self.exec_as_agent(
                environment,
                command=(
                    "if [ -s ~/.nvm/nvm.sh ]; then . ~/.nvm/nvm.sh; fi; "
                    f"PYTHONPATH={shlex.quote(pythonpath)} python3 -m "
                    "experiments.coding_verifier_matrix.continuation_replay promote "
                    f"--repo {shlex.quote(self.WORKSPACE)} "
                    f"--matrix {shlex.quote(remote['matrix'])} "
                    f"--config {shlex.quote(remote['config'])} "
                    f"--baseline {shlex.quote(remote['baseline'])} "
                    f"--baseline-archive {shlex.quote(remote['baseline_archive'])} "
                    f"--requirements {shlex.quote(remote['requirements'])} "
                    f"--archive-root {shlex.quote(self.REPAIRED_ARCHIVES)} "
                    f"--output {shlex.quote(self.PROMOTION_OUTPUT)}"
                ),
                env=promotion_env,
            )
            promotion_payload = json.loads((promotion.stdout or "").splitlines()[-1])
        finally:
            await self.exec_as_agent(
                environment,
                command=f"rm -rf {shlex.quote(self.PROMOTION_CODEX_HOME)}",
                env=promotion_env,
            )
        context.metadata = {
            "source_matrix_sha256": self.matrix_sha256,
            "feedback_checkpoint_key": self.matrix["checkpoint_key"],
            "original_thread_id": self.expected_thread_id,
            "promotion_status": promotion_payload.get("status"),
            "repaired_checkpoint_key": promotion_payload.get(
                "repaired_checkpoint_key"
            ),
        }

    async def _upload_inputs(self, environment: BaseEnvironment) -> dict[str, str]:
        session_root = self.source_trial / "agent" / "sessions"
        try:
            session_relative = self.local_session.relative_to(session_root)
        except ValueError as exc:
            raise ValueError("Saved Codex session is outside the source session root") from exc
        remote_session = (
            Path(EnvironmentPaths.agent_dir.as_posix())
            / "sessions"
            / session_relative
        ).as_posix()
        await self.exec_as_root(
            environment,
            command=(
                f"mkdir -p {shlex.quote(self.INPUT_ROOT)} "
                f"{shlex.quote(str(Path(remote_session).parent))}"
            ),
        )
        paths = {
            "matrix": (self.local_matrix, f"{self.INPUT_ROOT}/matrix.json"),
            "config": (self.local_config, f"{self.INPUT_ROOT}/config.json"),
            "baseline": (self.local_baseline, f"{self.INPUT_ROOT}/baseline.json"),
            "requirements": (
                self.local_requirements,
                f"{self.INPUT_ROOT}/requirements.json",
            ),
            "baseline_archive": (
                self.local_baseline_archive,
                f"{self.INPUT_ROOT}/baseline.tar.gz",
            ),
            "candidate_archive": (
                self.local_candidate_archive,
                f"{self.INPUT_ROOT}/candidate.tar.gz",
            ),
            "session": (
                self.local_session,
                remote_session,
            ),
        }
        for local, remote in paths.values():
            await environment.upload_file(local, remote)
            if environment.default_user is not None:
                await self.exec_as_root(
                    environment,
                    command=f"chown {environment.default_user} {shlex.quote(remote)}",
                )
        return {key: remote for key, (_, remote) in paths.items()}

    async def _prepare_promotion_auth(
        self, environment: BaseEnvironment
    ) -> dict[str, str]:
        env = {"CODEX_HOME": self.PROMOTION_CODEX_HOME}
        if openai_base_url := self._get_env("OPENAI_BASE_URL"):
            env["OPENAI_BASE_URL"] = openai_base_url
        await self.exec_as_agent(
            environment,
            command=f"mkdir -p {shlex.quote(self.PROMOTION_CODEX_HOME)}",
            env=env,
        )
        auth_json_path = self._resolve_auth_json_path()
        if auth_json_path:
            remote_auth = f"{self.PROMOTION_CODEX_HOME}/auth.json"
            await environment.upload_file(Path(auth_json_path), remote_auth)
            if environment.default_user is not None:
                await self.exec_as_root(
                    environment,
                    command=f"chown {environment.default_user} {shlex.quote(remote_auth)}",
                )
        else:
            env["OPENAI_API_KEY"] = self._get_env("OPENAI_API_KEY") or ""
        return env


def _single_file(root: Path, pattern: str) -> Path:
    matches = tuple(root.rglob(pattern))
    if len(matches) != 1:
        raise ValueError(f"Expected one {pattern} below {root}, found {len(matches)}")
    return matches[0]


def _require_sha(path: Path, expected: str) -> None:
    observed = hashlib.sha256(path.read_bytes()).hexdigest()
    if observed != expected:
        raise ValueError(
            f"Artifact digest mismatch for {path.name}: expected {expected}, observed {observed}"
        )
