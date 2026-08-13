# Amendment 05 — VF2 branch closure and evidence-portability finding

Date: 2026-08-13

This amendment closes the first Terminal-Bench 3 environment-branch pilot without sending
feedback to the producer.

## Observed branch state

The frozen plan contained seven verifier IDs. Three branches returned complete structured results:

- `adversarial-test-01`: found a concrete `Graph.subgraph()` attribute-view mismatch;
- `agentic-evidence-02`: found a requirement-derived speed result of 296.94× geometric mean,
  below the required 1000× threshold;
- `agentic-evidence-01` and `repo-evidence-01` were not complete results: the Codex verifier
  timed out at the frozen 180-second verifier limit.

The remaining two planned semantic/repository branches failed during Harbor artifact collection
after their environment was removed (`no container found for service "main"`). One additional branch
also encountered a Docker base-image authorization/network failure. These are infrastructure errors,
not verifier PASS/FAIL outcomes.

## Evidence-gate result

The two complete LLM reports claimed reproducible findings, but GRAFT correctly retained
`reproducible=false`. Their reported commands depended on `/tmp/nx342`, a NetworkX installation
created inside the disposable verifier workspace. The command therefore cannot be replayed in the
producer environment after the branch is destroyed. The observed failure is real within the branch,
but it is not portable Stop evidence under the frozen policy. No continuation prompt was generated,
and no producer file or thread was changed.

This is a useful mechanism result, not a method success claim:

```text
LLM discovery: positive
portable executable evidence: negative
feedback: correctly suppressed
official post-feedback delta: not estimable (matrix censored)
```

The partial findings must not be assembled into a fake complete matrix. The official branch health
rewards are ignored. The v3 Native candidate remains the only valid official baseline for this row:
reward `0.0`, candidate tree hash
`0535e3f160f04b5095f958806b74f328c1e6f17605bc444f6c161c45d99e56cc`.

## Required follow-up before an effectiveness claim

The next pilot must keep the same frozen candidate/graph but use verifier prompts and runtime
support that produce standalone commands whose dependencies belong to the task environment or the
candidate archive. It must also raise or separately budget verifier timeouts and preflight Docker
image availability. A row is eligible for the feedback experiment only when every planned verifier
closes, its evidence passes the portability gate, and the full matrix is assembled without pruning.
