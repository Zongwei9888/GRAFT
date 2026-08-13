# Prospective coding trial 01 — result

Date: 2026-08-13

Status: **negative online result; evidence-transport and contract-authority defects found**

This trial was frozen before producer output in `PROSPECTIVE-01.md`. It tests one
FeatureBench task and is not a population estimate.

## Outcome summary

| Stage | Result |
|---|---|
| Native/shared-prefix Codex | official reward `1.0`; no exception |
| Shadow GRAFT matrix | 10 behaviors, 8 failure modes, 7 verifiers, 0 shared-blind-spot edges |
| Frozen budget-4 selection | `adversarial-interface-test` + `agentic-optional-provenance` |
| Same-thread continuation | same thread resumed; candidate modified |
| Promotion | `unresolved`; both selected verifiers reported `regressed` with matched execution |
| Repaired official evaluator | reward `1.0`; no exception |
| Official reward delta | `0.0` |

GRAFT did not improve benchmark reward. It caused an unnecessary additional repair turn
and changed a candidate that already passed. In the bare task environment, the repair
made `import agent_code` fail because `pandas` is intentionally absent. The promotion
guard correctly refused to accept the repair. The official evaluator still returned
`1.0`, so the promotion observation and the benchmark acceptance boundary are not
equivalent.

## Frozen shared prefix

- task: `pandas-dev__pandas.82fa2715.test_iceberg.85771c70.lv2`
- producer model/runtime: `gpt-5.6-sol`, high, Codex CLI `0.147.0`
- producer thread: `019ff905-92c7-7800-af45-7b46be07848a`
- producer session SHA-256:
  `8b158584a71a0fee91efcac213119a50a077bb973b06fc9f2aabbd79fd93b3b2`
- baseline tree:
  `1e217b53e16bf1919df7c4def139d563f121b7b514be2527e783ab574e2ace94`
- candidate tree:
  `c2e065ff3c621a9a543a62555ca4d9838cacbe8f5f477222844ac190fb4d3c00`
- checkpoint:
  `7ffa319bca17118f247913759d2c31b1623f923b5f16ac512e098d2641a1d6f5`
- matrix SHA-256:
  `52867961605f4cad17b08ae73608b21803643c47f40ee98992fb7b392abf735c`
- candidate archive SHA-256:
  `91d819befc7a9f0afb9aee9ce140c0fd4fa8a454b8f9369600ff9fb67d47f8b9`

The producer added `agent_code/__init__.py`, `agent_code/iceberg.py`, and `setup.py`.
Producer usage was 164,605 input, 148,480 cached input, and 5,023 output tokens. Harbor
reported `$0.305555` for the producer trajectory only; this is not total GRAFT cost.

## What the matrix selected

Run-all shadow verification produced three eligible failing verifier records. The
frozen Original policy selected two at total modeled cost `3.5`, predicted coverage
`0.1171`, and predicted residual risk `0.8829`:

1. `adversarial-interface-test`, targeting the prescribed pandas helper/type provenance
   and transitive `ImportError` behavior;
2. `agentic-optional-provenance`, independently targeting the runtime return annotation.

The graph had zero shared-blind-spot edges. Therefore no high-order graph penalty or
common-failure structure contributed to this selection. This trial provides no positive
evidence for the graph component.

The graph modelers consumed 57,935 input and 6,083 output tokens. The seven shadow
verifiers consumed 955,070 input, 759,296 cached input, and 14,736 output tokens; summed
verifier duration was 554.9 seconds, although verifiers ran concurrently. This overhead
is large relative to the producer.

## Same-thread repair and promotion

The continuation emitted the exact original thread id. Codex changed the implementation
to import `DataFrame`, `import_optional_dependency`, and `set_module` from their
prescribed pandas locations, added identifier validation and missing-table translation,
and preserved scan forwarding. Continuation usage was 334,771 input, 302,336 cached
input, and 9,311 output tokens.

The repaired state was:

- tree:
  `83723a077422d4230492f8fd5d6e0b9b0d11d4156b5e567e8da80f61b79f4234`
