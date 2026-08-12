# Post-hoc diagnostic 02: verifier command-reporting protocol

The first rerun after commit `77f9b3b` still returned `unresolved`. A temporary recording runner
captured the selected verifier's raw Codex command events. It showed that the evidence guard was
correct to reject the structured claims:

- one tool call executed a multi-case heredoc harness chained to the repository test suite;
- one standalone escaped-hyphen reproduction was executed, but the structured verdict rewrote it
  to a different Python payload instead of reporting the observed command;
- the separately claimed invalid-UTF-8 command was not present as a standalone tool event.

The evidence matcher therefore remains strict. The generic verifier prompt now requires every
blocking finding discovered in a harness to be rerun as one standalone, non-compound tool command
and requires the evidence packet to copy that exact command without semantic rewriting. No task,
language, file, or failure-specific rule is added.

This is a post-hoc engineering correction and cannot be credited to the original frozen pilot.
