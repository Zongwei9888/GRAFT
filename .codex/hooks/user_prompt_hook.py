from _bootstrap import add_project_source

add_project_source()

from graft.codex.hooks import user_prompt_submit  # noqa: E402

raise SystemExit(user_prompt_submit())