- checkpoint:
  `666cd9e1b23910b7eba3dd0e6cc9d19fa19c143edb80ea48cdd02f2698e93e2a`
- archive SHA-256:
  `0920e2e52c4d07477bb7cacff0b401038b5fd0495bb3c0869423be30a0209cc7`
- continued session SHA-256:
  `caeae3082a828610526b57499d0d7cdffa05ba04cc9028bc4b6f67b9e4a45d59`
- promotion report SHA-256:
  `4744c888fe79f39fe8b7d5b327801050e3bd11163271912c4dc32ca74f4c5386`

Both promotion verifiers used standalone commands whose Codex events matched. Both
observed that importing the repaired package in the bare task environment raised
`ModuleNotFoundError: pandas`, returned `FAIL`, and marked the outcome `regressed`.
GRAFT therefore kept the status `unresolved`; it did not convert model optimism into a
successful promotion. Promotion consumed 249,715 input, 198,144 cached input, and 4,692
output tokens.

## Root causes

### 1. Discovery evidence was executable but not transportable

Every selected failing reproduction invoked a verifier-created file:

- `python verifier_checks/check_interface.py`
- `python verifier_checks/check_transitive_import.py`
- `python verify_annotation.py`

Those files lived only in disposable shadow copies. The report preserved commands but
not script contents, so the producer thread could not rerun them. The old evidence guard
proved “this command ran here,” not “this reproduction can travel to the next state.”

This is the direct protocol defect that allowed feedback. Under the amended portability
guard, replaying the exact frozen matrix now returns:

```text
ValueError: Frozen selection contains no eligible executable feedback
```

Thus the corrected policy would have performed No-Op and preserved the native candidate.
This is a post-hoc safety audit, not a rerun of the frozen treatment.

### 2. The raw contract contains an environment conflict

The public instruction prescribes pandas helper imports and a pandas `DataFrame` type,
while also stating that pandas is absent, must not be installed or called, and is not
needed in the task environment. The verifiers enforced the first branch; promotion
enforced bare-environment importability. Neither branch alone resolves the conflict.

The official score of `1.0` before and after repair shows that bare import without the
evaluation boundary is not a reliable proxy for the evaluator contract on this task.
Because hidden evaluator content remained unseen, the mechanism must treat the conflict
as uncertainty and abstain rather than infer which dependency boundary the evaluator
supplies.

### 3. Selection confidence was poorly calibrated

The selector predicted only 0.117 coverage and 0.883 residual risk yet still spent the
full modeled budget. More importantly, it reasoned over verifier predictions, not whether
the resulting evidence packet could be safely reproduced by the producer. Evidence
transportability and contract authority must be feasibility constraints before expected
coverage is optimized.

## Implemented amendments

The following generic rules were added after the result was frozen:

1. requirement-derived evidence may reference only files in the frozen candidate, or
   carry its program in one standalone argv such as `python -c`;
2. verifier-created temporary scripts are mechanically ineligible for feedback;
3. feedback replay repeats the portability check, including for old reports;
4. only failure modes actually covered by eligible evidence are placed in feedback and
   promotion—not every finding reported by the same verifier;
5. explicit environment/evaluation constraints are authoritative; conflicts with an
   interface instruction require abstention unless an allowed boundary or unchanged
   baseline oracle resolves them; and
6. promotion still requires event-matched executed evidence, source stability, and
   `fixed_and_preserved`.

These amendments contain no task name, framework, expected answer, or hidden evaluator
logic.

## Scientific conclusion

This prospective trial is a negative result for the current online GRAFT policy. It
validates same-thread continuation and conservative promotion mechanics, but also shows
that dynamic LLM verifiers can create apparently executable yet non-transferable evidence
and can over-enforce one side of a contradictory public contract. Benchmark reward was
neutral, process cost was strongly negative, and the graph had no observable role.

No claim that GRAFT improves Codex is supported. Before another expensive online trial,
the portable-evidence rule must pass frozen unit/replay tests, and the next task must use
a non-saturated or graded outcome so a repair can have measurable value. Population-level
claims still require the pre-registered paired multi-task baselines.
