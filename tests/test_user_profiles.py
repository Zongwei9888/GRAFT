from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from graft.project_config import set_project_enabled
from graft.user_profiles import create_profile, list_profiles


class UserProfileTests(unittest.TestCase):
    def test_profile_creation_requires_matchers_and_validates_listing(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            workspace = root / "workspace"
            workspace.mkdir()
            source = set_project_enabled(workspace, False).path
            with patch.dict(os.environ, {"GRAFT_CONFIG_HOME": str(root / "config")}):
                with self.assertRaises(ValueError):
                    create_profile("python", source)
                result = create_profile(
                    "python",
                    source,
                    files_all=("pyproject.toml",),
                )
                self.assertTrue(result.valid)
                profiles = list_profiles()
                self.assertEqual(len(profiles), 1)
                self.assertEqual(profiles[0].match, {"files_all": ["pyproject.toml"]})
                document = json.loads(result.path.read_text(encoding="utf-8"))
                self.assertFalse(document["config"]["enabled"])


if __name__ == "__main__":
    unittest.main()
