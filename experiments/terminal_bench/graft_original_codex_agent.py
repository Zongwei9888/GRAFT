from __future__ import annotations

import shlex
from typing import override

from harbor.environments.base import BaseEnvironment
from harbor.models.agent.context import AgentContext
from harbor.models.trial.paths import EnvironmentPaths

from experiments.terminal_bench.graft_codex_agent import NativeCodex


class GraftOriginalCodex(NativeCodex):
    """Harbor Codex adapter for the frozen, dynamic GRAFT Original method.

    The treatment installs an exact GRAFT source commit into a clean Codex home.
    It deliberately installs no task profile, command list, fixture, or verifier
    asset. The plugin must therefore construct the Behavior--Failure--Verifier--
    Lineage graph from the public instruction and observable workspace at Stop.
    """

    GRAFT_REPOSITORY = "https://github.com/Zongwei9888/GRAFT.git"
    GRAFT_COMMIT = "2ecea330faa0fce9b4e86688d831f22d73d80ace"
    GRAFT_SOURCE = "/opt/graft-original-v0.5.0"
    REMOTE_CODEX_HOME = "/tmp/codex-home"
    GRAFT_CONFIG_HOME = "/logs/agent/graft-config"
    GRAFT_STATE_HOME = "/logs/agent/graft-state"
    WORKSPACE = "/app"

    @staticmethod
    @override
    def name() -> str:
        return "graft-original-codex"

    def __init__(
        self,
        *args,
        extra_env: dict[str, str] | None = None,
        **kwargs,
    ) -> None:
        merged_env = dict(extra_env or {})
        merged_env.setdefault("GRAFT_CONFIG_HOME", self.GRAFT_CONFIG_HOME)
        merged_env.setdefault("GRAFT_STATE_HOME", self.GRAFT_STATE_HOME)
        super().__init__(*args, extra_env=merged_env, **kwargs)

    @override
    async def install(self, environment: BaseEnvironment) -> None:
        await super().install(environment)
        await self.exec_as_root(
            environment,
            command=(
                "apt-get update && "
                "DEBIAN_FRONTEND=noninteractive apt-get install -y git && "
                "rm -rf /var/lib/apt/lists/*"
            ),
        )
        await self.exec_as_root(
            environment,
            command=(
                f"rm -rf {shlex.quote(self.GRAFT_SOURCE)} && "
                f"git clone {shlex.quote(self.GRAFT_REPOSITORY)} "
                f"{shlex.quote(self.GRAFT_SOURCE)} && "
                f"git -C {shlex.quote(self.GRAFT_SOURCE)} checkout --detach "
                f"{shlex.quote(self.GRAFT_COMMIT)} && "
                f"test \"$(git -C {shlex.quote(self.GRAFT_SOURCE)} rev-parse HEAD)\" = "
                f"{shlex.quote(self.GRAFT_COMMIT)}"
            ),
        )
        # Harbor already executes Codex in a disposable Docker environment. The
        # wrapper opts this one experiment into the reviewed plugin hooks.
        await self.exec_as_root(
            environment,
            command=(
                "if [ -s ~/.nvm/nvm.sh ]; then . ~/.nvm/nvm.sh; fi; "
                'codex_bin="$(command -v codex)" && '
                'test -n "$codex_bin" && '
                'mv "$codex_bin" "${codex_bin}-real" && '
                "printf '%s\\n' "
                "'#!/bin/sh' "
                "'if [ \"$1\" = \"exec\" ]; then' "
                "'  shift' "
                "'  exec '" + '"${codex_bin}-real"' + "' exec --dangerously-bypass-hook-trust \"$@\"' "
                "'fi' "
                "'exec '" + '"${codex_bin}-real"' + "' \"$@\"' "
                '> "$codex_bin" && '
                'chmod 0755 "$codex_bin"'
            ),
        )

    async def _setup_graft(self, environment: BaseEnvironment) -> None:
        agent_dir = EnvironmentPaths.agent_dir.as_posix()
        setup = " && ".join(
            (
                (
                    "if [ -s ~/.nvm/nvm.sh ]; then . ~/.nvm/nvm.sh; fi; "
                    f"mkdir -p {shlex.quote(self.REMOTE_CODEX_HOME)} "
                    f"{shlex.quote(self.GRAFT_CONFIG_HOME)} "
                    f"{shlex.quote(self.GRAFT_STATE_HOME)} {shlex.quote(agent_dir)}"
                ),
                (
                    f"git -C {shlex.quote(self.GRAFT_SOURCE)} rev-parse HEAD > "
                    f"{shlex.quote(agent_dir + '/graft-source-commit.txt')}"
                ),
                (
                    f"CODEX_HOME={shlex.quote(self.REMOTE_CODEX_HOME)} "
                    f"codex plugin marketplace add {shlex.quote(self.GRAFT_SOURCE)} "
                    f"--json > {shlex.quote(agent_dir + '/graft-marketplace.json')}"
                ),
                (
                    f"CODEX_HOME={shlex.quote(self.REMOTE_CODEX_HOME)} "
                    "codex plugin add graft@graft --json "
                    f"> {shlex.quote(agent_dir + '/graft-plugin.json')}"
                ),
                (
                    f"test ! -d {shlex.quote(self.GRAFT_CONFIG_HOME + '/profiles')} || "
                    f"test -z \"$(find {shlex.quote(self.GRAFT_CONFIG_HOME + '/profiles')} "
                    "-type f -print -quit)\""
                ),
                (
                    f"graft_launcher=\"$(find {shlex.quote(self.REMOTE_CODEX_HOME)}"
                    "/plugins/cache/graft/graft -path '*/scripts/graft_plugin.py' "
                    "-type f | head -1)\" && test -n \"$graft_launcher\" && "
                    f"python3 \"$graft_launcher\" cli config validate --repo "
                    f"{shlex.quote(self.WORKSPACE)} > "
                    f"{shlex.quote(agent_dir + '/graft-config-validation.json')} && "
                    f"python3 \"$graft_launcher\" cli status --repo "
                    f"{shlex.quote(self.WORKSPACE)} > "
                    f"{shlex.quote(agent_dir + '/graft-config-status.json')}"
                ),
            )
        )
        await self.exec_as_agent(
            environment,
            command=setup,
            env={
                "CODEX_HOME": self.REMOTE_CODEX_HOME,
                "GRAFT_CONFIG_HOME": self.GRAFT_CONFIG_HOME,
                "GRAFT_STATE_HOME": self.GRAFT_STATE_HOME,
            },
        )

    @override
    async def run(
        self,
        instruction: str,
        environment: BaseEnvironment,
        context: AgentContext,
    ) -> None:
        await self._setup_graft(environment)
        try:
            await super().run(instruction, environment, context)
        finally:
            agent_dir = EnvironmentPaths.agent_dir.as_posix()
            await self.exec_as_agent(
                environment,
                command=(
                    f"find {shlex.quote(self.GRAFT_STATE_HOME)} -type f "
                    f"-print 2>/dev/null | sort > "
                    f"{shlex.quote(agent_dir + '/graft-report-files.txt')} || true; "
                    f"if [ -d {shlex.quote(self.GRAFT_STATE_HOME)} ]; then "
                    f"tar -czf {shlex.quote(agent_dir + '/graft-state.tar.gz')} "
                    f"-C {shlex.quote(agent_dir)} graft-state && "
                    f"sha256sum {shlex.quote(agent_dir + '/graft-state.tar.gz')} > "
                    f"{shlex.quote(agent_dir + '/graft-state.tar.gz.sha256')}; fi"
                ),
            )
