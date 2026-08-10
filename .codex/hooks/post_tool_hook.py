from _bootstrap import add_project_source

add_project_source()

from graft.codex.hooks import post_tool_use  # noqa: E402

raise SystemExit(post_tool_use())
