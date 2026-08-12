from __future__ import annotations

import shlex
from typing import override

from harbor.environments.base import BaseEnvironment
from harbor.models.trial.paths import EnvironmentPaths

from experiments.terminal_bench.graft_original_codex_agent import GraftOriginalCodex


class GraftValueAwareCodex(GraftOriginalCodex):
    """Harbor adapter for the domain-neutral opt-in value-aware policy.

    The policy is materialized outside ``/app`` as a generic path-matched user
    profile. The producer therefore receives no project-local GRAFT config,
    task command, fixture, expected output, or benchmark-specific verifier.
    """

    PROFILE_NAME = "harbor-value-aware"
    PROFILE_STAGE = "/tmp/graft-value-aware-profile"

    @staticmethod
    @override
    def name() -> str:
        return "graft-value-aware-codex"

    async def _setup_graft(self, environment: BaseEnvironment) -> None:
        # Install the exact source-pinned plugin and first establish that the
        # clean environment has no task profile or project override.
        await super()._setup_graft(environment)

        agent_dir = EnvironmentPaths.agent_dir.as_posix()
        command = " && ".join(
            (
                (
                    f"graft_launcher=\"$(find {shlex.quote(self.REMOTE_CODEX_HOME)}"
                    "/plugins/cache/graft/graft -path '*/scripts/graft_plugin.py' "
                    "-type f | head -1)\""
                ),
                'test -n "$graft_launcher"',
                f"rm -rf {shlex.quote(self.PROFILE_STAGE)}",
                f"mkdir -p {shlex.quote(self.PROFILE_STAGE)}",
                (
                    f"python3 \"$graft_launcher\" cli init --repo "
                    f"{shlex.quote(self.PROFILE_STAGE)} --selection-policy value-aware "
                    f"> {shlex.quote(agent_dir + '/graft-value-aware-init.json')}"
                ),
                (
                    f"python3 \"$graft_launcher\" cli profile create "
                    f"{shlex.quote(self.PROFILE_NAME)} --repo "
                    f"{shlex.quote(self.PROFILE_STAGE)} --from-config "
                    f"{shlex.quote(self.PROFILE_STAGE + '/.graft/config.json')} "
                    f"--path-regex {shlex.quote('^/app$')} > "
                    f"{shlex.quote(agent_dir + '/graft-value-aware-profile.json')}"
                ),
                (
                    f"python3 \"$graft_launcher\" cli status --repo "
                    f"{shlex.quote(self.WORKSPACE)} > "
                    f"{shlex.quote(agent_dir + '/graft-config-status.json')}"
                ),
                (
                    f"grep -q '\"config_source\": \"profile:{self.PROFILE_NAME}\"' "
                    f"{shlex.quote(agent_dir + '/graft-config-status.json')}"
                ),
                (
                    f"grep -q '\"method\": \"graft-value-aware\"' "
                    f"{shlex.quote(agent_dir + '/graft-config-status.json')}"
                ),
                (
                    f"grep -q '\"strategy\": \"value-aware\"' "
                    f"{shlex.quote(agent_dir + '/graft-config-status.json')}"
                ),
                # Remove the staging workspace. The active profile is an
                # external copy under GRAFT_CONFIG_HOME.
                f"rm -rf {shlex.quote(self.PROFILE_STAGE)}",
            )
        )
        await self.exec_as_agent(
            environment,
            command=command,
            env={
                "CODEX_HOME": self.REMOTE_CODEX_HOME,
                "GRAFT_CONFIG_HOME": self.GRAFT_CONFIG_HOME,
                "GRAFT_STATE_HOME": self.GRAFT_STATE_HOME,
                "GRAFT_CHECKPOINT_ARCHIVE_HOME": self.GRAFT_CHECKPOINT_ARCHIVE_HOME,
            },
        )
