# Post-hoc diagnostic 01: shell-wrapped evidence identity

This diagnostic was designed after the frozen pilot in `PROTOCOL.md` completed. It must not be
reported as part of that pre-registered result.

The frozen first candidate passed the 23 authored evaluator cases. A dynamically selected GRAFT
Test Agent additionally executed two direct requirement checks and reported failures: invalid raw
UTF-8 was accepted, and an escaped hyphen in a bracket class was interpreted as a range. Independent
replay of the archived checkpoint confirmed both failures.

GRAFT nevertheless returned `unresolved`: Codex recorded the executed command as a `zsh -lc`
wrapper while structured evidence named the simple inner `python3 -c` argv. The evidence-identity
guard did not recognize those representations as the same execution. Commit `77f9b3b` adds a
conservative generic match for one simple inner shell command while refusing compound commands,
pipelines, redirections, and substitutions. All 105 tests and release checks passed after the fix.

`rerun_after_match_fix.py` performs a diagnostic continuation without rerunning the producer:

1. restore the exact archived first checkpoint and verify its hash;
2. restore the previously generated graph and rerun the ordinary controller/verifier path;
3. continue only if the current controller returns `continue_with_evidence`;
4. resume the original producer thread and require thread-ID equality;
5. dynamically construct and execute the current promotion graph;
6. record the original evaluator separately from the two post-hoc counterexamples.

No result is forced. If the fresh verifier does not return eligible evidence, the script stops
without continuation.

