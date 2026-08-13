# Coding verifier matrix protocol amendment 03

Status: **post-hoc engineering amendment; prospective only for future tasks**

Date: 2026-08-13

## Observation

The completed Terminal-Bench `cli-2ph-simplex` matrix contained a selected adversarial verifier
whose executed reproduction was one self-contained `python3 -c` process. Codex represented the tool
event and verdict command as:

```text
bash -lc <one quoted python3 -c argv>
```

The frozen guard rejected every `bash|sh|zsh -c|-lc` command. This correctly rejected pipelines,
setup-plus-test chains, redirections, heredocs, and temporary-script dependencies, but also rejected
the single-process transport above. The completed trial remains `no_eligible_feedback`; it is not
reclassified.

## General replacement rule

For future tasks, GRAFT may unwrap one shell transport only when all conditions hold:

1. argv is exactly `bash|sh|zsh -c|-lc <payload>` with no extra arguments;
2. the payload parses as one command;
3. it has no sequencing, pipeline, redirection, subshell, or command substitution;
4. it does not begin with a shell environment assignment;
5. it does not recursively invoke a shell;
6. the resulting inner argv independently passes the existing frozen-candidate/inline-program
   portability check.

Thus `bash -lc "python3 -c <program>"` may qualify, while
`bash -lc "python3 verifier_check.py"` still fails when that file was verifier-created, and
`bash -lc "setup && python3 -c <program>"` always fails.

This is command-transport normalization, not task semantics or a new verifier. It has unit tests in
both the Python package and mirrored plugin runtime. Any effectiveness evidence must come from a new
prospectively frozen task.
