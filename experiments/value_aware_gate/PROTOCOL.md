# Value-aware GRAFT mechanism pilot — frozen protocol

Status: preregistered before model execution
Date: 2026-08-12
Code candidate: `d5da0d18562dd7b1dd8182c05f7e34f92c24d3c1`

This is a small mechanism and failure-discovery pilot. It cannot establish that GRAFT improves
Codex and it does not replace Gates M1–M3 in `docs/graft-optimization-plan-zh.md`. No result from
this pilot may be used to add a task name, language route, fixture, or answer to product code.

## Frozen order

1. M1 pilot: run the isolated LLM completion gate over the 12 labeled changed-workspace Stop
   cases in `completion_cases.json`, in file order.
2. M2 pilot: ask one Native Codex producer (hooks disabled) to solve the frozen JSONL aggregation
   task, freeze its first delivery checkpoint, run the preregistered held-out evaluator, build one
   value-aware graph, then compare Original and value-aware selection on that identical graph.
3. M3 pilot: only if the M2 online verification returns reproducible continuation evidence, resume
   the same producer thread with exactly that evidence and evaluate promotion. If M2 produces no
   continuation, report M3 as not exercised; do not manufacture feedback after seeing the task.

The producer, completion classifier, task modeler, planner and model verifiers use Codex. The
configured model is `gpt-5.6-sol`; Codex CLI and commit are recorded with every result.

## M1 labels and pilot criteria

The case labels are frozen in `completion_cases.json`. Binary trigger target is
`candidate_complete`; every other state is a No-Op. Primary metrics are binary precision, recall,
accuracy, exact six-state accuracy, duration and token usage.

Pilot success requires:

- no `question` or `blocked` case triggers expensive verification;
- binary precision at least 0.75;
- binary recall at least 0.75;
- every call remains read-only and returns a structured result.

These permissive small-sample criteria only decide whether the implementation is ready for a
50-checkpoint M1 dataset. Formal M1 still requires precision at least 0.90 and the cost/trigger
reductions stated in the optimization plan.

## M2 task and evaluator

The exact producer task is stored in `selector_task.json`. Its held-out evaluator is frozen in
`run_selector_pilot.py` and is not placed in the producer workspace or supplied to GRAFT. It checks:

- aggregation and lexicographic compact JSON output;
- ignored blank lines;
- negative deltas;
- malformed JSON;
- missing, extra and incorrectly typed fields, including booleans as invalid integer values;
- no partial stdout on invalid input.

The graph is built once with producer evidence and used unchanged by both selectors. Hidden
benchmark labels are never available. M2 records selections and modeled values; it does not call a
modeled estimate a causal utility result.

Pilot signals, not pass/fail claims:

- whether value-aware selects No-Op when the held-out evaluator passes and producer evidence is
  strong;
- whether it buys additional evidence when the held-out evaluator finds a defect or evidence is
  incomplete;
- selected nominal cost, predicted net value, coverage and residual risk relative to Original;
- whether the online decision returns actionable reproducible feedback.

## M3 rule

If continuation occurs, the exact pre-feedback checkpoint and report are retained. The same Codex
thread receives only GRAFT's evidence packet. The repaired state is evaluated with the frozen
held-out evaluator, then subjected to the value-aware promotion requirement. Record
`fixed_and_preserved`, `not_fixed`, `regressed`, or `unresolved` without reinterpretation.

## Analysis restrictions

- Do not inspect or alter a case label after its prediction.
- Do not edit GRAFT between arms.
- Do not add post-hoc retries except an infrastructure failure before any valid model output; all
  retries must be reported.
- Do not run a new Terminal-Bench treatment from this pilot.
- A zero verifier selection is a No-Op result, not a correctness proof.
- A passing evaluator is task evidence, not evidence of general GRAFT effectiveness.
