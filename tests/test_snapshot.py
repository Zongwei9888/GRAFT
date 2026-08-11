from __future__ import annotations

import tempfile
import unittest
import subprocess
from pathlib import Path

from graft.evidence.snapshot import freeze_source


class SnapshotTests(unittest.TestCase):
    def test_report_files_do_not_change_source_hash(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "module.py").write_text("answer = 42\n", encoding="utf-8")
            first = freeze_source(root, requirements=("keep answer",))
            report = root / ".graft" / "reports" / "report.json"
            report.parent.mkdir(parents=True)
            report.write_text("{}\n", encoding="utf-8")
            second = freeze_source(root, requirements=("keep answer",))
            self.assertEqual(first.tree_hash, second.tree_hash)
            self.assertEqual(first.checkpoint_key, second.checkpoint_key)

    def test_requirement_change_invalidates_checkpoint(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "module.py").write_text("answer = 42\n", encoding="utf-8")
            first = freeze_source(root, requirements=("requirement a",))
            second = freeze_source(root, requirements=("requirement b",))
            self.assertEqual(first.tree_hash, second.tree_hash)
            self.assertNotEqual(first.checkpoint_key, second.checkpoint_key)

    def test_standard_build_artifacts_do_not_change_source_hash(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "source.py").write_text("value = 1\n", encoding="utf-8")
            first = freeze_source(root)
            (root / "build").mkdir()
            (root / "build" / "generated.py").write_text("generated\n", encoding="utf-8")
            (root / "dist").mkdir()
            (root / "dist" / "package.whl").write_bytes(b"wheel")
            egg = root / "src" / "package.egg-info"
            egg.mkdir(parents=True)
            (egg / "PKG-INFO").write_text("metadata\n", encoding="utf-8")
            second = freeze_source(root)
            self.assertEqual(first.tree_hash, second.tree_hash)

    def test_git_ignore_policy_excludes_generated_task_artifacts(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            subprocess.run(["git", "init", "-q", str(root)], check=True)
            (root / ".gitignore").write_text("generated/\n", encoding="utf-8")
            (root / "source.any").write_text("one\n", encoding="utf-8")
            before = freeze_source(root)
            generated = root / "generated"
            generated.mkdir()
            (generated / "runtime-output.bin").write_bytes(b"ignored")
            ignored = freeze_source(root)
            self.assertEqual(before.tree_hash, ignored.tree_hash)
            (root / "new-untracked.any").write_text("relevant\n", encoding="utf-8")
            relevant = freeze_source(root)
            self.assertNotEqual(ignored.tree_hash, relevant.tree_hash)

    def test_baseline_manifest_distinguishes_modified_and_added_files(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "contract.md").write_text("original\n", encoding="utf-8")
            baseline = freeze_source(root)
            (root / "contract.md").write_text("candidate claim\n", encoding="utf-8")
            (root / "new_test.py").write_text("assert True\n", encoding="utf-8")
            current = freeze_source(
                root,
                baseline_tree_hash=baseline.tree_hash,
                baseline_files=baseline.files,
                baseline_file_hashes=baseline.file_hashes,
            )
            self.assertNotEqual(
                current.file_hashes["contract.md"],
                current.baseline_file_hashes["contract.md"],
            )
            self.assertNotIn("new_test.py", current.baseline_file_hashes)


if __name__ == "__main__":
    unittest.main()
