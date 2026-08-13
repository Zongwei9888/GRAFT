# Frozen Metaflow same-thread continuation protocol

Status: **prospective for the continuation; post-hoc with respect to finding discovery**

Frozen date: 2026-08-13

This experiment tests continuation and promotion mechanics on the exact candidate from
`SMOKE-02.md`. It is not an effectiveness estimate and cannot be used as a held-out confirmation of
the discovered failure hypotheses.

## Fixed prefix

- source matrix SHA-256:
  `4881015a048e4f36061f55447b1bd91e109315c5f91497f78a926ae3dee3a509`;
- candidate tree:
  `b6dddee3e6906c475ace17c7018ed007c058d59dd0ca67605ffe24eec44c5e5f`;
- candidate archive SHA-256:
  `c132a465c8b5898e146bbca6979f93239ff043b386a90b4a6919064ab8b86eda`;
- producer thread: `019ff8d2-366e-72a2-81f4-2a7c3ecb50ed`;
- producer session SHA-256:
  `4fb55edbc0f193a5395697486b8861f50e849207da6067e5dc0dc203dd7d8baa`;
- model/runtime: `gpt-5.6-sol`, high reasoning, Codex CLI `0.147.0`.

## Procedure

1. Start a fresh official FeatureBench task container and verify that its source tree equals the
   frozen task-start baseline.
2. Restore the candidate archive, verify every manifest hash, and require the exact candidate tree.
3. Restore exactly one saved Codex session and use native `codex exec resume --last`; do not create a
   repair agent or new thread.
4. Replay the frozen Original selector at budget `4.0`. Include only selected results that were
   blocking, reproducible, and backed by matched executable evidence. Do not manually add the other
   run-all findings.
5. Give that evidence to the same Codex thread. Codex chooses the repair.
6. Freeze the repaired candidate. Re-run the selected verifier against a fresh candidate copy in
   promotion mode, targeting only the failure modes actually contained in the feedback.
7. Promote only if the verifier executes evidence, returns `FIXED_AND_PRESERVED`, and the producer
   source remains unchanged during validation.
8. Run the unchanged official FeatureBench evaluator last. Never expose the solution or evaluator
   files to feedback preparation, Codex, selection, or promotion.

## Outcomes

Report:

- same-thread identity;
- repaired tree/checkpoint and source stability;
- promotion verdict and executed evidence;
- official score before/after;
- continuation and promotion tokens, timeout, and cost completeness;
- regression, unresolved, and infrastructure outcomes.

The success condition for this mechanism test is `same_thread ∧ FIXED_AND_PRESERVED ∧ official
score preserved`. Even if achieved, the causal quality claim is limited to the selected, post-hoc
discovered behaviors on one checkpoint.
