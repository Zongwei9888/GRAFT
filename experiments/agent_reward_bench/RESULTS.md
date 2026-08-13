# AgentRewardBench frozen result

Status: **primary method gate did not pass**

Date opened: 2026-08-13

This result tests only GRAFT's verifier-selection layer on web-agent judgments. It does not test
Codex repair, executable evidence promotion, or coding-task resolve rate.

## Provenance and audit

- Dataset revision: `b6d17e646009d6cb63d5dd7be78807b680693f61`
- Protocol SHA-256: `34921c29fe0ad7f49f38df2b0ac90b2cb589e3dddb2ca3b3b9d998c1d669291f`
- Matrix SHA-256: `c7383ddb135c66d76dd650f42e775afa00a83428c84c2f3e909bcd569092eeec`
- Audit SHA-256: `9e418d2d434c698604e4167df6b342c7ebd46c35474ed2211bd14b8232b2e5f4`
- Frozen selection result SHA-256: `8e4de48a6f82cce5fd2078e49f267723108ed8dd5fd72d51c554764792ae0276`
- Post-hoc diagnostics SHA-256: `d413026352792437201a3c6a2d99c759c0ae2b8a71ac0fd2e9111a8fd53772b6`

The immutable matrix contains all 1,302 trajectories and 15 judgments per trajectory. One
expert-`unsure` trajectory is excluded before splitting. Eight present but non-binary judge outputs
are retained as abstentions under the frozen policy. The grouped split has no task overlap:

| Split | Rows | Task groups | Expert failures | Expert successes |
|---|---:|---:|---:|---:|
| Development | 209 | 71 | 150 | 59 |
| Test | 1,092 | 380 | 796 | 296 |

## Confirmatory primary result

The primary rule rejects a trajectory when at least one selected judge predicts failure. At the
pre-registered development set-FPR limit of 0.10, no individual judge is feasible. Therefore no OR
portfolio can be feasible, and every cardinality budget from one through four returns
`no_feasible_portfolio`.

Consequences:

- the primary comparison against greedy mutual information cannot be estimated;
- there is no confirmatory evidence that the current high-order selector improves selection;
- the frozen threshold is not relaxed or retuned after observing test outcomes.

## Post-hoc diagnosis

These analyses explain the failure; they do not amend the primary result.

### Correlated misses are real, but the current high-order correction does not help

| Portfolio size | Independence test MAE | Pairwise test MAE | High-order test MAE |
|---:|---:|---:|---:|
| 2 | 0.07875 | 0.01262 | 0.01262 |
| 3 | 0.07352 | 0.01111 | 0.01218 |
| 4 | 0.06240 | 0.01048 | 0.01226 |

Independence substantially overestimates multi-judge recall. Pairwise dependence fixes most of this
aggregate calibration error. The current high-order correction fits the development data more
closely but generalizes worse than pairwise for both triples and quadruples.

The effect is heterogeneous. Pairwise beats independence in only 6 of the 12
benchmark-by-cardinality test strata for sizes two through four. A single unconditional dependence
model is therefore not adequate across task domains.

### The nomination graph is too dense

- 105 possible judge pairs;
- 24 pairs nominated by the frozen lineage-overlap rule;
- 87 pairs nominated by the same-split empirical residual rule;
- 87 union edges;
- 360/455 triples and 1,000/1,365 quadruples receive a high-order correction.

The empirical residual dominates the graph. It is computed on the same small development split
used for model fitting and is confounded by shared task difficulty. The result is almost a global
correction rather than sparse, task-conditional higher-order structure.

### Development set-FPR does not transfer reliably

The development split contains only 59 successes. At set-FPR 0.11, the sole feasible judge reaches
0.1115 on test and is no longer feasible. At set-FPR 0.20, only 2 of 8 development-feasible pairs
remain feasible on test. Point-estimate feasibility is unsafe near the constraint boundary.

A post-hoc consensus exploration can construct development-feasible portfolios, but none of the
best development portfolios for cardinalities two through four remains at or below 0.10 on test.
This is not evidence for changing the primary decision rule.

## Decision

Current method status: **NO-GO for a positive GRAFT selector claim**.

Preliminary measurement status: **promising but incomplete**. The data clearly reject naive
independence in aggregate, while also showing domain heterogeneity, verification false-reject tax,
and high-order overfitting. Confidence intervals, repeated grouped splits, and an independent
coding-verifier dataset are still required before making a WSDM measurement claim.

The next method iteration must be tested as a new protocol rather than silently replacing this
result:

1. nominate candidate dependencies from source lineage only; estimate empirical dependence on a
   separate fold and condition it on task domain/difficulty;
2. use uncertainty-aware set-FPR constraints instead of development point estimates;
3. separate model-review proposals from executable evidence promotion, so a raw LLM opinion cannot
   block Codex;
4. retain pairwise as the strongest current deployable dependence baseline;
5. require a second dataset with per-verifier outcomes, costs, reproducible findings, and repair
   deltas before claiming Codex benefit.

The raw upstream judgments are not redistributed. Reproduction commands are documented in
`README.md` in this directory.
