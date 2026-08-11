from __future__ import annotations

import hashlib
import json
import re
import shlex
import tarfile
from pathlib import Path, PurePosixPath
from typing import Any, override

from harbor.agents.base import BaseAgent
from harbor.environments.base import BaseEnvironment
from harbor.models.agent.context import AgentContext
from harbor.models.trial.paths import EnvironmentPaths


class CheckpointReplayAgent(BaseAgent):
    """Restore a captured GRAFT Stop checkpoint for the official evaluator.

    This agent performs no task work. It lets a fresh trial evaluate the exact
    candidate that existed immediately before GRAFT feedback, making pre/post
    comparisons independent of producer sampling differences.
    """

    SUPPORTS_WINDOWS = False

    @staticmethod
    @override
    def name() -> str:
        return "graft-checkpoint-replay"

    def __init__(
        self,
        *args: Any,
        checkpoint_archive: str,
        checkpoint_sha256: str,
        expected_checkpoint_key: str,
        **kwargs: Any,
    ) -> None:
        super().__init__(*args, **kwargs)
        self.checkpoint_archive = Path(checkpoint_archive).expanduser().resolve()
        if not self.checkpoint_archive.is_file():
            raise FileNotFoundError(
                f"Checkpoint archive does not exist: {self.checkpoint_archive}"
            )
        if re.fullmatch(r"[0-9a-f]{64}", checkpoint_sha256) is None:
            raise ValueError("checkpoint_sha256 must be a lowercase SHA-256 digest")
        observed = hashlib.sha256(self.checkpoint_archive.read_bytes()).hexdigest()
        if observed != checkpoint_sha256:
            raise ValueError(
                f"Checkpoint archive digest mismatch: expected {checkpoint_sha256}, "
                f"observed {observed}"
            )
        self.checkpoint_sha256 = checkpoint_sha256
        self.metadata = _read_and_validate_archive(
            self.checkpoint_archive, expected_checkpoint_key
        )

    @override
    def version(self) -> str:
        return "1.0.0"

    @override
    async def setup(self, environment: BaseEnvironment) -> None:
        return

    @override
    async def run(
        self,
        instruction: str,
        environment: BaseEnvironment,
        context: AgentContext,
    ) -> None:
        del instruction
        remote_archive = "/tmp/graft-checkpoint-replay.tar.gz"
        stage = "/tmp/graft-checkpoint-replay-stage"
        await environment.upload_file(self.checkpoint_archive, remote_archive)

        commands = [
            f"rm -rf {shlex.quote(stage)}",
            f"mkdir -p {shlex.quote(stage)}",
            (
                f"tar -xzf {shlex.quote(remote_archive)} "
                f"-C {shlex.quote(stage)}"
            ),
        ]
        for relative in self.metadata["deleted_baseline_files"]:
            target = _workspace_target(relative)
            commands.append(
                f"if [ -e {shlex.quote(target)} ] || [ -L {shlex.quote(target)} ]; "
                f"then rm -rf -- {shlex.quote(target)}; fi"
            )
        for relative in self.metadata["files"]:
            target = _workspace_target(relative)
            commands.append(
                f"if [ -d {shlex.quote(target)} ] && [ ! -L {shlex.quote(target)} ]; "
                f"then rm -rf -- {shlex.quote(target)}; fi"
            )
        commands.extend(
            (
                f"cp -a {shlex.quote(stage + '/workspace/.')} /app/",
                (
                    f"cp {shlex.quote(stage + '/checkpoint.json')} "
                    f"{shlex.quote(EnvironmentPaths.agent_dir.as_posix() + '/checkpoint-replay.json')}"
                ),
            )
        )
        result = await environment.exec(
            " && ".join(commands),
            user="root",
            timeout_sec=120,
        )
        if result.return_code != 0:
            raise RuntimeError(
                "Could not restore checkpoint: "
                + (result.stderr or result.stdout or "unknown error")
            )
        context.metadata = {
            "checkpoint_key": self.metadata["checkpoint_key"],
            "tree_hash": self.metadata["tree_hash"],
            "verification_round": self.metadata["verification_round"],
            "checkpoint_sha256": self.checkpoint_sha256,
        }


def _read_and_validate_archive(path: Path, expected_checkpoint_key: str) -> dict[str, Any]:
    with tarfile.open(path, "r:gz") as archive:
        members = archive.getmembers()
        member_names = {item.name for item in members}
        if "checkpoint.json" not in member_names:
            raise ValueError("Checkpoint archive is missing checkpoint.json")
        symlink_paths: set[PurePosixPath] = set()
        for member in members:
            pure = PurePosixPath(member.name)
            if pure.is_absolute() or ".." in pure.parts:
                raise ValueError(f"Unsafe checkpoint archive member: {member.name}")
            if member.name != "checkpoint.json" and (
                not pure.parts or pure.parts[0] != "workspace"
            ):
                raise ValueError(f"Unexpected checkpoint archive member: {member.name}")
            if member.issym() or member.islnk():
                symlink_paths.add(pure)
        for member in members:
            pure = PurePosixPath(member.name)
            if any(parent in symlink_paths for parent in pure.parents):
                raise ValueError(
                    f"Checkpoint member is nested below a symlink: {member.name}"
                )

        metadata_member = archive.extractfile("checkpoint.json")
        if metadata_member is None:
            raise ValueError("Checkpoint metadata is unreadable")
        metadata = json.load(metadata_member)

    if not isinstance(metadata, dict) or metadata.get("version") != 1:
        raise ValueError("Unsupported checkpoint metadata")
    if metadata.get("checkpoint_key") != expected_checkpoint_key:
        raise ValueError("Checkpoint key does not match the frozen replay config")
    if metadata.get("skipped_files"):
        raise ValueError("Checkpoint capture skipped source files and cannot be replayed")
    for field in ("files", "deleted_baseline_files"):
        values = metadata.get(field)
        if not isinstance(values, list) or not all(
            isinstance(item, str) and _valid_relative(item) for item in values
        ):
            raise ValueError(f"Invalid checkpoint metadata field: {field}")
    expected_members = {f"workspace/{item}" for item in metadata["files"]}
    if not expected_members.issubset(member_names):
        raise ValueError("Checkpoint archive is missing one or more captured files")
    return metadata


def _valid_relative(value: str) -> bool:
    pure = PurePosixPath(value)
    return bool(pure.parts) and not pure.is_absolute() and ".." not in pure.parts


def _workspace_target(relative: str) -> str:
    if not _valid_relative(relative):
        raise ValueError(f"Unsafe workspace path in checkpoint: {relative}")
    return str(PurePosixPath("/app") / PurePosixPath(relative))
