# Promotion-loop pilot results

This experiment establishes a mechanism result, not a quality or WSDM effectiveness result. The
protocol and task were frozen in commit `5e2e963984f9e979aea900b5b995242af717c1d0` before the first
producer, verifier, or evaluator run.

## Preregistered run

Native Codex thread `019ff4df-2c44-7690-a140-f6a92e5e174b` produced checkpoint
`b6549f9ce0fd4d4f9170444fcb617bf44d57a159f45360af128ed8e22a9916c2`. Its repository suite passed
10 tests and the independently authored held-out evaluator passed 23/23 cases.

The dynamically constructed GRAFT graph contained 11 behaviors, 18 failure modes, three candidate
verifiers, and five shared-blind-spot scenarios. The value-aware selector chose the adversarial CLI
verifier. That verifier discovered two candidate defects absent from the authored evaluator:

- malformed raw UTF-8 was accepted with status 0 and normal stdout;
- an escaped hyphen in `[a\\-c]` lost its escape and behaved as the range `a-c`.

Independent checkpoint replay confirmed both observations. The preregistered run nevertheless
ended `unresolved`: its structured reproductions were not bound to eligible standalone Codex
command events by the then-current implementation. No continuation was manufactured. This is the
authoritative preregistered outcome.

## Post-hoc mechanism diagnosis

After the observed integration defects documented in `POSTHOC-02.md` through `POSTHOC-08.md` were
fixed, the already frozen checkpoint and graph produced eligible continuation evidence for the
malformed-UTF-8 violation. The exact saved packet was returned to the same producer thread:

| Observation | Result |
| --- | --- |
| Producer thread before/after | identical |
| Checkpoint before | `b6549f9ce0fd4d4f9170444fcb617bf44d57a159f45360af128ed8e22a9916c2` |
| Checkpoint after | `11ce37c774a8ec9a36b56af8563527ef23544b914877aa6de16faf23c9d15297` |
| Original held-out evaluator | 23/23 before, 23/23 after |
| Two GRAFT-discovered probes | 0/2 before, 1/2 after |
| Promotion command events | seven completed events |
| Promotion decision | `allow` |
| Promotion outcome | `fixed_and_preserved` |
| Promotion evidence | executed and checkpoint-bound |

Codex changed stdin handling to strict byte-to-UTF-8 decoding and added a regression test. The
invalid-UTF-8 probe then returned status 2, empty stdout, and one stderr line. The escaped-hyphen
defect remained. It had been recorded as advisory uncertainty rather than placed in the blocking
feedback packet, so the promotion guard neither asked Codex to repair it nor claimed that it had.

## Interpretation

The experiment now verifies this concrete lifecycle:

```text
Native candidate
  -> LLM-built behavior/failure/verifier graph
  -> selected verifier executes a real counterexample
  -> source-bound evidence packet
  -> same Codex thread repairs a new checkpoint
  -> mandatory verifier executes repair and preservation checks
  -> fixed_and_preserved promotion
```

It does **not** show that GRAFT is generally better than Native Codex. The useful counterexamples
were discovered during the treatment and the implementation was repaired after observing the
initial outcome. The remaining escaped-hyphen defect also shows that LLM contract interpretation
can incorrectly downgrade a real requirement to uncertainty. A defensible effectiveness claim
requires preregistered, shared-prefix paired tasks with external evaluators, frozen code, multiple
seeds, cost reporting, and Native/No-Op/selector ablations.

