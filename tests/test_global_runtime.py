from __future__ import annotations

import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from graft.codex.event_dedup import claim_event
from graft.codex.global_install import (
    _install_cli_link,
    _stage_install_source,
    installed_hook_command,
    inspect_global_hooks,
    install_global_hooks,
    uninstall_global_hooks,
)
from graft.codex.session_state import SessionState, SessionStateStore
from graft.configuration import (
    project_config_trust,
    resolve_config,
    trust_project_config,
)
from graft.project_config import initialize_project
from graft.registry import ORIGINAL_METHOD_ID, default_original_config_payload
from graft.runtime_paths import resolve_workspace, workspace_runtime_paths


class GlobalRuntimeTests(unittest.TestCase):
    def test_install_is_idempotent_and_preserves_existing_hooks(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            codex_home = root / "codex"
            codex_home.mkdir()
            runtime = root / "graft-hook"
            runtime.write_text("#!/bin/sh\n", encoding="utf-8")
            hooks_path = codex_home / "hooks.json"
            hooks_path.write_text(
                json.dumps(
                    {
                        "description": "existing",
                        "hooks": {
                            "SessionStart": [
                                {
                                    "hooks": [
                                        {
                                            "type": "command",
                                            "command": "/tmp/existing-hook",
                                        }
                                    ]
                                }
                            ]
                        },
                    }
                ),
                encoding="utf-8",
            )
            first = install_global_hooks(runtime, codex_home=codex_home)
            second = install_global_hooks(runtime, codex_home=codex_home)
            self.assertEqual(first.installed_handlers, 3)
            self.assertIsNotNone(first.backup_path)
            self.assertIsNone(second.backup_path)
            doctor = inspect_global_hooks(codex_home=codex_home)
            self.assertEqual(doctor["graft_handlers"], 3)
            self.assertTrue(doctor["runtime_commands_exist"])
            document = json.loads(hooks_path.read_text(encoding="utf-8"))
            self.assertEqual(document["description"], "existing")
            self.assertIn("SessionStart", document["hooks"])
            self.assertEqual(
                document["hooks"]["Stop"][-1]["hooks"][0]["timeout"],
                600,
            )

            removed = uninstall_global_hooks(codex_home=codex_home)
            self.assertEqual(removed.removed_handlers, 3)
            remaining = json.loads(hooks_path.read_text(encoding="utf-8"))
            self.assertEqual(
                remaining["hooks"]["SessionStart"][0]["hooks"][0]["command"],
                "/tmp/existing-hook",
            )

    def test_config_resolution_and_state_are_outside_workspace(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            workspace = root / "repo"
            workspace.mkdir()
            config = root / "config-home"
            state = root / "state-home"
            with patch.dict(
                os.environ,
                {
                    "GRAFT_CONFIG_HOME": str(config),
                    "GRAFT_STATE_HOME": str(state),
                },
            ):
                default = resolve_config(workspace)
                self.assertEqual(default.source, "graft-original-default")
                self.assertEqual(default.load().method, ORIGINAL_METHOD_ID)
                subprocess.run(
                    ["git", "init", "-q", str(workspace)], check=True
                )
                still_default = resolve_config(workspace)
                self.assertEqual(still_default.source, "graft-original-default")
                self.assertEqual(
                    [item.template_id for item in still_default.load().verifier_templates],
                    [
                        "repository-evidence-agent",
                        "semantic-reviewer",
                        "agentic-evidence-reviewer",
                        "test-agent",
                    ],
                )
                paths = workspace_runtime_paths(workspace)
                store = SessionStateStore(workspace)
                store.save(SessionState(session_id="central"))
                self.assertTrue((paths.state_dir / "central.json").is_file())
                self.assertFalse((workspace / ".graft" / "state").exists())

    def test_profile_precedes_safe_git_and_workspace_resolves_git_root(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            workspace = root / "repo"
            child = workspace / "nested"
            child.mkdir(parents=True)
            subprocess.run(["git", "init", "-q", str(workspace)], check=True)
            config_home = root / "config"
            profiles = config_home / "profiles"
            profiles.mkdir(parents=True)
            (workspace / "pyproject.toml").write_text("[project]\n", encoding="utf-8")
            profile_config = default_original_config_payload(enabled=False)
            (profiles / "python.json").write_text(
                json.dumps(
                    {
                        "match": {"files_all": ["pyproject.toml"]},
                        "config": profile_config,
                    }
                ),
                encoding="utf-8",
            )
            with patch.dict(os.environ, {"GRAFT_CONFIG_HOME": str(config_home)}):
                resolved_root = resolve_workspace(child)
                self.assertEqual(resolved_root, workspace.resolve())
                resolution = resolve_config(resolved_root)
                self.assertEqual(resolution.source, "profile:python")

    def test_invalid_matching_profile_falls_back_to_graft_original(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            workspace = root / "workspace"
            workspace.mkdir()
            (workspace / "marker.any").write_text("present\n", encoding="utf-8")
            config_home = root / "config"
            profiles = config_home / "profiles"
            profiles.mkdir(parents=True)
            (profiles / "00-malformed.json").write_text(
                json.dumps(
                    {
                        "match": {"files_all": 7},
                        "config": {"version": 2, "method": ORIGINAL_METHOD_ID},
                    }
                ),
                encoding="utf-8",
            )
            (profiles / "legacy.json").write_text(
                json.dumps(
                    {
                        "match": {"files_all": ["marker.any"]},
                        "config": {"version": 1, "method": "empirical-fixture"},
                    }
                ),
                encoding="utf-8",
            )
            with patch.dict(os.environ, {"GRAFT_CONFIG_HOME": str(config_home)}):
                resolution = resolve_config(workspace)
                self.assertEqual(resolution.source, "graft-original-default")
                self.assertEqual(resolution.load().method, ORIGINAL_METHOD_ID)

    def test_project_commands_require_hash_trust_and_changes_revoke_it(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            workspace = root / "repo"
            workspace.mkdir()
            subprocess.run(["git", "init", "-q", str(workspace)], check=True)
            config_home = root / "config"
            with patch.dict(os.environ, {"GRAFT_CONFIG_HOME": str(config_home)}):
                result = initialize_project(workspace)
                self.assertEqual(
                    resolve_config(workspace).source, "graft-original-default"
                )
                trusted = trust_project_config(workspace)
                self.assertTrue(trusted.trusted)
                self.assertEqual(resolve_config(workspace).source, "project")

                raw = json.loads(result.path.read_text(encoding="utf-8"))
                raw["budget"] = raw["budget"] + 0.25
                result.path.write_text(json.dumps(raw), encoding="utf-8")
                self.assertFalse(project_config_trust(workspace).trusted)
                self.assertEqual(
                    resolve_config(workspace).source, "graft-original-default"
                )

    def test_event_claim_is_atomic_for_duplicate_hook_sources(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            events = Path(directory)
            event = {"session_id": "s", "turn_id": "t", "prompt": "work"}
            self.assertTrue(claim_event(events, "UserPromptSubmit", event))
            self.assertFalse(claim_event(events, "UserPromptSubmit", event))

    def test_cli_link_is_idempotent_and_never_replaces_unrelated_file(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            install = root / "install"
            binary = install / "runtime-venv" / "bin" / "graft"
            binary.parent.mkdir(parents=True)
            binary.write_text("managed", encoding="utf-8")
            bin_home = root / "bin"
            with patch.dict(
                os.environ,
                {
                    "GRAFT_INSTALL_HOME": str(install),
                    "GRAFT_BIN_HOME": str(bin_home),
                },
            ):
                link = _install_cli_link(binary, "graft")
                self.assertEqual(link.resolve(), binary.resolve())
                self.assertEqual(_install_cli_link(binary, "graft"), link)
                link.unlink()
                link.write_text("user-owned", encoding="utf-8")
                with self.assertRaises(FileExistsError):
                    _install_cli_link(binary, "graft")

    def test_installed_hook_detection_is_safe_when_entry_point_is_absent(self) -> None:
        detected = installed_hook_command()
        self.assertTrue(detected is None or detected.name.startswith("graft-hook"))

    def test_install_source_staging_excludes_checkout_build_artifacts(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "checkout"
            destination = Path(directory) / "staged"
            (root / "src" / "graft").mkdir(parents=True)
            (root / "src" / "codex_graft.egg-info").mkdir()
            (root / "build").mkdir()
            (root / "pyproject.toml").write_text("[build-system]\n", encoding="utf-8")
            (root / "README.md").write_text("readme\n", encoding="utf-8")
            (root / "src" / "graft" / "__init__.py").write_text("", encoding="utf-8")
            (root / "src" / "codex_graft.egg-info" / "PKG-INFO").write_text(
                "generated", encoding="utf-8"
            )
            (root / "build" / "generated.py").write_text("", encoding="utf-8")
            _stage_install_source(root, destination)
            self.assertTrue((destination / "src" / "graft" / "__init__.py").is_file())
            self.assertFalse((destination / "src" / "codex_graft.egg-info").exists())
            self.assertFalse((destination / "build").exists())


if __name__ == "__main__":
    unittest.main()
