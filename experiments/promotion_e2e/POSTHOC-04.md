# Post-hoc diagnostic 04: promotion verifier replacement

The first successful same-thread continuation did not modify the restored checkpoint because the
diagnostic had moved it to a new temporary path outside the producer thread's original writable
sandbox root. Codex correctly reported that its patch was rejected, and the checkpoint hash stayed
unchanged.

That failed repair nevertheless exposed an independent selector defect. In promotion mode the
value-aware selector initially chose the mandatory `revalidates_feedback=true` candidate, but its
ordinary best-singleton fallback could replace that choice with a higher-value discovery verifier.
The controller consequently received another failure report instead of a promotion result. The
selector now disables non-promotion singleton replacement whenever a promotion requirement exists.
A regression test uses a deliberately higher-valued discovery candidate and requires the promotion
candidate to remain selected.

The next diagnostic restores the candidate at the original absolute workspace path bound to the
Codex session before continuing. This is a runner correction, not a method-effectiveness result.
