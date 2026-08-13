# Amendment 06 — EvidenceCapability and ReproductionBundle mechanism replay

Date: 2026-08-13

This amendment records a post-hoc mechanism replay over the already frozen VF2 checkpoint. It does
not reopen the censored treatment row, run a new model, send producer feedback, or estimate an
effectiveness delta.

## Frozen inputs

- checkpoint: `6700f6aff33c0484a8729f77eadde30ad034516996fc852fdddc5845c1581a29`;
- candidate tree: `0535e3f160f04b5095f958806b74f328c1e6f17605bc444f6c161c45d99e56cc`;
- candidate manifest SHA-256:
  `71fb790b5938c768afe799a808bbe31dfed5d07114edc6d10e1d66a8101ddbb3`;
- frozen plan SHA-256: `5d19de7d992592e826ecc7de1c083fcc59cca44ad5083a0b4745396a89dbfc57`;
- four saved complete branch files, including the two blocking finding reports recorded by
  Amendment 05.

## Replay result

The current generic canonicalization/portability function was applied to the exact reported command
for every saved evidence item carrying a failure mode. Both commands were rejected, so neither can
form a `ReproductionBundle`:

| Verifier | Failure | Canonical replay argv | Bundle |
|---|---|---|---|
| `agentic-evidence-02` | `F11` | unavailable | no |
| `adversarial-test-01` | `F02` | unavailable | no |

Both commands visibly declare an environment path under verifier-only `/tmp` state. Under the
explicit counterfactual route declaration `dependency_origins=[verifier_workspace,
frozen_candidate]`, `EvidenceCapabilityPreflight` classifies both candidates `unavailable` before
selection. This counterfactual is diagnostic only: the old frozen Original plan predates the new
capability field, so it must not be represented as a prospectively collected planner answer.

The machine-readable result is
[`VF2-EVIDENCE-REPLAY-01.json`](VF2-EVIDENCE-REPLAY-01.json), SHA-256
`ae3a5f601c0c1616c6dc09670e6f5919c8890e0a81c99ec9cd8302638e0f5574`. The regression is executed
by `test_frozen_vf2_findings_cannot_form_reproduction_bundles` in
`tests/test_coding_verifier_matrix.py`.

## Interpretation

This replay confirms the intended safety mechanism on the exact failure that motivated it:

```text
LLM discovery: retained as an auditable finding
portable Stop evidence: absent
ReproductionBundle: absent
producer continuation: forbidden
```

It does not show that preflight improves task reward, because the capability declarations were not
collected before the frozen verifier runs and the row remains incomplete. A valid effectiveness
test requires a new prospectively frozen task where every arm and capability plan is captured before
outcomes are observed.
