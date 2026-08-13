from __future__ import annotations

import os
import shlex
from pathlib import Path
from typing import override

from harbor.agents.installed.codex import Codex
from harbor.environments.base import BaseEnvironment
from harbor.models.agent.context import AgentContext
from harbor.models.trial.paths import EnvironmentPaths

from experiments.terminal_bench.profile_loader import (
    build_external_profile,
    load_public_profile,
)


class NativeCodex(Codex):
    """Harbor's Codex agent with only host proxy forwarding added.

    This is the native control condition. It installs no GRAFT files and runs
    no external online verifier. Proxy forwarding is shared with GraftCodex so
    networking is not an experimental difference.
    """

    @staticmethod
    @override
    def name() -> str:
        return "native-codex"

    def __init__(
        self,
        *args,
        extra_env: dict[str, str] | None = None,
        use_host_auth_json: bool = False,
        **kwargs,
    ) -> None:
        self.use_host_auth_json = bool(use_host_auth_json)
        merged_env = dict(extra_env or {})
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
            if value := os.environ.get(key):
                merged_env.setdefault(key, value)
        super().__init__(*args, extra_env=merged_env, **kwargs)

    @override
    def _resolve_auth_json_path(self) -> Path | None:
        """Select ChatGPT authentication without adding a redaction secret.

        Harbor treats every configured agent ``env`` value as sensitive and
        replaces that value in downloaded text artifacts.  A conventional
        ``CODEX_FORCE_AUTH_JSON=1`` therefore corrupts every digit ``1`` in
        JSON logs.  The explicit agent option keeps authentication selection
        out of that redaction set while retaining Harbor's normal upload path.
        """

        configured = super()._resolve_auth_json_path()
        if configured is not None or not self.use_host_auth_json:
            return configured
        default = Path.home() / ".codex" / "auth.json"
        if not default.is_file():
            raise ValueError(
                f"use_host_auth_json is enabled but {default} does not exist"
            )
        return default

    @override
    async def run(
        self,
        instruction: str,
        environment: BaseEnvironment,
        context: AgentContext,
    ) -> None:
        if environment.default_user is None:
            identity = await self.exec_as_agent(environment, command="id -u")
            agent_uid = (identity.stdout or "").strip()
            if not agent_uid.isdigit():
                raise RuntimeError("Could not resolve the disposable agent user's UID")
            # Harbor 0.20 only chowns an uploaded auth.json when this field is
            # populated. Some task images have a non-root default USER without
            # declaring it in task metadata. Apply this generic infrastructure
            # repair to both Native and GRAFT conditions before Harbor performs
            # its normal Codex authentication setup.
            environment.default_user = agent_uid
        await super().run(instruction, environment, context)


class GraftCodex(NativeCodex):
    """Harbor Codex adapter with a released GRAFT plugin and public profile."""

    GRAFT_REPOSITORY = "https://github.com/Zongwei9888/GRAFT.git"
    GRAFT_REF = "v0.4.0"
    GRAFT_SOURCE = "/opt/graft-v0.4.0"
    REMOTE_CODEX_HOME = "/tmp/codex-home"
    VERIFIER_ROOT = "/opt/graft-public-verifiers"
    GRAFT_CONFIG_HOME = "/logs/agent/graft-config"
    GRAFT_STATE_HOME = "/logs/agent/graft-state"
    WORKSPACE = "/app"

    @staticmethod
    @override
    def name() -> str:
        return "graft-codex"

    def __init__(
        self,
        *args,
        graft_profile: str = "session-window-debug",
        extra_env: dict[str, str] | None = None,
        **kwargs,
    ) -> None:
        self.graft_profile = graft_profile
        self._profile_config, self._profile_assets = load_public_profile(
            graft_profile
        )
        self._external_profile = build_external_profile(
            graft_profile, self._profile_config
        )
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
                f"git clone --depth 1 --branch {shlex.quote(self.GRAFT_REF)} "
                f"{shlex.quote(self.GRAFT_REPOSITORY)} "
                f"{shlex.quote(self.GRAFT_SOURCE)}"
            ),
        )
        # Harbor already executes Codex inside a disposable Docker sandbox. The
        # wrapper enables the profile's reviewed hooks for this one invocation.
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
        profile_root = f"{self.VERIFIER_ROOT}/{self.graft_profile}"
        commands = [
            (
                "if [ -s ~/.nvm/nvm.sh ]; then . ~/.nvm/nvm.sh; fi; "
                f"mkdir -p {shlex.quote(self.REMOTE_CODEX_HOME)} "
                f"{shlex.quote(self.GRAFT_CONFIG_HOME + '/profiles')} "
                f"{shlex.quote(profile_root)} {shlex.quote(agent_dir)}"
            )
        ]
        for filename, content in self._profile_assets.items():
            target = f"{profile_root}/{filename}"
            commands.append(
                f"printf '%s' {shlex.quote(content)} > {shlex.quote(target)}"
            )
        commands.extend(
            [
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
                    f"printf '%s' {shlex.quote(self._external_profile)} > "
                    f"{shlex.quote(self.GRAFT_CONFIG_HOME + '/profiles/' + self.graft_profile + '.json')}"
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
            ]
        )
        await self.exec_as_agent(
            environment,
            command=" && ".join(commands),
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
            # Harbor may redact text logs using authentication values. Preserve
            # the exact report bytes in a binary archive plus an external hash.
            await self.exec_as_agent(
                environment,
                command=(
                    f"find {shlex.quote(agent_dir + '/graft-state')} -type f "
                    f"-print 2>/dev/null | sort > "
                    f"{shlex.quote(agent_dir + '/graft-report-files.txt')} || true; "
                    f"if [ -d {shlex.quote(agent_dir + '/graft-state')} ]; then "
                    f"tar -czf {shlex.quote(agent_dir + '/graft-state.tar.gz')} "
                    f"-C {shlex.quote(agent_dir)} graft-state && "
                    f"sha256sum {shlex.quote(agent_dir + '/graft-state.tar.gz')} > "
                    f"{shlex.quote(agent_dir + '/graft-state.tar.gz.sha256')}; fi"
                ),
            )
