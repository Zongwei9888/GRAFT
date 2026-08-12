# Value-aware GRAFT mechanism pilot — results

Date: 2026-08-12

This pilot tests mechanisms and discovers implementation failures. It does not establish that
GRAFT improves Codex, and it is not a Terminal-Bench result. Protocol and amendments are in
`PROTOCOL.md`, `AMENDMENT-01.md`, and `AMENDMENT-02.md`.

## Frozen environment

- Candidate before experiment fixes: `d5da0d18562dd7b1dd8182c05f7e34f92c24d3c1`
- Preregistered protocol: `c02ea9b3db32e418d019dd0483855bb6d5d7ae08`
- Strict-schema fix used by the first valid M2 graph: `efbc19f9637cf6dd283b7a851590e5377599f051`
- Codex: `codex-cli 0.147.0`
- Model: `gpt-5.6-sol`

## M1 — lifecycle completion gate

The isolated completion classifier ran on 12 labeled, changed-workspace Stop cases: four complete
candidates, two intermediate states, two user questions, one explanation-only turn, two blocked
turns, and one ambiguous abstention.

| Metric | Result |
|---|---:|
| Binary trigger precision | 1.00 (4/4) |
| Binary trigger recall | 1.00 (4/4) |
| Binary accuracy | 1.00 (12/12) |
| Exact six-state accuracy | 1.00 (12/12) |
| Question/blocked false triggers | 0 |
| Structured result rate | 1.00 |
| Total duration | 114.648 s |
| Mean duration | 9.554 s/Stop |
| Known tokens | 164,873 |

The small pilot meets its preregistered threshold and supports the lifecycle mechanism: GRAFT can
be placed at candidate delivery boundaries without verifying explicit questions, intermediate
work, or external blockers in these cases. It also shows that a separate LLM call at every changed
Stop has material cost. The 12 authored cases are too small and too clean to estimate deployment
accuracy; the planned 50-checkpoint M1 dataset remains necessary.

## M2 attempt 1 — real interface failure

Native Codex produced one JSONL aggregation CLI with hooks disabled. Its archived first checkpoint
passed all 15 frozen held-out evaluator checks. Before selector execution, the Codex verifier
planner request failed with `invalid_json_schema`: the new schema declared optional-looking object
fields without listing them in the strict object's `required` array.

This attempt is an infrastructure failure, not a treatment outcome. It exposed that 100 fake-runner
tests did not exercise the real Codex structured-output validator. The response schemas were fixed,
a recursive strict-schema regression test was added, the plugin runtime was synchronized, and the
same producer checkpoint—not a new stochastic implementation—was restored for retry 1.

## M2 retry 1 — same graph selection

The restored checkpoint key was exactly
`166539c6a22ff69aaf0481fa556c47f8ffc84a32fe7a201c994bc61da7eecb3f`; its held-out evaluator again
passed 15/15. One graph containing 9 behaviors, 6 failure modes, 7 verifier candidates, and 5
higher-order shared-blind-spot scenarios was built and held fixed for both selectors.

| Arm on identical graph | Selection | Nominal cost | Candidates evaluated |
|---|---|---:|---:|
| GRAFT Original | `adversarial-test-1`, `repo-evidence-1` | 3.25 | 20 |
| Value-aware online before safety fix | empty | 0 | 0 |
| Value-aware, post-hoc without resource gate | No-Op | 0 | 14 |
| Value-aware, post-hoc verifier-only 120 s gate | No-Op | 0 | 14 |

The two modeling calls consumed 70.774 s and 107.124 s: 177.898 s total before verifier execution,
against a 120 s task-epoch wall budget. Consequently the online selector had no resource-feasible
candidate. Before the safety fix it mislabeled that empty set as a value-aware No-Op and the
controller returned `ALLOW`.

The execution-free post-hoc diagnostic is important but limited. On the identical graph, with only
the resource-feasibility constraint removed, all seven singleton net values were negative (from
-0.1147 to -0.3119), the selector evaluated 14 candidate choices, and it still selected No-Op.
That supports the intended mechanism on this passing, strong-producer-evidence checkpoint: the
value-aware objective can decline redundant verification that Original would buy. It does not show
that the online system was safe or that quality improved.

The product now distinguishes an evaluated No-Op from resource exhaustion. Replaying the same
frozen graph after the fix gives `feasible=false`, `no_op=false`, and controller `UNRESOLVED` when
the graph has already consumed the wall budget.

## M3 — promotion

Not exercised. The frozen M2 checkpoint produced no reproducible GRAFT failure, so the protocol
forbade manufacturing feedback merely to force a continuation. Unit tests cover promotion
mechanics, but this pilot supplies no real-Codex evidence for repair effectiveness or regression
prevention.

## What this pilot supports

1. The semantic lifecycle trigger worked on the 12 frozen cases and avoided question/intermediate
   false triggers.
2. On one already-correct checkpoint, the intrinsic value-aware selector chose No-Op while Original
   selected two redundant verifiers on the same modeled graph.
3. The experiment found and fixed two integration/safety defects that mocked unit tests missed:
   strict response-schema incompatibility and resource exhaustion masquerading as No-Op evidence.

## What it does not support

- It does not show GRAFT outperforming Native Codex.
- It does not measure whether GRAFT discovers a defect, produces a successful repair, or prevents
  a regression; M3 did not occur.
- It does not demonstrate statistical significance, calibration, or generalization to unseen task
  families.
- It does not solve the current cost problem: completion classification plus two serial modeling
  calls can exceed the default online budget even when no verifier runs.

## Required next experiment

Do not start a broad Terminal-Bench claim yet. First complete formal M1 with at least 50 naturally
collected multi-turn checkpoints and measure classifier calibration and trigger-cost reduction.
Then run a prospective multi-task M2/M3 dataset containing both correct and defective first
checkpoints, caching one graph per checkpoint for selector replay. Report separately:

- trigger cost;
- behavior/planner graph cost;
- verifier execution cost;
- continuation cost;
- hidden-evaluator quality delta;
- false feedback and regression outcomes.

The next optimization target is a cheaper pre-graph value screen or progressive graph construction;
changing the selector alone cannot recover the 177.898 seconds already spent before selection.
