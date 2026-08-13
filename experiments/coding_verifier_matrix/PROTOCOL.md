# Coding verifier matrix pilot protocol

Status: **prospective infrastructure pilot; freeze before producer execution**

Frozen date: 2026-08-13

This pilot is not a positive GRAFT experiment. Its purpose is to determine whether a real coding
benchmark can supply the per-checkpoint verifier outcomes, executable evidence, costs, and official
labels that AgentRewardBench lacks.

## Dataset and task selection

- Dataset: Harbor `featurebench-lite@1.0`, 30 real-repository feature tasks.
- Task ordering: ascending SHA-256 of
  `graft-featurebench-pilot-v1:<task-directory-name>`.
- Resource eligibility: `lv1` only for this smoke; `lv2` tasks are deferred before reading task
  instructions because the smoke is capped to one medium task.
- First eligible task:
  `huggingface__transformers.e2e8dbed.test_processing_wav2vec2.4f660c78.lv1`.
- Public instruction SHA-256:
  `2ddb9470bac789f34edb3c0e2dd2917fe557d34e6aa7deb73c691116378542fa`.
- Task metadata SHA-256:
  `ef631c9e1bfb04044903fef3ab357eda8767242e0e5de7199d4d22f7feecdb89`.

The official solution and evaluator files are never read by the producer, graph builder, selector,
or model verifiers. An Oracle run occurs first only to establish that the independently managed
container and evaluator are healthy. Oracle output is not passed into any later model prompt.

## Runtime

- Harbor `0.20.0`, Docker `27.4.0`.
- Producer and every model verifier: Codex CLI `0.147.0`, model `gpt-5.6-sol`, high reasoning.
- GRAFT source is pinned by full commit in the job configuration.
- No task profile, fixed command, expected answer, benchmark test, or task-specific verifier route.
- Network is available to the producer only under the benchmark runtime. GRAFT model verifiers use
  their declared sandbox and network policy.

## Procedure

1. Before Codex runs, hash and archive the public task-start workspace outside `/app`.
2. Run Native Codex once and freeze its final candidate. GRAFT is not installed as a producer hook
   and cannot continue or modify this candidate.
3. If the source tree is unchanged, record `no_candidate_change` and spend no verifier-model budget.
4. Otherwise, use the frozen GRAFT Original LLM modeler and planner to construct the task-specific
   Behavior–Failure–Verifier graph from the raw instruction, baseline, and candidate only.
5. In shadow mode, execute every dynamically instantiated verifier concurrently, capped at eight.
   If the planner returns more than eight, censor the whole matrix row rather than cherry-pick.
6. Model-review findings are proposals. Record blocking eligibility only when the existing evidence
   guard observes an authoritative, baseline-owned, or directly requirement-derived reproduction.
7. Verify that the producer source hash did not change during shadow verification.
8. Only after all verifier artifacts are closed does Harbor run its official evaluator.

## Pilot outputs

- official candidate score;
- graph, lineage, stage cost, and every verifier verdict/usage;
- eligible reproducible findings versus advisory findings;
- source hashes before and after verifier execution;
- infrastructure failures and censoring reasons.

One task is only a pipeline smoke. It cannot establish selector quality, high-order benefit, or
Codex repair improvement. Scaling requires at least 40 independent checkpoints, at least five
observed verifier families per checkpoint, a task-group-held-out selection split, and a separate
feedback/promotion experiment measuring `official_post_feedback - official_pre_feedback`.
