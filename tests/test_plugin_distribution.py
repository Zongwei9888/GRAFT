from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from graft import __version__


PROJECT_ROOT = Path(__file__).resolve().parents[1]
PLUGIN_ROOT = PROJECT_ROOT / "plugins" / "graft"
LAUNCHER = PLUGIN_ROOT / "scripts" / "graft_plugin.py"


class PluginDistributionTests(unittest.TestCase):
    def test_repository_marketplace_points_to_the_plugin(self) -> None:
        marketplace = json.loads(
            (PROJECT_ROOT / ".agents" / "plugins" / "marketplace.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual(marketplace["name"], "graft")
        entries = {item["name"]: item for item in marketplace["plugins"]}
        source = entries["graft"]["source"]
        self.assertEqual(source["source"], "local")
        self.assertEqual(source["path"], "./plugins/graft")
        self.assertTrue(PLUGIN_ROOT.is_dir())

    def test_manifest_and_default_hooks_form_a_portable_plugin(self) -> None:
        manifest = json.loads(
            (PLUGIN_ROOT / ".codex-plugin" / "plugin.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual(manifest["name"], "graft")
        self.assertEqual(manifest["version"].split("+", 1)[0], __version__)
        self.assertEqual(manifest["skills"], "./skills/")
        self.assertNotIn("hooks", manifest)

        hooks = json.loads(
            (PLUGIN_ROOT / "hooks" / "hooks.json").read_text(encoding="utf-8")
        )["hooks"]
        self.assertEqual(
            set(hooks), {"UserPromptSubmit", "PostToolUse", "Stop"}
        )
        for groups in hooks.values():
            for group in groups:
                for handler in group["hooks"]:
                    self.assertIn("$PLUGIN_ROOT", handler["command"])
                    self.assertIn("graft-plugin-v1", handler["command"])
        stop_handler = hooks["Stop"][0]["hooks"][0]
        self.assertGreaterEqual(
            stop_handler["timeout"],
            600,
            "The Stop window must cover both sequential modelers and the longest verifier",
        )

    def test_bundled_runtime_matches_core_source(self) -> None:
        completed = subprocess.run(
            [sys.executable, "scripts/sync_plugin_runtime.py", "--check"],
            cwd=PROJECT_ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertIn("matches", completed.stdout)

    def test_skill_keeps_reverification_inside_the_stop_round_budget(self) -> None:
        skill = (PLUGIN_ROOT / "skills" / "graft" / "SKILL.md").read_text(
            encoding="utf-8"
        )
        self.assertIn("never run `cli verify` in response", skill)
        self.assertIn("Stop hook will verify", skill)
        self.assertIn("max_feedback_rounds", skill)

    def test_launcher_works_without_checkout_pythonpath(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            workspace = root / "workspace"
            workspace.mkdir()
            environment = os.environ.copy()
            environment.pop("PYTHONPATH", None)
            environment.update(
                {
                    "PLUGIN_ROOT": str(PLUGIN_ROOT),
                    "GRAFT_CONFIG_HOME": str(root / "config"),
                    "GRAFT_STATE_HOME": str(root / "state"),
                    "PYTHONDONTWRITEBYTECODE": "1",
                }
            )

            status = subprocess.run(
                [
                    sys.executable,
                    str(LAUNCHER),
                    "cli",
                    "status",
                    "--repo",
                    str(workspace),
                ],
                cwd=workspace,
                env=environment,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(status.returncode, 0, status.stderr)
            self.assertEqual(
                json.loads(status.stdout)["config_source"],
                "graft-original-default",
            )

            event = {
                "session_id": "plugin-test-session",
                "turn_id": "prompt-1",
                "cwd": str(workspace),
                "hook_event_name": "UserPromptSubmit",
                "prompt": "Inspect this workspace",
            }
            hook = subprocess.run(
                [
                    sys.executable,
                    str(LAUNCHER),
                    "hook",
                    "user-prompt",
                    "--installation-id",
                    "graft-plugin-v1",
                ],
                cwd=workspace,
                env=environment,
                input=json.dumps(event),
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(hook.returncode, 0, hook.stderr)
            self.assertEqual(json.loads(hook.stdout), {"continue": True})
            session_files = list((root / "state").rglob("plugin-test-session.json"))
            self.assertEqual(len(session_files), 1)
            state = json.loads(session_files[0].read_text(encoding="utf-8"))
            self.assertEqual(
                [item["text"] for item in state["prompts"] if item["origin"] == "user"],
                ["Inspect this workspace"],
            )

    def test_bundled_runtime_rejects_malformed_profile_matcher(self) -> None:
        environment = os.environ.copy()
        environment["PYTHONPATH"] = str(PLUGIN_ROOT / "runtime" / "src")
        environment["PYTHONDONTWRITEBYTECODE"] = "1"
        script = (
            "from pathlib import Path; import graft; "
            "from graft.configuration import _profile_matches; "
            "assert 'plugins/graft/runtime/src' in graft.__file__.replace('\\\\', '/'); "
            "assert _profile_matches(Path('.').resolve(), "
            "{'match': {'files_all': 7}}) is False"
        )
        completed = subprocess.run(
            [sys.executable, "-c", script],
            cwd=PROJECT_ROOT,
            env=environment,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)


if __name__ == "__main__":
    unittest.main()
