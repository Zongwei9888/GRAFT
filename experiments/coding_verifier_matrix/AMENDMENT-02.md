# Coding verifier matrix protocol amendment 02

Status: **infrastructure amendment frozen before the replacement producer run**

Frozen date: 2026-08-13

## Observed smoke 01

The first Metaflow matrix job completed with official reward `1.0`, no exception, and no feedback to
the producer. GRAFT dynamically produced 7 verifiers over 15 failure hypotheses and 6 shared-blind-
spot scenarios. The producer candidate changed one source file and remained byte-identical before
and after shadow verification.

However, all executable model verifiers encountered the same infrastructure failure: the nested
Codex Linux sandbox could not install seccomp inside the x86 FeatureBench image running under Docker
Desktop emulation. Five results were `error` or `abstain`; one non-blocking semantic review proposed
failures without executable evidence; no result was eligible for feedback. This row demonstrates the
promotion guard but is censored for verifier-detection analysis.

Frozen artifact hashes:

- matrix: `73e1ba6319acddbb4f834a7f69ab6e15f3a49d93a083ebf85f7a5ce8c69f6b4d`;
- trial result: `d270f03dd430eaa0ae50b9ee347a6bbfb6841801ea5100f31724836075fe9509`;
- job result: `4d2a49cb9dec66ceebf02a7cd2329d020afa70c2a3cd2b2887316f594d1edb88`.

The task solution and evaluator tests were not inspected. The official `1.0` label was observed only
after the matrix artifact had closed.

## Replacement isolation

The replacement run changes infrastructure only:

1. archive the exact producer candidate outside its worktree before model verification;
2. give every verifier, including semantic reviewers, a fresh candidate copy;
3. disable the unsupported nested Codex sandbox only inside that copy;
4. retain the disposable Harbor container as the outer isolation boundary;
5. hash the producer worktree after all verifiers and invalidate evidence if it changed;
6. continue to treat model findings as proposals unless the existing execution-identity and
   authority-promotion guard observes an eligible reproduction.

This is not a product-default change and does not alter task semantics, graph prompts, verifier
prompts, evidence eligibility, selection, or the official evaluator. Because the outer container's
agent-phase network remains available for Codex model calls, this local smoke records network
isolation as unenforced and cannot support a final security/isolation claim.
