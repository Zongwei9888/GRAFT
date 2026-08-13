# Prospective coding trial 01

Frozen: 2026-08-13, before any producer, graph builder, verifier, selector, feedback,
promotion, or evaluator output for this task was observed.

## Objective

Test the amended GRAFT pipeline on one task that was not used to develop the evidence
protocol. This is a mechanism replication, not a statistically powered effectiveness
study.

## Task selection

The source dataset is `featurebench-lite@1.0`. Its registry description contains 30
feature-implementation tasks. The selected task is the hard/lv2 CPU task:

`pandas-dev__pandas.82fa2715.test_iceberg.85771c70.lv2`

The task metadata requests 2 CPUs, 8 GB memory, 15 GB storage, a 1-hour agent timeout,
and no GPU. Selection used public task metadata only. Evaluator and solution files were
not inspected.

- public instruction SHA-256:
  `26bb1b0d01df528c86f0390b3bdd6fe97840d3513844128a03d0724e3a599a87`
- public `task.toml` SHA-256:
  `15f7297ee21dcd04011843fd7e4b3dcb5c884090fab409ec0ed9b2c7abf61ab5`

## Frozen producer and GRAFT configuration

- model: `gpt-5.6-sol`
- reasoning effort: `high`
- Codex CLI: `0.147.0`
- GRAFT commit:
  `5d3155f4bc7122fab894294985fad2fb1e4588eb`
- selection policy: Original, budget 4, as materialized by the frozen source
- maximum planned verifiers: 8
- verifier execution: shadow, in fresh candidate copies
- producer feedback during the first job: disabled
- hidden evaluator visibility during graphing, verification, and selection: disabled

## Procedure and decision rules

1. Capture the pristine task tree before Codex runs.
2. Let native Codex produce one candidate in its ordinary agent loop.
3. Freeze the candidate and saved Codex session.
4. Build the behavior/failure/verifier graph from the public requirement and actual
   candidate, then run all planned verifiers in shadow mode.
5. Run the official evaluator only after the shadow matrix is complete. This score is
   the native/shared-prefix outcome.
6. Replay the frozen selector. If it selects no blocking, reproducible result with
   event-matched executable evidence, record a no-feedback outcome and do not create
   evidence manually.
7. Otherwise, freeze a continuation configuration that binds the matrix, candidate,
   and saved session hashes. Return only selected eligible evidence to the same Codex
   thread.
8. Revalidate the repaired state in a fresh copy. Promotion requires matched executed
   evidence and `fixed_and_preserved`; a model claim without matching events is
   `unresolved`.
9. Run the unchanged official evaluator last and compare the shared-prefix and repaired
   outcomes.

## Interpretation boundary

A positive result requires more than a reviewer PASS: the same thread must repair an
eligible observed failure, promotion must succeed mechanically, and official behavior
must be preserved or improve. A zero reward delta on a saturated native candidate is
only a mechanism result. One trial cannot establish graph value; that requires the
later paired multi-task comparison against Native, Run-All, individual ROI, and
pairwise-diversity policies.
