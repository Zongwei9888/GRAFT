# Protocol amendment 01 — strict response schema infrastructure failure

Date: 2026-08-12

M1 completed before this amendment and remains unchanged. The first M2 attempt produced and
archived a valid Native Codex checkpoint, and the frozen held-out evaluator passed 15/15 checks.
Before either selector ran, the real Codex verifier-planner request failed with HTTP 400 because
`verifier_plan.schema.json` declared `value_estimate` and `revalidates_feedback` properties without
including them in the strict object's `required` array. The same audit found that
`verifier_verdict.schema.json` omitted `promotion_outcome` from `required`.

This is classified as a pre-selection integration failure, not a GRAFT outcome. The correction is
limited to strict structured-output schema conformance, the matching nullable-field instruction,
and a recursive regression test. No task, label, hidden evaluator case, selection parameter,
candidate implementation, or result threshold changes.

M2 retry 1 must restore the exact archived first checkpoint and producer JSONL rather than ask
Codex to generate another implementation. It will rerun the frozen held-out evaluator, construct
the first valid graph, and compare both selectors only if the recovered checkpoint key is exactly
`166539c6a22ff69aaf0481fa556c47f8ffc84a32fe7a201c994bc61da7eecb3f`. The failed attempt remains
reported in `results/m2_attempt_01_infrastructure_failure.json`.
