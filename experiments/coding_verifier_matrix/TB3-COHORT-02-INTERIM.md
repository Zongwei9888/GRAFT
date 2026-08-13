# Terminal-Bench 3.0 cohort 02 interim result

Status: **paused after a censored first row; no GRAFT effectiveness result**

Date: 2026-08-13

Protocol: [TB3-COHORT-02.md](TB3-COHORT-02.md)

## Infrastructure checks

- `production-planning` Oracle: reward `1.0`;
- original `data-anonymization` Oracle: canceled after its previously omitted public 7200-second
  verifier limit produced an eight-minute health check; no model ran and no reward was assigned;
- replacement `kv-live-surgery` Oracle: reward `1.0`.

The verifier-resource amendment and replacement selection were committed before any cohort Codex
producer ran.

## Censored `production-planning` row

Native Codex completed a real ERP/MES/WMS trajectory and reported a validated plan. It used 912,673
input tokens (822,528 cached), 25,265 output tokens, and Harbor estimated USD 1.619939. The candidate
checkpoint was frozen before GRAFT modeling:

- producer thread: `019ff95c-c710-7212-910a-582516a11d34`;
- candidate tree: `2a335e45e1a24ac5843ae535b6508c4a50cee05ec777fc226d4d590a1e9599d8`;
- checkpoint key: `7d245778ac201a6f655fcfca550bb99801823d059b838edfbf52dc025538cd21`;
- matrix SHA-256:
  `bfb748e220ceff2f2c7b5d24f7e64e7c4b54e1d8fa59e9ce3b20458900e19bdd`;
- candidate archive SHA-256:
  `093966323241a32b8599c7d4d95c6b77285f49ce4a550fbcc368b040edbcae7e`;
- session SHA-256:
  `3192a7d49141fe05672cc5697a79156c2ca25baa050b7cfb71ffaa0de0aef755`.

The archive manifest skipped four candidate paths:

```text
data/dbgw
data/erp.db
data/mes.db
data/wms.db
```

GRAFT then built 16 Behaviors, 14 Failure Modes, seven verifiers, and five high-order blind-spot
edges. All seven verifier results were invalidated because the live producer workspace changed
during verification; the changed paths were the three databases and audit/output artifacts. Raw
requirements and model commands use absolute `/app/...` paths, so a temporary directory copy is not
an isolation boundary for this service-backed task. The candidate archive cannot restore the
skipped binary/database state, and the verifier may also have changed external service state.

Harbor subsequently reported reward `0.0`, but that score was measured after the contaminated
shadow phase. It is neither a valid first-candidate score nor a GRAFT feedback score. No eligible
finding or continuation existed. The entire row is censored as an isolation failure.

## Consequence

The second cohort producer is paused. Repeating the same file-copy matrix on another service-backed
task would spend model budget without a valid causal boundary.

The experiment harness now creates a bounded full regular-file candidate archive, including binary
outputs, and stops before LLM graph construction whenever a changed/new candidate file still cannot
be archived (for example a symlink or an archive beyond the 256 MB bound). Unchanged baseline files
do not make a candidate unreplayable. In a stopped row the original candidate proceeds directly to
the official evaluator, so data are not contaminated, but the row supplies no verifier matrix.

This does not solve service isolation. Service-backed verification requires cloning the complete
task environment and service state for each verifier branch, with no route from branch `/app` or
service endpoints to the producer. That is a separate infrastructure gate and must be implemented
before resuming this cohort.
