# Architecture

GRAFT is a feedback controller around a free Codex producer, not a replacement planner.

```text
producer session
  ├─ UserPromptSubmit → raw multi-turn requirements / task epoch
  ├─ PostToolUse      → bounded redacted semantic evidence
  └─ Stop             → deterministic guards + immutable checkpoint
                              ↓
                  value-aware only: LLM completion gate
                     ┌────────┴────────┐
                  No-Op        candidate_complete
                                      ↓
                 structured Behavior/Failure modeler
                              ↓
                structured verifier-registry planner
                              ↓
          Behavior–Failure–Verifier–Lineage hypergraph
                              ↓
          value-aware: EvidenceCapabilityPreflight
                              ↓
       Original detection selector OR value-aware/No-Op selector
                              ↓
                     isolated verifier execution
                         ┌────┴────┐
                    allow stop   reproducible failure
                                      ↓
                            same producer continues
                                      ↓
                         executable promotion revalidation
```

For failure mode `z` and selected set `S`, the implementation evaluates a mixture of the ordinary
case and explicit shared-blind-spot scenarios:

```text
P(miss z | S) = Σ_h w_h Π_{f∈S}(1 - p(f detects z | h))
utility(S)    = Σ_z risk(z) · (1 - P(miss z | S))
```

A blind-spot hyperedge can jointly weaken more than two verifiers. This is why multiple fresh Codex
threads are not automatically treated as independent. The selector performs cost-aware greedy
retrieval and compares it with the best feasible singleton.

The fixed layer contains only method protocols: typed schemas, state hashes, budget/stop policy,
sandboxing, generic capability templates, and lineage fields. Behaviors, Failure Modes, verifier
objectives, prompts, detection estimates, and shared-blind-spot instances are generated for the
current task.

For the opt-in value-aware strategy, candidate `f` is discounted by actionability, successful
repair probability, overlap with producer evidence, and estimate uncertainty. Its joint
risk-weighted detection benefit still uses the high-order graph, then subtracts predicted
execution and repair-regression costs. Greedy marginal net value is compared against a formal
No-Op candidate with value zero. This is a provisional decision model pending held-out calibration,
not a correctness probability.

The value-aware path now separates discovery capability from feedback capability. Before selection,
each blocking verifier must have at least one task-dynamic route whose authority can support
feedback, whose final transport is a standalone command, and whose dependencies already belong to
the task environment, frozen candidate, or unchanged baseline. After execution, GRAFT binds the
observed event, canonical replay argv, expected/actual observation, route, lineage, failure modes,
and checkpoint into a hashed `ReproductionBundle`. The preflight declaration is an LLM planning
claim, not evidence; the post-execution guard remains authoritative.

Task-epoch state accumulates nominal verifier budget, measured wall time, known model cost and a
count of stages whose model cost is unavailable. Cost history is stored by stable generic verifier
template ID only; it contains duration/token/cost metadata, never source or prompt content. A
conservative observed quantile overrides cold-start LLM cost predictions.

Security boundaries:

- project overrides require an exact trusted config hash;
- model stages run in fresh ephemeral threads with hooks/user config/rules isolated;
- writable verifier agents use disposable copies;
- task-start per-file hashes and an external bounded text-source archive separate unchanged baseline
  oracles from candidate-authored artifacts and provide semantic diffs in non-Git directories;
- model-only suspicions, generated mocks, candidate contracts, and source-display commands cannot
  become blocking evidence; an authoritative runtime observation, unchanged baseline oracle, or
  raw-requirement-derived executable counterexample against the actual candidate is required;
- verifier network access is off by default and requires a reviewed project opt-in;
- verifier execution must leave the producer checkpoint unchanged;
- evidence produced for one source hash cannot gate another.
- after continuation feedback, a new checkpoint is accepted only when a designated executable
  verifier reports `fixed_and_preserved` with observed eligible evidence; `not_fixed`, `regressed`,
  and `unresolved` remain explicit report states.

The plugin embeds the same core as the Python package. `scripts/sync_plugin_runtime.py` replaces the
embedded tree atomically, and tests reject drift. Runtime state lives outside target repositories.
