from _bootstrap import add_project_source

add_project_source()

from graft.codex.hooks import stop  # noqa: E402

raise SystemExit(stop())
