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


class _FrozenCandidateAgent(VerifierMatrixCodex):
    """Shared artifact and authentication handling for environment branches."""

    INPUT_ROOT = "/logs/agent/environment-branch-input"
    BRANCH_CODEX_HOME = "/tmp/graft-environment-branch-codex-home"

    def __init__(
        self,
        *args: Any,
        source_capture_job_dir: str,
        candidate_manifest_sha256: str,
        candidate_archive_sha256: str,
        **kwargs: Any,
    ) -> None:
        super().__init__(*args, **kwargs)
        self.source_capture_job_dir = Path(source_capture_job_dir).expanduser().resolve()
        if not self.source_capture_job_dir.is_dir():
            raise FileNotFoundError(
                f"Source capture job not found: {self.source_capture_job_dir}"
            )
        self.capture_agent_dir = _single_trial_agent_dir(
            self.source_capture_job_dir, "verifier-candidate.json"
        )
        self.local_candidate = self.capture_agent_dir / "verifier-candidate.json"
        self.local_baseline = (
            self.capture_agent_dir / "verifier-matrix-baseline.json"
        )
        self.local_requirements = (
            self.capture_agent_dir / "verifier-matrix-requirements.json"
        )
        self.local_config = self.capture_agent_dir / "verifier-matrix-config.json"
        self.local_candidate_archive = _single_file(
            self.capture_agent_dir / "verifier-matrix-candidates", "*.tar.gz"
        )
        self.local_baseline_archive = _single_file(
            self.capture_agent_dir / "verifier-matrix-baselines", "*.tar.gz"
        )
        _validate_sha(candidate_manifest_sha256, "candidate_manifest_sha256")
        _validate_sha(candidate_archive_sha256, "candidate_archive_sha256")
        _require_sha(self.local_candidate, candidate_manifest_sha256)
        _require_sha(self.local_candidate_archive, candidate_archive_sha256)
        candidate = json.loads(self.local_candidate.read_text(encoding="utf-8"))
        if candidate.get("status") != "candidate_captured":
            raise ValueError("Source job does not contain a replayable candidate")
        if candidate.get("candidate_archive_sha256") != candidate_archive_sha256:
            raise ValueError("Candidate manifest and archive digest disagree")
        self.candidate = candidate
        self.candidate_manifest_sha256 = candidate_manifest_sha256
        self.candidate_archive_sha256 = candidate_archive_sha256

    async def _upload_capture_bundle(
        self, environment: BaseEnvironment
    ) -> dict[str, str]:
        await self.exec_as_root(
            environment,
            command=f"mkdir -p {shlex.quote(self.INPUT_ROOT)}",
        )
        paths = {
            "candidate": (
                self.local_candidate,
                f"{self.INPUT_ROOT}/candidate.json",
            ),
            "baseline": (
                self.local_baseline,
                f"{self.INPUT_ROOT}/baseline.json",
            ),
            "requirements": (
                self.local_requirements,
                f"{self.INPUT_ROOT}/requirements.json",
            ),
            "config": (self.local_config, f"{self.INPUT_ROOT}/config.json"),
            "candidate_archive": (
                self.local_candidate_archive,
                f"{self.INPUT_ROOT}/candidate.tar.gz",
            ),
            "baseline_archive": (
                self.local_baseline_archive,
                f"{self.INPUT_ROOT}/baseline.tar.gz",
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

    async def _ensure_agent_identity(self, environment: BaseEnvironment) -> None:
        if environment.default_user is not None:
            return
        identity = await self.exec_as_agent(environment, command="id -u")
        agent_uid = (identity.stdout or "").strip()
        if not agent_uid.isdigit():
            raise RuntimeError("Could not resolve the disposable agent user's UID")
        environment.default_user = agent_uid

    async def _restore_candidate(
        self,
        environment: BaseEnvironment,
        remote: dict[str, str],
    ) -> None:
        pythonpath = f"{self.GRAFT_SOURCE}:{self.GRAFT_SOURCE}/src"
        result = await self.exec_as_agent(
            environment,
            command=(
                f"PYTHONPATH={shlex.quote(pythonpath)} python3 -m "
                "experiments.coding_verifier_matrix.continuation_replay restore "
                f"--repo {shlex.quote(self.WORKSPACE)} "
                f"--archive {shlex.quote(remote['candidate_archive'])} "
                f"--archive-sha256 {shlex.quote(self.candidate_archive_sha256)} "
                f"--baseline {shlex.quote(remote['baseline'])} "
                f"--expected-tree "
                f"{shlex.quote(str(self.candidate['candidate_tree_hash']))}"
            ),
        )
        payload = json.loads((result.stdout or "").splitlines()[-1])
        if payload.get("status") != "restored":
            raise RuntimeError("Frozen candidate was not restored")

    async def _prepare_branch_auth(
        self, environment: BaseEnvironment
    ) -> dict[str, str]:
        env = {
            "CODEX_HOME": self.BRANCH_CODEX_HOME,
            "GRAFT_CONFIG_HOME": self.GRAFT_CONFIG_HOME,
            "GRAFT_STATE_HOME": self.GRAFT_STATE_HOME,
        }
        if openai_base_url := self._get_env("OPENAI_BASE_URL"):
            env["OPENAI_BASE_URL"] = openai_base_url
        for key in (
            "HTTPS_PROXY",
            "HTTP_PROXY",
            "ALL_PROXY",
            "NO_PROXY",
            "https_proxy",
            "http_proxy",
            "all_proxy",
            "no_proxy",
        ):
            if value := self._get_env(key):
                env[key] = value
        await self.exec_as_agent(
            environment,
            command=f"mkdir -p {shlex.quote(self.BRANCH_CODEX_HOME)}",
            env=env,
        )
        auth_json_path = self._resolve_auth_json_path()
        if auth_json_path:
            remote_auth = f"{self.BRANCH_CODEX_HOME}/auth.json"
            await environment.upload_file(Path(auth_json_path), remote_auth)
            if environment.default_user is not None:
                await self.exec_as_root(
                    environment,
                    command=f"chown {environment.default_user} {shlex.quote(remote_auth)}",
                )
        else:
            env["OPENAI_API_KEY"] = self._get_env("OPENAI_API_KEY") or ""
        return env

    async def _clean_branch_auth(
        self, environment: BaseEnvironment, env: dict[str, str]
    ) -> None:
        await self.exec_as_agent(
            environment,
            command=f"rm -rf {shlex.quote(self.BRANCH_CODEX_HOME)}",
            env=env,
        )


class MatrixPlanCodex(_FrozenCandidateAgent):
    """Restore a candidate and build only its task-specific GRAFT graph."""

    PLAN_OUTPUT = "/logs/agent/verifier-plan.json"

    @staticmethod
    @override
    def name() -> str:
        return "codex-verifier-plan"

    @override
    async def run(
        self,
        instruction: str,
        environment: BaseEnvironment,
        context: AgentContext,
    ) -> None:
        del instruction
        await self._ensure_agent_identity(environment)
        await self._resolve_workspace(environment)
        remote = await self._upload_capture_bundle(environment)
        await self._restore_candidate(environment, remote)
        env = await self._prepare_branch_auth(environment)
        pythonpath = f"{self.GRAFT_SOURCE}:{self.GRAFT_SOURCE}/src"
        try:
            result = await self.exec_as_agent(
                environment,
                command=(
                    "if [ -s ~/.nvm/nvm.sh ]; then . ~/.nvm/nvm.sh; fi; "
                    f"PYTHONPATH={shlex.quote(pythonpath)} python3 -m "
                    "experiments.coding_verifier_matrix.verifier_matrix plan "
                    f"--repo {shlex.quote(self.WORKSPACE)} "
                    f"--baseline {shlex.quote(remote['baseline'])} "
                    f"--candidate {shlex.quote(remote['candidate'])} "
                    f"--requirements {shlex.quote(remote['requirements'])} "
                    f"--config {shlex.quote(remote['config'])} "
                    f"--output {shlex.quote(self.PLAN_OUTPUT)} --max-verifiers 8"
                ),
                env=env,
            )
            payload = json.loads((result.stdout or "").splitlines()[-1])
            if payload.get("status") != "planned":
                raise RuntimeError(f"Matrix planning ended with {payload.get('status')}")
        finally:
            await self._clean_branch_auth(environment, env)
        context.metadata = {
            "candidate_manifest_sha256": self.candidate_manifest_sha256,
            "candidate_archive_sha256": self.candidate_archive_sha256,
            "candidate_checkpoint_key": self.candidate["checkpoint_key"],
            "phase": "graft_graph_plan",
            "official_reward_role": "ignored_branch_health_check",
        }


class VerifierBranchCodex(_FrozenCandidateAgent):
    """Restore a candidate and execute one dynamically planned verifier."""

    RESULT_OUTPUT = "/logs/agent/verifier-branch.json"

    @staticmethod
    @override
    def name() -> str:
        return "codex-verifier-branch"

    def __init__(
        self,
        *args: Any,
        source_plan_job_dir: str,
        plan_sha256: str,
        verifier_id: str,
        **kwargs: Any,
    ) -> None:
        super().__init__(*args, **kwargs)
        self.source_plan_job_dir = Path(source_plan_job_dir).expanduser().resolve()
        if not self.source_plan_job_dir.is_dir():
            raise FileNotFoundError(
                f"Source plan job not found: {self.source_plan_job_dir}"
            )
        plan_agent_dir = _single_trial_agent_dir(
            self.source_plan_job_dir, "verifier-plan.json"
        )
        self.local_plan = plan_agent_dir / "verifier-plan.json"
        _validate_sha(plan_sha256, "plan_sha256")
        _require_sha(self.local_plan, plan_sha256)
        if not verifier_id or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*", verifier_id) is None:
            raise ValueError("verifier_id has an unsafe format")
        plan = json.loads(self.local_plan.read_text(encoding="utf-8"))
        if plan.get("status") != "planned":
            raise ValueError("Source job does not contain a completed plan")
        if verifier_id not in plan.get("verifier_ids", []):
            raise ValueError("verifier_id is not present in the frozen plan")
        if plan.get("checkpoint_key") != self.candidate.get("checkpoint_key"):
            raise ValueError("Plan and candidate checkpoint disagree")
        self.plan_sha256 = plan_sha256
        self.verifier_id = verifier_id

    @override
    async def run(
        self,
        instruction: str,
        environment: BaseEnvironment,
        context: AgentContext,
    ) -> None:
        del instruction
        await self._ensure_agent_identity(environment)
        await self._resolve_workspace(environment)
        remote = await self._upload_capture_bundle(environment)
        remote_plan = f"{self.INPUT_ROOT}/plan.json"
        await environment.upload_file(self.local_plan, remote_plan)
        if environment.default_user is not None:
            await self.exec_as_root(
                environment,
                command=f"chown {environment.default_user} {shlex.quote(remote_plan)}",
            )
        await self._restore_candidate(environment, remote)
        env = await self._prepare_branch_auth(environment)
        pythonpath = f"{self.GRAFT_SOURCE}:{self.GRAFT_SOURCE}/src"
        try:
            result = await self.exec_as_agent(
                environment,
                command=(
                    "if [ -s ~/.nvm/nvm.sh ]; then . ~/.nvm/nvm.sh; fi; "
                    f"PYTHONPATH={shlex.quote(pythonpath)} python3 -m "
                    "experiments.coding_verifier_matrix.verifier_matrix branch "
                    f"--repo {shlex.quote(self.WORKSPACE)} "
                    f"--baseline {shlex.quote(remote['baseline'])} "
                    f"--plan {shlex.quote(remote_plan)} "
                    f"--requirements {shlex.quote(remote['requirements'])} "
                    f"--config {shlex.quote(remote['config'])} "
                    f"--verifier-id {shlex.quote(self.verifier_id)} "
                    f"--output {shlex.quote(self.RESULT_OUTPUT)}"
                ),
                env=env,
            )
            payload = json.loads((result.stdout or "").splitlines()[-1])
            if payload.get("status") != "complete":
                raise RuntimeError(
                    f"Verifier branch ended with {payload.get('status')}"
                )
        finally:
            await self._clean_branch_auth(environment, env)
        context.metadata = {
            "candidate_manifest_sha256": self.candidate_manifest_sha256,
            "candidate_archive_sha256": self.candidate_archive_sha256,
            "candidate_checkpoint_key": self.candidate["checkpoint_key"],
            "plan_sha256": self.plan_sha256,
            "verifier_id": self.verifier_id,
            "phase": "single_verifier_branch",
            "official_reward_role": "ignored_branch_health_check",
        }


def _single_trial_agent_dir(job_dir: Path, marker: str) -> Path:
    matches = tuple(job_dir.glob(f"*/agent/{marker}"))
    if len(matches) != 1:
        raise ValueError(
            f"Expected one completed trial with {marker}, found {len(matches)}"
        )
    return matches[0].parent


def _single_file(root: Path, pattern: str) -> Path:
    matches = tuple(root.rglob(pattern))
    if len(matches) != 1:
        raise ValueError(f"Expected one {pattern} below {root}, found {len(matches)}")
    return matches[0]


def _validate_sha(value: str, label: str) -> None:
    if re.fullmatch(r"[0-9a-f]{64}", value) is None:
        raise ValueError(f"{label} must be a lowercase SHA-256 digest")


def _require_sha(path: Path, expected: str) -> None:
    observed = hashlib.sha256(path.read_bytes()).hexdigest()
    if observed != expected:
        raise ValueError(
            f"Artifact digest mismatch for {path.name}: expected {expected}, observed {observed}"
        )
