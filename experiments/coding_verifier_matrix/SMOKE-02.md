# FeatureBench Metaflow executable verifier-matrix smoke

Status: **completed mechanism/data-pipeline smoke; not a positive method claim**

Run date: 2026-08-13

## Frozen setup

- Dataset: Harbor `featurebench-lite@1.0`.
- Task: `netflix__metaflow.b390a8d4.test_stub_generator.7bf08c98.lv1`.
- Public instruction SHA-256:
  `dd90ed450be01f947d1dd44ad362794903f97c822f359fdf0142dd17bc12e638`.
- Producer and model verifiers: Codex CLI `0.147.0`, `gpt-5.6-sol`, high reasoning.
- GRAFT source: `52f8974c956058409ae98c0542a61066f5964a54`.
- Frozen job-config SHA-256:
  `dba13b0b1d8a40bc2def76a90bcc4bff25cc2365395dcbc99b2a6594b5fa76cd`.
- The official solution and evaluator tests were not inspected.

The Oracle preflight scored `1.0` before treatment. Native Codex then produced a candidate without
GRAFT hooks. GRAFT ran only after that candidate was frozen and sent no feedback to the producer.
The official evaluator ran after the matrix artifact closed.

## Candidate and result integrity

- Baseline tree:
  `af754c09bf5827f40bbf5b6c8494986c23fc2233eff5eee72eb77a257b0455c9`.
- Candidate tree:
  `b6dddee3e6906c475ace17c7018ed007c058d59dd0ca67605ffe24eec44c5e5f`.
- Post-verifier tree: identical to the candidate tree.
- Changed path: `metaflow/cmd/develop/stub_generator.py`.
- Candidate archive SHA-256:
  `c132a465c8b5898e146bbca6979f93239ff043b386a90b4a6919064ab8b86eda`.
- Matrix SHA-256:
  `4881015a048e4f36061f55447b1bd91e109315c5f91497f78a926ae3dee3a509`.
- Trial-result SHA-256:
  `4839dcba09a49919196d8547a328196af706ff2fd2645986062e29e5313adac1`.
- Job-result SHA-256:
  `6315c1c9aefcb1f5b7693ee6f547c6654694d3982fb9c1afedbb2aacc16fbcb3`.
- Official reward: `1.0`; job exceptions: `0`.

The v1 and v2 independent producer runs yielded the exact same candidate and checkpoint key. This
makes the nested-sandbox amendment comparison source matched, although the graph-builder calls were
new samples.

## What GRAFT produced

The dynamic modeler and planner produced:

- 9 Behaviors;
- 18 Failure Modes;
- 7 candidate verifiers;
- 6 shared-blind-spot scenarios;
- 195.0 seconds of summed graph-modeling duration.

All seven planned verifiers ran in shadow mode. Three timed out, three returned blocking and
mechanically reproducible failures, and one returned non-blocking semantic-review failures. The
three eligible verifiers covered seven failure-mode IDs. Their executed observations included:

- a positional-only runtime signature emitted without `/`;
- singleton tuple default `(1,)` emitted as `(1)`, which evaluates as an integer expression;
- an embedded triple quote producing a generated-stub parse error;
- an empty `TypedDict` class emitted with no body and failing `ast.parse`;
- a public attached callable omitted from an emitted class stub;
- a defaulted `NamedTuple` field emitted as required;
- a syntactically valid but arguably redundant `object, metaclass=type` class header.

The first six findings have direct relationships to the public requirements for proper signatures,
defaults, documentation, dynamic/injected methods, `TypedDict`, and `NamedTuple` handling. The final
class-header finding rests on the softer “clean, readable” requirement and requires independent
adjudication before it should drive feedback. This shows that command execution and requirement
references are necessary but not sufficient to resolve semantic ambiguity.

The official `1.0` score does not decide whether these observations are false positives. It can also
mean that the benchmark evaluator is narrower than its public natural-language contract. Independent
adjudication and repair/preservation testing are therefore required.

## Cost and selection diagnostics

The nested shadow Codex calls are not represented in Harbor's producer trajectory metrics, so the
job-level `$0.946388` must **not** be reported as total GRAFT cost. Matrix-local accounting records:

- graph calls: 89,268 input, 42,496 cached input, and 8,293 output tokens;
- verifier calls with completed usage: 1,266,393 input, 1,106,432 cached input, and 9,250 output
  tokens;
- three timeout calls have no final token record, so even these totals are lower bounds;
- verifier duration sum: 901.4 seconds; calls were concurrent, so this is not verifier wall time.

At the frozen Original budget `4.0`, offline replay selected:

```text
adversarial-stub-test       -> eligible executable failures
runtime-annotation-function -> timeout
```

Removing every shared-blind-spot scenario selected the same two verifiers. The high-order graph
changed predicted coverage from `0.7870` to `0.4469` but did not change the decision. A different
unselected verifier, `runtime-class-integration`, also found eligible failures. Thus this row provides
no evidence that the graph improved selection and exposes an opportunity-cost/calibration problem.

## What this run establishes

Supported for this one checkpoint:

- a completely unseen coding task can be modeled without task-name, language, framework, or hidden-
  test routing;
- LLM-generated verifier objectives can become executed, source-bound evidence;
- the evidence guard rejects model-only findings as blocking evidence;
- shadow verification preserves the producer candidate;
- Native Codex can pass the official evaluator while dynamic requirement-derived probes reveal
  additional plausible contract violations.

Not established:

- that any finding should be fed back without independent adjudication;
- that feedback improves final quality or avoids regression;
- that GRAFT beats Native Codex, strongest-single, pairwise, or run-all baselines;
- that the high-order graph has positive causal value;
- that current trigger, selector, graph stability, latency, or cost is deployable.

## Next preregistered step

Use this exact archived candidate and saved producer thread for a post-hoc mechanism test only:

1. independently adjudicate the strongest requirement-grounded reproductions;
2. return only adjudicated evidence to the same Codex thread;
3. re-run the original reproductions plus preservation checks in a fresh candidate copy;
4. run the unchanged official evaluator last;
5. report repair, regression, promotion, time, token lower bounds, and any unresolved ambiguity.

This next step can validate continuation and promotion mechanics. It still cannot supply a population-
level method claim; that requires a prospectively sampled multi-task checkpoint matrix.
