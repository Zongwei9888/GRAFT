from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from graft import __version__
from graft.codex import CliCodexRunner
from graft.codex.global_install import (
    inspect_global_hooks,
    installed_hook_command,
    install_global_hooks,
    provision_runtime,
    uninstall_global_hooks,
)
from graft.codex.runtime_authority import inspect_runtime_sources
from graft.configuration import (
    project_config_trust,
    resolve_config,
    trust_project_config,
    untrust_project_config,
)
from graft.controller import GraftController
from graft.evidence.snapshot import freeze_source
from graft.project_config import initialize_project, set_project_enabled
from graft.registry import load_config
from graft.replay import replay_selection
from graft.runtime_paths import resolve_workspace, workspace_runtime_paths
from graft.schema import DecisionKind, RunConfig, to_jsonable
from graft.user_profiles import create_profile, list_profiles


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="graft")
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    subparsers = parser.add_subparsers(dest="command", required=True)

    snapshot = subparsers.add_parser("snapshot", help="Hash a source checkpoint")
    snapshot.add_argument("--repo", type=Path, default=Path.cwd())
    snapshot.add_argument("--requirement", action="append", default=[])
    snapshot.add_argument("--config", type=Path)

    verify = subparsers.add_parser("verify", help="Select and execute verifiers")
    verify.add_argument("--repo", type=Path, default=Path.cwd())
    verify.add_argument("--config", type=Path)
    verify.add_argument("--requirement", action="append", default=[])
    verify.add_argument("--session-id", default="manual")

    replay = subparsers.add_parser(
        "replay-selection",
        help="Replay a selector over an existing report without executing verifiers",
    )
    replay.add_argument("--report", type=Path, required=True)
    replay.add_argument("--config", type=Path, required=True)
    replay.add_argument("--budget", type=float)

    codex_run = subparsers.add_parser(
        "codex-run", help="Run or continue a Codex thread with JSONL capture"
    )
    codex_run.add_argument("--repo", type=Path, default=Path.cwd())
    codex_run.add_argument("--prompt", required=True)
    codex_run.add_argument("--thread-id")
    codex_run.add_argument("--model")
    codex_run.add_argument(
        "--sandbox",
        choices=("read-only", "workspace-write", "danger-full-access"),
        default="workspace-write",
    )
    codex_run.add_argument("--timeout", type=float, default=900.0)
    codex_run.add_argument("--ephemeral", action="store_true")
    codex_run.add_argument("--isolate-config", action="store_true")
    codex_run.add_argument("--disable-hooks", action="store_true")

    install = subparsers.add_parser(
        "install-codex", help="Install GRAFT as user-level Codex hooks"
    )
    install.add_argument("--codex-home", type=Path)
    install.add_argument("--runtime-command", type=Path)
    install.add_argument("--source-root", type=Path)

    uninstall = subparsers.add_parser(
        "uninstall-codex", help="Remove only user-level GRAFT Codex hooks"
    )
    uninstall.add_argument("--codex-home", type=Path)

    doctor = subparsers.add_parser(
        "doctor", help="Inspect the global Codex integration and current workspace"
    )
    doctor.add_argument("--repo", type=Path, default=Path.cwd())
    doctor.add_argument("--codex-home", type=Path)

    initialize = subparsers.add_parser(
        "init", help="Create a domain-neutral GRAFT project override"
    )
    initialize.add_argument("--repo", type=Path, default=Path.cwd())
    initialize.add_argument(
        "--checkpoint-mode",
        choices=("completion", "strict", "explicit"),
        default="explicit",
        help=(
            "Use explicit opt-in by default; completion/strict are research modes until "
            "their trigger policy is calibrated"
        ),
    )
    initialize.add_argument(
        "--selection-policy",
        choices=("original", "value-aware"),
        default="original",
        help="Keep the frozen Original baseline or opt into the uncalibrated value-aware policy",
    )
    initialize.add_argument(
        "--verifier-network-access",
        action="store_true",
        help=(
            "Allow workspace-write verifier agents to access the network; disabled by default"
        ),
    )
    initialize.add_argument("--force", action="store_true")

    status = subparsers.add_parser(
        "status", help="Show how GRAFT resolves the current workspace"
    )
    status.add_argument("--repo", type=Path, default=Path.cwd())

    config = subparsers.add_parser(
        "config", help="Inspect, validate, enable, or disable project configuration"
    )
    config_commands = config.add_subparsers(dest="config_command", required=True)
    for name, help_text in (
        ("show", "Print the resolved configuration"),
        ("validate", "Validate the resolved or explicitly supplied configuration"),
        ("enable", "Enable GRAFT for this project"),
        ("disable", "Disable GRAFT for this project without uninstalling the plugin"),
        ("trust", "Trust the current project configuration hash after review"),
        ("untrust", "Revoke trust so project commands cannot run"),
    ):
        command = config_commands.add_parser(name, help=help_text)
        command.add_argument("--repo", type=Path, default=Path.cwd())
        if name == "validate":
            command.add_argument("--path", type=Path)

    profile = subparsers.add_parser(
        "profile", help="Manage reviewed user-level configuration profiles"
    )
    profile_commands = profile.add_subparsers(dest="profile_command", required=True)
    profile_commands.add_parser("list", help="List user profiles and validation status")
    profile_create = profile_commands.add_parser(
        "create", help="Create a profile by copying a reviewed project configuration"
    )
    profile_create.add_argument("name")
    profile_create.add_argument("--repo", type=Path, default=Path.cwd())
    profile_create.add_argument("--from-config", type=Path)
    profile_create.add_argument("--files-all", action="append", default=[])
    profile_create.add_argument("--files-any", action="append", default=[])
    profile_create.add_argument("--path-regex")
    profile_create.add_argument("--force", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.command == "snapshot":
        workspace = resolve_workspace(args.repo)
        if args.config is not None:
            config_path = args.config
            if not config_path.is_absolute():
                config_path = workspace / config_path
        else:
            config_path = resolve_config(workspace).path
        config = load_config(config_path)
        snapshot = freeze_source(
            workspace,
            requirements=tuple(args.requirement),
            config_path=config_path,
            environment_fingerprint=config.environment_fingerprint,
        )
        print(json.dumps(to_jsonable(snapshot), ensure_ascii=False, indent=2))
        return 0

    if args.command == "verify":
        workspace = resolve_workspace(args.repo)
        paths = workspace_runtime_paths(workspace)
        if args.config is not None:
            config_path = args.config
            if not config_path.is_absolute():
                config_path = workspace / config_path
        else:
            resolution = resolve_config(workspace)
            if not resolution.configured:
                print(
                    json.dumps(
                        {
                            "kind": "unconfigured",
                            "workspace": str(workspace),
                            "reason": resolution.reason,
                            "next": "Run graft init in this workspace.",
                        },
                        ensure_ascii=False,
                        indent=2,
                    )
                )
                return 2
            config_path = resolution.path
        controller = GraftController.from_path(
            config_path, report_root=paths.reports_dir
        )
        decision = controller.verify(
            workspace,
            requirements=tuple(args.requirement),
            session_id=args.session_id,
        )
        print(json.dumps(to_jsonable(decision), ensure_ascii=False, indent=2))
        return 1 if decision.kind == DecisionKind.CONTINUE_WITH_EVIDENCE else 0

    if args.command == "replay-selection":
        try:
            config = load_config(args.config.resolve())
            selection = replay_selection(
                args.report.resolve(), config, budget=args.budget
            )
        except (OSError, ValueError) as exc:
            print(str(exc), file=sys.stderr)
            return 2
        print(
            json.dumps(
                {
                    "report": str(args.report.resolve()),
                    "config": str(args.config.resolve()),
                    "method": config.method,
                    "selection": to_jsonable(selection),
                    "executed_verifiers": False,
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0

    if args.command == "codex-run":
        runner = CliCodexRunner()
        config = RunConfig(
            sandbox=args.sandbox,
            model=args.model,
            timeout_s=args.timeout,
            ephemeral=args.ephemeral,
            isolate_config=args.isolate_config,
            disable_hooks=args.disable_hooks,
        )
        if args.thread_id:
            result = runner.continue_thread(
                args.thread_id, args.prompt, args.repo, config
            )
        else:
            result = runner.start_thread(args.prompt, args.repo, config)
        print(json.dumps(to_jsonable(result), ensure_ascii=False, indent=2))
        return result.return_code

    if args.command == "install-codex":
        runtime = args.runtime_command or (
            installed_hook_command() if args.source_root is None else None
        )
        runtime = runtime or provision_runtime(source_root=args.source_root)
        result = install_global_hooks(runtime, codex_home=args.codex_home)
        payload = to_jsonable(result)
        payload["next"] = "Start Codex and use /hooks to trust the three current GRAFT hook hashes."
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0

    if args.command == "uninstall-codex":
        result = uninstall_global_hooks(codex_home=args.codex_home)
        print(json.dumps(to_jsonable(result), ensure_ascii=False, indent=2))
        return 0

    if args.command == "doctor":
        workspace = resolve_workspace(args.repo)
        paths = workspace_runtime_paths(workspace)
        resolution = resolve_config(workspace)
        payload = inspect_global_hooks(codex_home=args.codex_home)
        runtime_audit = inspect_runtime_sources(
            workspace,
            codex_home=args.codex_home,
        )
        payload["runtime_authority"] = runtime_audit
        payload["workspace"] = str(workspace)
        payload["workspace_id"] = paths.workspace_id
        payload["workspace_data"] = str(paths.workspace_data)
        payload["config_source"] = resolution.source
        payload["config_path"] = str(resolution.path) if resolution.path else None
        payload["config_reason"] = resolution.reason
        payload["project_trust"] = to_jsonable(project_config_trust(workspace))
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        integration_present = bool(runtime_audit["sources"])
        return 0 if integration_present and runtime_audit["healthy"] else 1

    if args.command == "init":
        workspace = resolve_workspace(args.repo)
        try:
            result = initialize_project(
                workspace,
                checkpoint_mode=args.checkpoint_mode,
                selection_policy=args.selection_policy,
                verifier_network_access=args.verifier_network_access,
                force=args.force,
            )
        except (FileExistsError, ValueError) as exc:
            print(str(exc), file=sys.stderr)
            return 2
        payload = to_jsonable(result)
        payload["project_trust"] = to_jsonable(trust_project_config(workspace))
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0

    if args.command == "status":
        workspace = resolve_workspace(args.repo)
        paths = workspace_runtime_paths(workspace)
        resolution = resolve_config(workspace)
        payload = {
            "workspace": str(workspace),
            "workspace_id": paths.workspace_id,
            "workspace_data": str(paths.workspace_data),
            "config_source": resolution.source,
            "config_path": str(resolution.path) if resolution.path else None,
            "reason": resolution.reason,
            "method": None,
            "verifier_templates": [],
            "selection": None,
            "project_trust": to_jsonable(project_config_trust(workspace)),
        }
        config = resolution.load()
        payload["method"] = config.method
        payload["enabled"] = config.enabled
        payload["checkpoint_mode"] = config.checkpoint_mode
        payload["verifier_templates"] = [
            item.template_id for item in config.verifier_templates
        ]
        payload["selection"] = {
            "strategy": config.selection.strategy,
            "algorithm": config.selection.algorithm,
            "status": "constructed dynamically at each verification checkpoint",
        }
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0

    if args.command == "config":
        workspace = resolve_workspace(args.repo)
        if args.config_command in {"enable", "disable"}:
            try:
                result = set_project_enabled(
                    workspace, enabled=args.config_command == "enable"
                )
            except (OSError, ValueError, json.JSONDecodeError) as exc:
                print(str(exc), file=sys.stderr)
                return 2
            payload = to_jsonable(result)
            trust = trust_project_config(workspace)
            payload["trusted_config_hash"] = trust.trusted_hash
            payload["next"] = (
                "GRAFT will verify changed completion checkpoints in new and active sessions."
                if result.enabled
                else "GRAFT hooks remain installed but this project is explicitly disabled."
            )
            print(json.dumps(payload, ensure_ascii=False, indent=2))
            return 0

        if args.config_command in {"trust", "untrust"}:
            try:
                trust = (
                    trust_project_config(workspace)
                    if args.config_command == "trust"
                    else untrust_project_config(workspace)
                )
            except (OSError, ValueError, json.JSONDecodeError) as exc:
                print(str(exc), file=sys.stderr)
                return 2
            print(json.dumps(to_jsonable(trust), ensure_ascii=False, indent=2))
            return 0

        resolution = resolve_config(workspace)
        path = args.path if args.config_command == "validate" else None
        if path is not None and not path.is_absolute():
            path = workspace / path
        project_candidate = workspace / ".graft" / "config.json"
        if args.config_command == "validate":
            path = path or (
                project_candidate if project_candidate.is_file() else resolution.path
            )
        else:
            path = resolution.path
        if path is None:
            print(
                json.dumps(
                    {
                        "valid": False,
                        "workspace": str(workspace),
                        "reason": resolution.reason,
                        "next": "Run `graft config enable` or `graft init`.",
                    },
                    ensure_ascii=False,
                    indent=2,
                )
            )
            return 2
        try:
            loaded = load_config(path)
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            print(
                json.dumps(
                    {"valid": False, "path": str(path), "error": str(exc)},
                    ensure_ascii=False,
                    indent=2,
                )
            )
            return 2
        payload = {
            "valid": True,
            "workspace": str(workspace),
            "source": resolution.source,
            "path": str(path.resolve()),
            "config": to_jsonable(loaded),
            "project_trust": to_jsonable(project_config_trust(workspace)),
        }
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0

    if args.command == "profile":
        if args.profile_command == "list":
            print(
                json.dumps(
                    {"profiles": [to_jsonable(item) for item in list_profiles()]},
                    ensure_ascii=False,
                    indent=2,
                )
            )
            return 0
        workspace = resolve_workspace(args.repo)
        source = args.from_config or (workspace / ".graft" / "config.json")
        if not source.is_absolute():
            source = workspace / source
        try:
            result = create_profile(
                args.name,
                source,
                files_all=tuple(args.files_all),
                files_any=tuple(args.files_any),
                path_regex=args.path_regex,
                force=args.force,
            )
        except (OSError, ValueError, FileExistsError, json.JSONDecodeError) as exc:
            print(str(exc), file=sys.stderr)
            return 2
        print(json.dumps(to_jsonable(result), ensure_ascii=False, indent=2))
        return 0

    raise AssertionError(f"Unhandled command: {args.command}")


if __name__ == "__main__":
    sys.exit(main())
