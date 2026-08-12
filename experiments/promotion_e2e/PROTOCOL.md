# Current promotion-loop end-to-end pilot

Status: frozen before the producer, verifier, or evaluator is run.

This pilot tests one mechanism claim on the current GRAFT implementation:

```text
Native Codex candidate
  -> dynamic GRAFT verification
  -> reproducible blocking evidence (if any)
  -> same Codex thread continuation
  -> executable promotion revalidation
```

It is not an effectiveness estimate. One task cannot establish a WSDM result.

## Information boundary

- The producer receives only the raw task in `task.json` and an empty Git workspace.
- GRAFT receives the same raw task, the frozen candidate, producer execution telemetry, and its
  domain-neutral repository-evidence and test-agent templates.
- The evaluator cases in `run.py` are held out from both producer and verifier workspaces.
- Hooks are disabled in the scripted Codex turns. The runner explicitly resumes the recorded
  producer thread with exactly the GRAFT decision reason.
- No task-specific command, fixture, path-pattern verifier, or expected answer is placed in the
  GRAFT configuration.

## Frozen decisions

- Model: `gpt-5.6-sol` for producer, graph stages, and verifier agents.
- Producer timeout: 600 seconds; graph-stage timeout: 240 seconds; verifier timeout: 300 seconds.
- At most one verifier is selected per checkpoint so a promotion verifier retains nominal budget.
- Value-aware weights are declared in `run.py` before the result is observed. They differ from
  product defaults because this is a mechanism-trigger pilot, not a default-policy evaluation.
- The task succeeds only if all held-out cases pass.

## Interpretation

- If the first candidate passes, report that no continuation was warranted; do not invent one.
- If GRAFT does not produce eligible reproducible evidence, do not manually force feedback.
- If continuation occurs, require the returned thread ID to equal the original thread ID, evaluate
  both checkpoints, and require `fixed_and_preserved` for promotion.
- Preserve every raw model event and GRAFT report under ignored `artifacts/promotion-e2e/`.

