# Post-hoc diagnostic 08: promotion evidence and decision semantics

The writable same-thread run produced a changed checkpoint and a promotion verifier returned
`fixed_and_preserved`, but the controller initially kept the checkpoint unresolved. A raw-event
audit on the same repaired checkpoint identified two domain-neutral implementation defects.

First, a passing verdict correctly had no top-level failing `failure_modes`. The verifier executor
used that empty list to filter every evidence item's target modes, so genuine executed PASS
evidence could never set `executed_evidence`. The executor now retains evidence modes from the
verifier's declared target set only for a passing designated promotion verifier. Failing verdicts
retain the stricter top-level intersection behavior.

Second, after executed promotion evidence was recognized, graph-wide `coverage_gap` metadata still
vetoed the `fixed_and_preserved` state. Promotion is a conditional decision about the
feedback-induced change: the prior executable finding must be fixed and the packet's named
behaviors preserved. Generic gaps remain in the report, but do not veto this state. Concrete new
findings, verifier errors, abstentions, `not_fixed`, `regressed`, and unexecuted evidence still do.

`audit_promotion_events.py` records the full Codex verifier event stream instead of trusting its
structured summary. On the final replay, the frozen promotion graph selected the mandatory
revalidator, seven completed command events were observed, eligible evidence was bound to the
repaired checkpoint, and the controller returned:

```text
decision = allow
promotion_outcome = fixed_and_preserved
executed_evidence = true
```

This diagnostic was designed after observing the initial pilot and is not a preregistered
effectiveness result. Both fixes are generic state/evidence semantics; neither contains the task,
its filenames, expected glob outputs, benchmark identity, or a task-specific verifier route.

