# Value-aware shared-prefix pilot 01

Status: prospectively censored before treatment. The official Harbor 0.20.0 registry could not
resolve the frozen Terminal-Bench 3 revision/digest or the selected task, so no producer, GRAFT
verifier, checkpoint replay, or Native control was run for this pilot.

## Censoring record (2026-08-12)

The required first command, the official oracle sanity check, failed during dataset resolution and
before Harbor created a task environment:

```text
ValueError: Digest 'sha256:88433fbcecd1a3f81f7a71bff4cc76c394d0edbefb7e028f90d4109b639fefba'
not found for dataset terminal-bench/terminal-bench-3
```

Queries for `latest`, revision `10`, revision `9`, and `head`, plus direct resolution of
`terminal-bench/wal-recovery-ordering`, also failed. The public Harbor Hub still listed
Terminal-Bench 3, and the installed Harbor 0.20.0 matched the latest public release. Per the frozen
rules below, this is an infrastructure censoring event; no substitute task is used to estimate the
prospectively registered outcome.

After recording the censoring event, a separate **post-hoc infrastructure smoke** uses the locally
cached official `cancel-async-tasks` task. Its purpose is limited to checking that the external
value-aware profile is installed, GRAFT runs at a Codex completion boundary, checkpoints and state
are exported, and any eligible finding is returned to the same Codex thread. Its score is not a
replacement pilot result, a generalization result, or evidence that the selector improves coding
quality. The smoke uses the unchanged GRAFT commit and official task evaluator; the online GRAFT
stages do not read the evaluator implementation or output.

## Frozen method

- GRAFT source commit: `692e75660f707711dbfaf8cbc8fd7d5f72c709ff`
- Codex CLI: `0.147.0`
- Producer/modeler/planner/verifier model: `gpt-5.6-sol`
- Producer reasoning effort: `high`
- Policy: domain-neutral `graft-value-aware`
- Verifier network access: disabled
- No task profile, repository command, fixture, expected answer, task name, or hidden evaluator is
  present in GRAFT product code or configuration.

The value-aware policy is materialized outside `/app` and matches only the generic Harbor workspace
path. The target workspace receives no `.graft/config.json`. The external policy changes budgets,
completion gating, selection, cost accounting, No-Op, and promotion protocol only; it contains no
task semantics.

## Dataset and deterministic task selection

```text
dataset: terminal-bench/terminal-bench-3
revision: 10
dataset digest: sha256:88433fbcecd1a3f81f7a71bff4cc76c394d0edbefb7e028f90d4109b639fefba
```

The eligible names are the remaining tasks from the earlier frozen unseen-name list that have not
been executed in a recorded GRAFT treatment:

```text
batched-eval-parity
kv-live-surgery
wal-recovery-ordering
```

Hashing the dataset digest without `sha256:` followed by
`:value-aware-shared-prefix-1` gives:

```text
a317f79754a4b50a7ec921f335c62bf4bff16ba0f3219c54fd23af348e493b31
```

Interpreting that digest as an integer modulo three selects sorted index two:

```text
terminal-bench/wal-recovery-ordering
```

If the official oracle is not 1.0 because of task/environment infrastructure, the run is censored
and no method conclusion is drawn. This pilot does not substitute a different task after seeing a
treatment result.

## Frozen execution order

1. Run the official oracle sanity check.
2. Run one Codex + GRAFT value-aware treatment with external checkpoint capture.
3. Replay every captured pre-feedback checkpoint through the unchanged official evaluator.
4. Run one matched Native Codex condition only as a descriptive overall comparison.
5. Inspect evaluator details only after all scored conditions finish.

The treatment and Native conditions pin the same dataset, task, Codex CLI, model, and reasoning
effort. GRAFT model calls, verifier calls, continuations, and promotion checks are treatment costs.

## Outcomes

Primary same-trajectory outcome:

```text
delta_feedback = official final treatment score - official first-Stop checkpoint score
```

- positive: feedback causally helped this trajectory;
- zero: feedback was score-neutral and any extra work is cost-only;
- negative: feedback causally harmed this trajectory.

Secondary outcomes are official passed/failed subtests, whether the selector chose No-Op, verifier
selection and lineage, finding eligibility, feedback rounds, same-thread continuation, promotion
state, checkpoint hashes, wall time, tokens, known model cost, and unknown-cost stages.

The independent Native difference is not called causal because it samples a different trajectory.
One task is a mechanism/smoke result, not an effectiveness estimate and not a WSDM claim.

## Integrity rules

- Hidden evaluator source and outputs are unavailable to GRAFT online stages.
- Checkpoint replay is evaluator-only and validates archive SHA-256 plus full checkpoint key.
- The frozen GRAFT commit is not changed or repointed after any result.
- Infrastructure defects may be diagnosed, but a rerun after a code change is post-hoc and cannot
  replace this prospective result.
- A run without a captured verification-eligible checkpoint cannot estimate `delta_feedback` and
  is reported as a measurement failure or a genuine No-Op boundary, as applicable.
