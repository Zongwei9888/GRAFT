from __future__ import annotations

import json
import shlex
from pathlib import Path
from typing import override

from harbor.environments.base import BaseEnvironment
from harbor.models.agent.context import AgentContext
from harbor.models.trial.paths import EnvironmentPaths

from experiments.terminal_bench.graft_codex_agent import NativeCodex
from experiments.terminal_bench.graft_original_codex_agent import GraftOriginalCodex
from experiments.coding_verifier_matrix.verifier_matrix import (
    select_unique_workspace,
)


class VerifierMatrixCodex(GraftOriginalCodex):
    """Native Codex producer followed by read-only/shadow GRAFT verifier collection."""

    MATRIX_CODEX_HOME = "/tmp/graft-matrix-codex-home"
    MATRIX_BASELINE = "/logs/agent/verifier-matrix-baseline.json"
    MATRIX_BASELINE_ARCHIVES = "/logs/agent/verifier-matrix-baselines"
    MATRIX_REQUIREMENTS = "/logs/agent/verifier-matrix-requirements.json"
    MATRIX_CONFIG = "/logs/agent/verifier-matrix-config.json"
    MATRIX_OUTPUT = "/logs/agent/verifier-matrix.json"
    MATRIX_WORKSPACE = "/logs/agent/verifier-matrix-workspace.txt"

    @staticmethod
    @override
    def name() -> str:
        return "codex-verifier-matrix"

    @override
    async def run(
        self,
        instruction: str,
        environment: BaseEnvironment,
        context: AgentContext,
    ) -> None:
        await self._resolve_workspace(environment)
        await self._write_requirements_and_capture_baseline(instruction, environment)
        previous_workdir = environment.task_env_config.workdir
        environment.task_env_config.workdir = self.WORKSPACE
        try:
            await NativeCodex.run(self, instruction, environment, context)
        finally:
            environment.task_env_config.workdir = previous_workdir
        await self._run_shadow_matrix(environment)

    async def _resolve_workspace(self, environment: BaseEnvironment) -> None:
        """Discover the benchmark worktree without task-name or dataset branches."""

        result = await self.exec_as_agent(
            environment,
            command=(
                "{ git rev-parse --show-toplevel 2>/dev/null || true; "
                "find / -mindepth 2 -maxdepth 2 -name .git -print 2>/dev/null "
                "| while IFS= read -r marker; do "
                "git -C \"${marker%/.git}\" rev-parse --show-toplevel "
                "2>/dev/null || true; done; } | sort -u"
            ),
        )
        workspace = select_unique_workspace((result.stdout or "").splitlines())
        self.WORKSPACE = workspace.as_posix()
        await self.exec_as_agent(
            environment,
            command=(
                f"printf '%s\\n' {shlex.quote(self.WORKSPACE)} > "
                f"{shlex.quote(self.MATRIX_WORKSPACE)}"
            ),
        )

    async def _write_requirements_and_capture_baseline(
        self, instruction: str, environment: BaseEnvironment
    ) -> None:
        requirements = json.dumps([instruction], ensure_ascii=False)
        pythonpath = f"{self.GRAFT_SOURCE}:{self.GRAFT_SOURCE}/src"
        await self.exec_as_agent(
            environment,
            command=(
                f"mkdir -p {shlex.quote(EnvironmentPaths.agent_dir.as_posix())} && "
                f"printf '%s' {shlex.quote(requirements)} > "
                f"{shlex.quote(self.MATRIX_REQUIREMENTS)} && "
                f"PYTHONPATH={shlex.quote(pythonpath)} python3 -m "
                "experiments.coding_verifier_matrix.verifier_matrix config "
                f"--model {shlex.quote(self.model_name or '')} "
                f"--output {shlex.quote(self.MATRIX_CONFIG)} && "
                f"PYTHONPATH={shlex.quote(pythonpath)} python3 -m "
                "experiments.coding_verifier_matrix.verifier_matrix capture "
                f"--repo {shlex.quote(self.WORKSPACE)} "
                f"--output {shlex.quote(self.MATRIX_BASELINE)} "
                f"--archive-root {shlex.quote(self.MATRIX_BASELINE_ARCHIVES)}"
            ),
            env={
                "GRAFT_CONFIG_HOME": self.GRAFT_CONFIG_HOME,
                "GRAFT_STATE_HOME": self.GRAFT_STATE_HOME,
            },
        )

    async def _run_shadow_matrix(self, environment: BaseEnvironment) -> None:
        env = {
            "CODEX_HOME": self.MATRIX_CODEX_HOME,
            "GRAFT_CONFIG_HOME": self.GRAFT_CONFIG_HOME,
            "GRAFT_STATE_HOME": self.GRAFT_STATE_HOME,
        }
        if openai_base_url := self._get_env("OPENAI_BASE_URL"):
            env["OPENAI_BASE_URL"] = openai_base_url
        auth_json_path = self._resolve_auth_json_path()
        await self.exec_as_agent(
            environment,
            command=f"mkdir -p {shlex.quote(self.MATRIX_CODEX_HOME)}",
            env=env,
        )
        if auth_json_path:
            remote_auth = f"{self.MATRIX_CODEX_HOME}/auth.json"
            await environment.upload_file(Path(auth_json_path), remote_auth)
            if environment.default_user is not None:
                await self.exec_as_root(
                    environment,
                    command=(
                        f"chown {environment.default_user} {shlex.quote(remote_auth)}"
                    ),
                )
        else:
            env["OPENAI_API_KEY"] = self._get_env("OPENAI_API_KEY") or ""

        pythonpath = f"{self.GRAFT_SOURCE}:{self.GRAFT_SOURCE}/src"
        matrix_log = f"{EnvironmentPaths.agent_dir.as_posix()}/verifier-matrix.log"
        try:
            await self.exec_as_agent(
                environment,
                command=(
                    "if [ -s ~/.nvm/nvm.sh ]; then . ~/.nvm/nvm.sh; fi; "
                    f"PYTHONPATH={shlex.quote(pythonpath)} python3 -m "
                    "experiments.coding_verifier_matrix.verifier_matrix run "
                    f"--repo {shlex.quote(self.WORKSPACE)} "
                    f"--baseline {shlex.quote(self.MATRIX_BASELINE)} "
                    f"--requirements {shlex.quote(self.MATRIX_REQUIREMENTS)} "
                    f"--config {shlex.quote(self.MATRIX_CONFIG)} "
                    f"--output {shlex.quote(self.MATRIX_OUTPUT)} "
                    f"--max-verifiers 8 > {shlex.quote(matrix_log)} 2>&1"
                ),
                env=env,
            )
        except Exception:
            self.logger.exception("Shadow verifier-matrix collection failed")
        finally:
            try:
                await self.exec_as_agent(
                    environment,
                    command=f"rm -rf {shlex.quote(self.MATRIX_CODEX_HOME)}",
                    env=env,
                )
            except Exception:
                self.logger.exception("Could not clean the matrix Codex home")
