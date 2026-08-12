# Value-aware post-hoc smoke 01

Status: completed on 2026-08-12. This is a post-hoc integration and mechanism
smoke, not a replacement for the prospectively censored Terminal-Bench 3 pilot
and not an effectiveness estimate.

## Question

Can the source-pinned, profile-free value-aware GRAFT plugin run at a real
Codex completion boundary, construct a task-specific feedback graph without a
hand-authored task profile, capture the first-Stop candidate, and preserve
enough information for independent official-evaluator replay?

## Frozen inputs

- Task: the locally cached official `cancel-async-tasks` task.
- Task checksum: `283c70ca90688dc09a969d24e3ed137ba0f00d23018df68771bdf86526b82047`.
- GRAFT source: `692e756f029b1e0a9ca4cf911459dfef79381a22`.
- Codex CLI: `0.147.0`.
- Producer, modeler, planner, and candidate verifier model family:
  `gpt-5.6-sol`.
- Producer reasoning effort: `high`.
- Selection policy: `graft-value-aware` / `value-aware-hypergraph`.
- Nominal verifier budget: `4.0`.
- Task-epoch wall-time budget: `120 s`.
- Task-epoch model-cost budget: `$1.00`.
- No `.graft/config.json`, task profile, task name, task command, fixture,
  expected answer, or hidden-evaluator output was supplied to `/app`.

The unchanged official oracle first scored `1.0`, establishing that the cached
task and evaluator could execute. The first oracle attempt was an infrastructure
failure because its evaluator tried to fetch `uv` from GitHub while that host
was unreachable; the successful sanity run used Harbor's environment network
configuration and changed neither the task nor the evaluator.

## Scored result

| Condition | Official score | Exception | Interpretation |
| --- | ---: | ---: | --- |
| Official oracle sanity check | 1.0 | no | Task environment is executable |
| Codex + value-aware GRAFT final candidate | 1.0 | no | End-to-end treatment completed |
| Exact first-Stop checkpoint replay | 1.0 | no | Producer candidate was already correct before GRAFT feedback |

The same-trajectory outcome is therefore:

```text
delta_feedback = final treatment score - first-Stop checkpoint score
               = 1.0 - 1.0
               = 0.0
```

This is a cost-only result. It is not a positive GRAFT effect, but it also does
not show a code-quality regression on this trajectory. A separately sampled
Native control is unnecessary for this causal statement because checkpoint
replay evaluates the exact pre-feedback source state.

## What GRAFT actually did

At the producer's first completion boundary, GRAFT:

1. classified the turn as a candidate completion;
2. bound the raw requirement and actual `run.py` workspace to checkpoint key
   `6ebbb9b29c7e40ad57ebb342ec880e92d219eb556326e0052a905811a281ee38`;
3. used the LLM modeler to derive 6 task behaviors and 13 potential failure
   modes;
4. used the LLM verifier planner to propose 7 task-specific verifier instances;
5. represented 5 higher-order shared blind spots and 15 uncertainties or
   coverage gaps; and
6. captured a replayable archive before any possible continuation.

Examples of the dynamically derived semantics include bounded concurrency,
completion of all submitted jobs, empty-input behavior, cancellation cleanup,
and preventing queued jobs from starting after cancellation. Candidate
verifiers included repository-evidence agents, semantic contract and
cancellation reviewers, agentic runtime probes, and an adversarial lifecycle
test agent. These were generated from the raw task and workspace; none was a
hard-coded `cancel-async-tasks` rule.

However, GRAFT selected and executed **zero** verifiers. It emitted an
`unresolved` decision, sent no evidence feedback to the producer, created no
promotion obligation, and allowed Codex to finish. This was not a semantic
No-Op decision: the selector evaluated zero candidates because none was
resource-feasible.

## Root cause of the zero-verifier outcome

The configured `120 s` wall-time budget covers completion gating, graph
construction, planning, and verifier execution together. Before selection:

| LLM stage | Wall time | Input tokens | Output tokens |
| --- | ---: | ---: | ---: |
| Completion gate | 11.160 s | 15,054 | 61 |
| Behavior/failure-mode modeler | 68.515 s | 15,727 | 2,470 |
| Verifier planner | 88.405 s | 19,318 | 3,981 |
| **Total before verifier execution** | **168.108 s** | **50,099** | **6,512** |

After the completion gate, the controller received about `108.84 s` of wall
budget. Graph construction and verifier planning then used about `156.92 s`,
leaving zero. Every proposed verifier had a predicted duration between `20 s`
and `80 s`, so all seven failed the resource-feasibility gate. The reported
reason was:

```text
No verifier was feasible inside the remaining task-epoch resource budget.
GRAFT did not make a No-Op value judgment for this checkpoint.
```

All three LLM stages also reported token counts but no dollar-cost estimate, so
the session recorded three unknown-cost stages. That is a cost-observability
problem, but the immediate cause of infeasibility in this run was exhausted
wall time.

## Checkpoint integrity and replay

The captured archive contains `run.py` and `checkpoint.json`, reports no skipped
files, and has SHA-256:

```text
2ebaaa1e5e1e9ba52e58eb62978764fa09b950c74ab7e12553416741fa77e030
```

The replay adapter independently checked both that digest and the full
checkpoint key before restoring the source into a fresh task container. It did
no coding and invoked no model. The unchanged official evaluator scored that
restored first-Stop state `1.0`.

## Runtime cost

- Producer plus GRAFT agent-execution phase: about `272.5 s`.
- GRAFT task-epoch stages: about `168.1 s` of that phase.
- Official Harbor producer accounting: 95,238 input tokens, 79,360 cached input
  tokens, 3,517 output tokens, and `$0.22458`.
- GRAFT separately observed 50,099 stage-input tokens and 6,512 stage-output
  tokens, but dollar cost was unavailable and is therefore not silently folded
  into the Harbor number.
- Environment agent setup took about `514 s`, dominated by installing the
  pinned Codex CLI; this is deployment overhead rather than selection quality.

## Engineering findings fixed during the smoke

Three adapter defects were found before the completed treatment:

1. the expanded GRAFT commit SHA in the frozen config was incorrect;
2. installation unnecessarily attempted `apt` plus a Git clone in the task
   image; and
3. archive validation checked the plugin manifest at the repository root
   instead of `plugins/graft/.codex-plugin/plugin.json`.

The adapter now installs an exact commit archive without requiring Git and
validates the actual plugin manifest path. These changes affect reproducible
installation only; they do not alter the frozen GRAFT algorithm commit used in
the task container.

## Conclusion and next decision

The smoke validates the integration path, dynamic LLM graph construction,
external profile loading, source-bound checkpoint capture, and independent
first-Stop replay. It does **not** validate the claimed benefit of value-aware
selection, because no verifier ran and no feedback occurred.

Before another effectiveness run, the resource policy must reserve execution
budget before invoking the modeler/planner, or bound those stages so at least
one predicted verifier can remain feasible. That change must receive unit tests
and a new source commit, then be evaluated under a newly preregistered task or
task set. This completed post-hoc result remains immutable and must not be
relabelled as evidence for the revised policy.
