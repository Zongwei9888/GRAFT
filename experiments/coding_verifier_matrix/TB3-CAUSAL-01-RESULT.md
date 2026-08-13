# Terminal-Bench 3.0 causal matrix trial 01 result

Status: **completed; correct No-Op, zero reward delta, no positive effectiveness claim**

Date: 2026-08-13

Protocol: [TB3-CAUSAL-01.md](TB3-CAUSAL-01.md)

## Outcome

| Outcome | Frozen first candidate | GRAFT continuation path |
|---|---:|---:|
| Official Terminal-Bench reward | 1.0 | 1.0 |
| Codex resumed | no | no |
| Source/checkpoint changed | yes, from task start | no |
| Eligible selected feedback | — | none |
| Promotion | — | not requested |

Therefore:

```text
delta_feedback = 1.0 - 1.0 = 0.0
```

This is a safety/instrumentation success and an effectiveness null result. GRAFT did not improve an
already correct benchmark candidate. It also did not damage it or spend a repair-agent turn.

## Source-bound causal chain

- task: `terminal-bench/cli-2ph-simplex` from official Git tag `v3.0.0`;
- task commit: `2b0442c3c583b710ca8da14c8e601b99f2f1f244`;
- task checksum: `60b2150c58190978991251ad6b8773dd078710aeadbcb522800fab8526f3fccf`;
- producer thread: `019ff931-40e9-76b2-af67-fd72fe85b16e`;
- first-candidate tree: `d3d40cdf449e2715b6b69194548f19ac9180f84f050a64b307baa1577cb76dca`;
- checkpoint key before and after replay:
  `51e572e48f6f44de0dd19b0846b07a786a391656bc4751a8be821bfa838ded31`;
- matrix SHA-256:
  `e39d1cea40ef5da4f6f9a3562cf21cfd5ed95ea13dd909b52b702f7392ec350a`;
- candidate archive SHA-256:
  `3962ec6b5c05ac75a796d3e268d0281a0e46c17683f534502c089b1f683e7c6e`;
- producer session SHA-256:
  `9db60a7edff349ad8ec82dee0270013ec93d3733ff63405907c402d5929f5eec`.

The continuation adapter validated all three input digests, restored the exact candidate, and
returned `no_eligible_feedback`. Harbor recorded no model tokens for that arm, agent execution took
1.06 seconds, metadata recorded `promotion_status=not_requested_no_eligible_feedback`, and the
unchanged official evaluator again returned `1.0`.

## What Native Codex already did

Before GRAFT saw the candidate, Codex used 1,872,779 input tokens (1,779,712 cached), 36,249 output
tokens, and USD 2.442661 according to Harbor. It implemented the two-phase solver and independently
ran feasible, infeasible, unbounded, degenerate, random, shortest-pivot, pivot-replay, schema, CLI,
and exception-output checks. This confirms the hard experimental baseline: GRAFT must add
*complementary* evidence rather than count more same-lineage checking as value.

## What the dynamic GRAFT matrix did

The LLM modeler and planner created:

- 13 Behaviors;
- 11 Failure Modes;
- 7 task-instantiated verifiers;
- 6 high-order shared-blind-spot edges.

The producer tree remained byte-identical throughout all shadow runs. Behavior/planner modeling
used 125,645 input tokens (76,032 cached), 8,438 output tokens, and 184.9 summed seconds. The seven
shadow verifiers used 1,026,506 input tokens (828,928 cached), 18,248 output tokens, and 696.5 summed
verifier-seconds; their concurrent matrix occupied about 6m46s wall time. This full run-all cost is
measurement overhead, not the cost of the selected online set.

The frozen Original selector chose:

```text
adversarial-simplex-test + runtime-phase-boundary-audit
nominal cost = 3.5
expected coverage = 0.009952
residual risk = 0.990048
```

This is direct evidence that the Original objective is still willing to select a very low-coverage
set. It is not a value-aware No-Op decision. The later evidence gate, rather than the graph
objective, prevented feedback.

## Findings and evidence gate

`adversarial-simplex-test` observed that a bounded one-variable LP with objective coefficient
`1e-10` was treated as already optimal at zero because the implementation used an absolute `1e-9`
reduced-cost tolerance. A semantic reviewer independently reported the same family with `5e-10`.
The adversarial verifier also questioned the basic-variable report for redundant equality rows.

These findings did not become online feedback:

- the semantic reviewer was intentionally non-blocking;
- the selected adversarial result reported its self-contained `python3 -c` reproductions inside a
  `bash -lc` transport wrapper;
- the frozen portability rule rejected every shell wrapper, even though it already rejected
  compound shell programs and could structurally identify a single inner argv;
- the selected phase-boundary verifier passed;
- the shortest-path verifier timed out.

The tiny-coefficient case is a plausible public-contract robustness defect, but it was outside the
official evaluator's exercised boundary because the exact candidate scored `1.0`. The redundant-row
basis interpretation is less certain. Neither may be called a benchmark failure.

## Scientific interpretation

This trial supports four narrow claims:

1. the matrix harness now works on a non-Git Terminal-Bench task directory;
2. the source/session/hash boundary and exact-candidate replay work;
3. GRAFT can generate complementary public-requirement counterexamples after a very thorough Codex
   producer believes it is finished;
4. the evidence gate can abstain and avoid an unnecessary same-thread repair.

It does **not** show a reward gain, verifier-selection advantage, or positive effect from the graph.
The graph selected a set, but no selected evidence was fed back; high-order edges therefore have no
causal reward attribution in this row. The run also confirms that run-all LLM verification is far
too expensive for product use.

## Post-hoc engineering consequence

For future unseen tasks, a shell transport is now safely unwrapped only when it contains one
parseable command with no pipeline, sequencing, redirection, subshell, or command substitution. The
inner argv must then pass the same frozen-file/inline-program portability check. This general rule
does not alter this completed trial; its result remains No-Op. Regression tests cover portable
inline programs, nonportable generated scripts, compound commands, and environment-prefixed shell
payloads. A new prospective task is still required before this change can support an effectiveness
claim. See [AMENDMENT-03.md](AMENDMENT-03.md).
