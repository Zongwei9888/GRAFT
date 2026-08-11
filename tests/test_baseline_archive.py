from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from graft.evidence.baseline_archive import archive_baseline, baseline_diff_excerpt
from graft.evidence.snapshot import freeze_source


class BaselineArchiveTests(unittest.TestCase):
    def test_non_git_baseline_content_produces_bounded_semantic_diff(self) -> None:
        with tempfile.TemporaryDirectory() as directory, tempfile.TemporaryDirectory() as state:
            root = Path(directory)
            source = root / "source.py"
            deleted = root / "deleted.txt"
            source.write_text("estimator = 'old'\n", encoding="utf-8")
            deleted.write_text("keep history\n", encoding="utf-8")
            baseline = freeze_source(root)
            archive = archive_baseline(
                root,
                files=baseline.files,
                file_hashes=baseline.file_hashes,
                tree_hash=baseline.tree_hash,
                archive_root=Path(state),
                session_id="session",
                task_epoch=1,
            )

            source.write_text("estimator = 'new'\n", encoding="utf-8")
            deleted.unlink()
            (root / "added.txt").write_text("new evidence\n", encoding="utf-8")
            current = freeze_source(
                root,
                baseline_tree_hash=baseline.tree_hash,
                baseline_files=baseline.files,
                baseline_file_hashes=baseline.file_hashes,
                baseline_archive_path=str(archive),
            )

            diff = baseline_diff_excerpt(current)
            self.assertIn("-estimator = 'old'", diff)
            self.assertIn("+estimator = 'new'", diff)
            self.assertIn("baseline/deleted.txt", diff)
            self.assertIn("candidate/added.txt", diff)

    def test_archive_manifest_must_match_checkpoint_baseline(self) -> None:
        with tempfile.TemporaryDirectory() as directory, tempfile.TemporaryDirectory() as state:
            root = Path(directory)
            (root / "source.py").write_text("before\n", encoding="utf-8")
            baseline = freeze_source(root)
            archive = archive_baseline(
                root,
                files=baseline.files,
                file_hashes=baseline.file_hashes,
                tree_hash=baseline.tree_hash,
                archive_root=Path(state),
                session_id="session",
                task_epoch=1,
            )
            current = freeze_source(
                root,
                baseline_tree_hash="different",
                baseline_files=baseline.files,
                baseline_file_hashes=baseline.file_hashes,
                baseline_archive_path=str(archive),
            )
            self.assertIn("does not match", baseline_diff_excerpt(current))

    def test_binary_baseline_is_hashed_but_not_copied_into_state(self) -> None:
        with tempfile.TemporaryDirectory() as directory, tempfile.TemporaryDirectory() as state:
            root = Path(directory)
            binary = root / "fixture.bin"
            binary.write_bytes(b"\0private-binary")
            baseline = freeze_source(root)
            archive = archive_baseline(
                root,
                files=baseline.files,
                file_hashes=baseline.file_hashes,
                tree_hash=baseline.tree_hash,
                archive_root=Path(state),
                session_id="session",
                task_epoch=1,
            )
            binary.write_bytes(b"\0changed-binary")
            current = freeze_source(
                root,
                baseline_tree_hash=baseline.tree_hash,
                baseline_files=baseline.files,
                baseline_file_hashes=baseline.file_hashes,
                baseline_archive_path=str(archive),
            )
            self.assertIn("baseline text unavailable", baseline_diff_excerpt(current))


if __name__ == "__main__":
    unittest.main()
