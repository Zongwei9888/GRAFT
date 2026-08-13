# Coding verifier matrix protocol amendment 04

Status: **infrastructure amendment frozen before environment-branch trials**

Frozen date: 2026-08-13

## Why the prior copy boundary is insufficient

The censored `production-planning` row showed that a copied directory is not a copied task
environment. Its public instruction names `/app`, and its behavior includes mutable ERP, MES, and
WMS state. A verifier whose current directory is a temporary copy can still follow the authoritative
instruction and touch the original `/app` or shared services. Hashing the producer afterward detects
some contamination, but detection after the fact cannot recover a valid first-candidate score.

The legacy in-container matrix now fails closed before LLM graph construction when a raw requirement
contains an absolute reference to the producer workspace. This guard prevents another known-invalid
row; it is not presented as complete process isolation.

## Replacement causal unit

The replacement harness separates one logical trial into content-addressed phases:

1. **Candidate capture.** Native Codex runs once. Before any GRAFT model stage, the harness records
   the exact baseline, candidate tree, full bounded regular-file archive, public requirement hash,
   configuration hash, Codex session, and artifact digests. Harbor then scores this untouched first
   candidate.
2. **Graph plan.** A fresh task environment restores the candidate and builds the dynamic
   Behavior--Failure--Verifier--Lineage graph. It executes no verifier and sends no feedback.
3. **Verifier branches.** Every planned verifier receives its own fresh task environment, restores
   the same candidate checkpoint, and runs alone. Absolute task paths therefore resolve inside that
   verifier's branch. Branch mutation is recorded and discarded rather than invalidating or changing
   the producer.
4. **Assembly and selection.** Only checkpoint-matched branch results are assembled. Missing,
   duplicate, stale, or foreign verifier results fail closed. The frozen selector operates on the
   assembled matrix.
5. **Continuation.** When selected evidence passes the existing authority, execution-identity, and
   portability gates, another fresh environment restores the exact candidate and original Codex
   session before delivering feedback. Otherwise the treatment is an exact No-Op.

The graph model, verifier planner, verifier prompts, lineage representation, selection objective,
and promotion rules are unchanged. The amendment changes experimental isolation only; it adds no
task name, expected output, benchmark fixture, solution rule, or hard-coded verifier.

## Current scope boundary

The branch implementation makes filesystem state and absolute workspace paths causally independent.
It does not yet snapshot arbitrary external service state or Docker volumes modified by the producer.
Until service-state capture is implemented, effectiveness trials are restricted prospectively to
single-task-container, filesystem-contained tasks. Service-backed tasks remain eligible only for
infrastructure research and cannot contribute to the primary GRAFT effect estimate.

Each verifier branch's automatic Harbor reward is an ignored health check. The only first-candidate
outcome is the reward from the candidate-capture trial; the only treatment outcome is the reward from
the later continuation trial.

