# Terminal-Bench 3.0 environment-branch trial 01

Status: **prospective; frozen before reading the selected instruction or running any model**

Frozen date: 2026-08-13

Infrastructure amendment: [AMENDMENT-04.md](AMENDMENT-04.md)

## Objective

Measure whether GRAFT can discover an eligible failure on an untouched Native Codex candidate and,
when it does, improve the official Terminal-Bench reward through same-thread feedback. This first
row is also the end-to-end smoke for one full task environment per verifier.

## Dataset and selection

- Dataset source: `harbor-framework/terminal-bench@v3.0.0`.
- Resolved tag commit: `2b0442c3c583b710ca8da14c8e601b99f2f1f244`.
- Previously attempted or inspected pilot tasks are excluded.
- Public metadata eligibility: no GPU, at most 2 CPUs, at most 8192 MB memory, verifier timeout at
  most 900 seconds, and agent timeout at most 7200 seconds.
- Filesystem-containment proxy: the public task tree has one environment Dockerfile and no Compose
  file. Tasks known to require producer-mutated external services are excluded from the primary
  effect estimate.
- Eligible task order is ascending SHA-256 of
  `graft-tb3-filesystem-cohort-v1:<task-name>`.
- First eligible task: `vf2-speedup-networkx`.
- Task metadata SHA-256:
  `6410b31d9c4835b7da164d249b0e259066fb9e12284742614ba745df0f5c72e0`.
- Public root-tree response SHA-256:
  `20ab17ad2e56b6c39a3849a6325b6e6f7a6c63798152c01dbefd88d8350a6775`.
- Public environment-tree response SHA-256:
  `ed3524a89aa635f42940d7abe4bd3dabace3adaceded598cc31ecac53e621089`.

The task metadata and file names were read to freeze eligibility. The instruction, solution, cheat,
and evaluator contents were not read. After this file and the candidate job config are committed,
Harbor may deliver the public instruction to Native Codex.

## Fixed runtime

- Harbor `0.20.0` and Codex CLI `0.147.0`;
- model `gpt-5.6-sol`, high reasoning;
- GRAFT commit `42e3cdebc476a6f4e6a16033ed7e8ecfd9c9f193`;
- at most eight dynamically planned verifiers;
- no task profile, task-specific command, expected output, hidden evaluator, or hard-coded verifier;
- GRAFT Original graph and selection policy remain frozen; Amendment 04 changes isolation only.

## Phases and outcomes

1. Run `CandidateCaptureCodex`. Record the official reward, candidate/session hashes, token usage,
   duration, and cost. This is the only native first-candidate score.
2. If the candidate is replayable, restore it in a fresh task environment and dynamically build the
   graph. Commit the plan hash before running verifier branches.
3. Run every planned verifier in a separate fresh task environment. Assemble only exact checkpoint
   matches and record all verdicts/costs.
4. Replay the frozen Original selector under its nominal budget. If no selected result contains
   eligible executable evidence, continuation is an exact No-Op with the first reward unchanged.
5. Otherwise restore the candidate and original Codex session in a fresh environment, send only the
   selected evidence, then run promotion and the official evaluator.

Primary row outcome:

```text
official_reward_after_GRAFT - official_first_candidate_reward
```

Secondary mechanism outcomes include selected evidence eligibility, feedback adoption, promotion,
verifier/model cost, wall time, and whether an unselected verifier would have detected an official
failure. Branch health-check rewards are ignored.

