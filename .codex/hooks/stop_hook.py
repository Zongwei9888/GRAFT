from _bootstrap import add_project_source

add_project_source()

from graft.codex.hooks import main  # noqa: E402

raise SystemExit(main(["stop", "--installation-id", "graft-repo-v1"]))
