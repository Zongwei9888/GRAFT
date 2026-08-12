# Protocol amendment 02 — resource exhaustion is not No-Op evidence

Date: 2026-08-12

M2 retry 1 restored the exact archived checkpoint
`166539c6a22ff69aaf0481fa556c47f8ffc84a32fe7a201c994bc61da7eecb3f`; its frozen held-out
evaluator again passed 15/15 checks. The first valid feedback graph took 177.898 seconds to model,
which exceeded the default 120-second task-epoch wall-time budget before any verifier could run.

On that one graph, Original selected `adversarial-test-1` plus `repo-evidence-1` at nominal cost
3.25. The online value-aware call selected an empty set with `evaluated_candidates=0`, but the
selector marked it as a feasible No-Op and the controller returned `ALLOW`. This is a safety
semantics defect: no candidate was evaluated under the remaining resource budget, so the empty set
cannot mean that every candidate has non-positive conservative marginal value.

A post-hoc, execution-free diagnostic on the identical graph removed only the resource-feasibility
constraint. In that diagnostic the value-aware selector evaluated 14 candidate choices and still
selected No-Op. This supports the intended selector mechanism for this passing checkpoint, but it
does not validate the online `ALLOW` path or establish causal utility.

The product correction distinguishes:

- feasible evaluated empty set: `no_op=true`, eligible for `ALLOW`;
- no resource-feasible candidate: `feasible=false`, `no_op=false`, `UNRESOLVED`;
- promotion without a feasible revalidator: `UNRESOLVED` with the stricter promotion reason.

No task rule, hidden evaluator case, verifier score, selection coefficient, prompt, or task outcome
is changed. M3 remains not exercised because the frozen M2 run produced no reproducible feedback.
