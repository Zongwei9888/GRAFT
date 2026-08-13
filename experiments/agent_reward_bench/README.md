# AgentRewardBench selection-layer experiment

This adapter is deliberately narrower than a coding-agent benchmark. AgentRewardBench contains
expert labels and multiple automatic judgments over the same web-agent trajectories, so it can
test whether a GRAFT-style selector avoids correlated judge failures under token/dollar budgets.
It cannot establish that GRAFT improves Codex repairs or coding-task resolve rate.

The source and dataset revisions are pinned in `manifest.json`. Download those revisions outside
the repository, retain the upstream Terms of Use, then build the matrix:

```bash
python3 -m experiments.agent_reward_bench.build_matrix \
  --annotations /path/to/agent-reward-bench/agent_reward_bench/data/annotations.csv \
  --judgments /path/to/huggingface-snapshot/judgments \
  --output /path/to/output
```

The normal command fails closed unless all 1,302 primary trajectories have all 15 judge columns.
`--allow-incomplete` exists only for schema smoke tests and marks `complete: false`; incomplete
outputs are not paper results.

Outputs:

- `matrix.jsonl`: expert binary label plus each judge prediction, correctness, model/provider,
  tokens and recorded dollar cost;
- `audit.json`: coverage, parse failures, cost availability and pinned provenance.

Before inspecting held-out judge outcomes, the group split, budgets, set-FPR, high-order shrinkage,
lineage nomination rule, primary baseline, and bootstrap procedure were frozen in `protocol.json`.
Run the selection experiment only on a complete matrix:

```bash
python3 -m experiments.agent_reward_bench.run_selection \
  --matrix /path/to/output/matrix.jsonl \
  --output /path/to/output/selection-results.json
```

The development/test split hashes `benchmark + task_id`, so trajectories from different producer
agents for the same task cannot leak across the boundary. Deployable portfolios are selected from
development labels only. `exhaustive_test_oracle` is explicitly evaluator-only; it is never an
online input or a deployable baseline.

No raw upstream trajectories or judgments are redistributed by this repository.
