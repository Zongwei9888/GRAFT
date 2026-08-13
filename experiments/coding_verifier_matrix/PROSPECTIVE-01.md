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

## Frozen continuation decision

The shared-prefix run completed before this section was added. The Original policy at
budget 4 selected `adversarial-interface-test` and
`agentic-optional-provenance`; both had blocking, reproducible, event-matched evidence.
Therefore step 7 is enabled without adding any unselected finding.

- source matrix SHA-256:
  `52867961605f4cad17b08ae73608b21803643c47f40ee98992fb7b392abf735c`
- candidate archive SHA-256:
  `91d819befc7a9f0afb9aee9ce140c0fd4fa8a454b8f9369600ff9fb67d47f8b9`
- producer session SHA-256:
  `8b158584a71a0fee91efcac213119a50a077bb973b06fc9f2aabbd79fd93b3b2`
- producer thread:
  `019ff905-92c7-7800-af45-7b46be07848a`
- candidate tree:
  `c2e065ff3c621a9a543a62555ca4d9838cacbe8f5f477222844ac190fb4d3c00`
- feedback checkpoint:
  `7ffa319bca17118f247913759d2c31b1623f923b5f16ac512e098d2641a1d6f5`
- feedback SHA-256:
  `6f5e0447eda847a621525e4aa17de2f2bc2ec0433164b88724a710ce2bfa11c1`
- continuation runtime commit:
  `bebd552613c02c92f370e4a6b8ac71eef89059bd`

The shared-prefix official reward is `1.0`. That score was not included among the
artifacts used to prepare feedback or promotion.
