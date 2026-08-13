# Terminal-Bench 3.0 causal matrix trial 01

Status: **prospectively frozen before agent, verifier, solution, or evaluator execution**

Frozen date: 2026-08-13

This is a one-task causal mechanism trial, not a powered effectiveness study. Its purpose is to
measure whether evidence selected by dynamic GRAFT improves or harms the exact candidate that a
Codex producer had already chosen to submit.

## Frozen benchmark and task

- Harbor: `0.20.0`.
- Dataset: `terminal-bench/terminal-bench@3.0.0`.
- Official Git tag commit: `2b0442c3c583b710ca8da14c8e601b99f2f1f244`.
- Task: `terminal-bench/cli-2ph-simplex`.
- Public instruction SHA-256:
  `77008a66eefbacbe8c684df063bc7f285834b7e6f2c59e3e451d5ea676a90b45`.
- Public task metadata SHA-256:
  `6db2ad834371b29367fca69a9c0bc62b29a8d9658d7fceac7083f6bdbbc6058f`.
- Public task-implementation rubric SHA-256:
  `9e6aa64bc57fb7bb66e5caefe942da2959e4def91831e8e292b96efec26adb58`.
- GRAFT source commit:
  `22d4510a426501fbf4e517c00fa24d2e98a0f9f1`.
- Producer and verifier model/runtime: `gpt-5.6-sol`, high reasoning, Codex CLI `0.147.0`.

The task was selected from public metadata because it is a one-CPU, standard-library software
task with several interacting behavioral requirements and was not used in the earlier GRAFT
pilots. No solution, test, verifier implementation, leaderboard result, or agent trajectory was
inspected when selecting it. Public instructions are legitimate producer input.

Terminal-Bench 3.0 has binary task reward. Per-check test counts are descriptive only; the primary
endpoint is the official binary reward difference between the frozen first candidate and its
same-thread continuation.

## Information boundary

The producer, GRAFT graph builder, selector, and model verifiers may access only:

- the public task instruction;
- task-start files visible to the producer;
- the frozen candidate and its change from task start;
- commands and runtime behavior available in the agent environment.

They never read the official solution, evaluator source, private tests, oracle output, or final
reward. The official evaluator runs after each candidate is closed. Any Oracle run is an
infrastructure check only and its output is not passed to a model or selector.

## Fixed procedure

1. Resolve the task workspace from the environment. Prefer the closest Git root enclosing the
   current task directory; support a non-Git task directory; reject `/` as unsafe.
2. Hash and archive task-start files outside the workspace.
3. Run Native Codex once. GRAFT is not installed in the producer thread and cannot affect this
   first candidate.
4. Freeze the candidate source and original Codex JSONL session before verification.
5. Build the LLM-generated Behavior--Failure--Verifier--Lineage graph. Run the complete planned
   verifier matrix only in disposable workspace copies, with at most seven planned verifiers and
   four concurrent verifier processes. The producer workspace must remain byte-identical.
6. Let the unchanged official Terminal-Bench evaluator score the frozen first candidate. This is
   the causal pre-feedback outcome.
7. Before continuation, freeze matrix, archive, session, checkpoint, and graph hashes. Replay the
   Original selector at nominal budget `4.0` without looking at the official outcome.
8. If there is no selected failure backed by a portable direct-argv reproduction, return No-Op:
   do not resume Codex and submit the frozen candidate unchanged.
9. Otherwise, restore the exact candidate and exact Codex session in a fresh task container. Feed
   only the selected, evidence-covered failure modes to that same thread. Codex decides how to
   repair them.
10. Re-run only the selected verifier evidence in promotion mode. A model assertion without a
    matched command event cannot promote the repair. Submit the final candidate to the unchanged
    official evaluator even if promotion is unresolved, while recording the promotion state.

Verifier-created temporary scripts are not transportable evidence. Commands referencing files
must reference files present in the frozen candidate, while a self-contained single-process
`python -c` command may be portable. Explicit task-contract/runtime conflicts require abstention
unless an allowed authoritative boundary resolves them.

## Frozen outcomes and interpretation

Primary:

```text
delta_feedback = official_reward_after_continuation
               - official_reward_of_frozen_first_candidate
```

Secondary:

- exact same-thread identity and source hashes;
- selected failure modes and portable reproductions;
- promotion verdict and source stability;
- graph and high-order lineage structure;
- model tokens, wall time, timeouts, and nominal selected cost;
- full run-all matrix cost, reported separately as experimental measurement overhead.

Interpretation is fixed:

- `+1`: preliminary positive causal repair evidence on one task;
- `0` with No-Op: correct abstention, but no effectiveness evidence;
- `0` after feedback: mechanism activity without benchmark-value evidence;
- `-1`: feedback-induced regression and a method failure;
- infrastructure or evaluator failure: censored, not assigned a method score.

One task cannot establish WSDM-level effectiveness or graph causality. A positive result advances
the method to a pre-registered multi-task shared-prefix study. A zero or negative result triggers
mechanism diagnosis before any larger run.

## Infrastructure amendment 01

Frozen before any task container, Oracle, producer, or verifier executed: Harbor `0.20.0` failed
while resolving the Hub package `terminal-bench/terminal-bench@3.0.0` because one returned
`task_version.package` value was null. The failure occurred during dataset metadata parsing.

The transport is therefore changed to Harbor's official Git-registry path
`harbor-framework/terminal-bench@v3.0.0`, which resolves the already frozen Git commit above and
discovers tasks from its `tasks/` tree. The task, public bytes, model, method, outcomes, and
information boundary are unchanged. The failed package-resolution launch is an infrastructure
attempt, not a trial.

## Frozen continuation inputs

Frozen after the first-candidate matrix and official score were closed, before any continuation
container was launched:

- producer thread: `019ff931-40e9-76b2-af67-fd72fe85b16e`;
- producer session SHA-256:
  `9db60a7edff349ad8ec82dee0270013ec93d3733ff63405907c402d5929f5eec`;
- first-candidate tree:
  `d3d40cdf449e2715b6b69194548f19ac9180f84f050a64b307baa1577cb76dca`;
- checkpoint key:
  `51e572e48f6f44de0dd19b0846b07a786a391656bc4751a8be821bfa838ded31`;
- candidate archive SHA-256:
  `3962ec6b5c05ac75a796d3e268d0281a0e46c17683f534502c089b1f683e7c6e`;
- matrix SHA-256:
  `e39d1cea40ef5da4f6f9a3562cf21cfd5ed95ea13dd909b52b702f7392ec350a`;
- official first-candidate reward: `1.0`.

The frozen Original selector chose `adversarial-simplex-test` and
`runtime-phase-boundary-audit`, but no selected result satisfied the already-frozen portable,
blocking, reproducible evidence rule. The continuation policy is therefore No-Op. The replay run
must not resume Codex; it restores and submits the exact frozen candidate solely to validate the
causal artifact path.
