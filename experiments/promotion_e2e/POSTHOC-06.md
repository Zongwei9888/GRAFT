# Post-hoc diagnostic 06: resume sandbox propagation

Two same-thread repairs returned the original thread ID but could not modify files, even after the
checkpoint was restored at the original absolute workspace path. Codex reported that the workspace
was read-only, produced no successful command events, and left the checkpoint unchanged.

The cause was the CLI adapter. `codex exec resume` does not expose the top-level `--sandbox` option
shown by `codex exec`; the adapter therefore omitted sandbox configuration. Because controlled
experiments also use `--ignore-user-config`, the resumed turn fell back to read-only. The official
Codex configuration reference defines `sandbox_mode` with `workspace-write` as a supported value.
A minimal live CLI experiment confirmed that this override preserves the same thread and permits a
write when the resume process uses the target workspace as its current directory.

`CliCodexRunner.continue_thread` now passes `-c sandbox_mode="<RunConfig.sandbox>"` and propagates
the explicit workspace-write network setting. A command-line regression test records the fake CLI
argv and checks both overrides. This correction is generic and post-hoc.
