# Architecture

GRAFT is a feedback controller around a free Codex producer, not a replacement planner.

```text
producer session
  ├─ UserPromptSubmit → raw multi-turn requirements
  ├─ PostToolUse      → hashed tool facts
  └─ Stop             → immutable checkpoint
                              ↓
                 structured Behavior/Failure modeler
                              ↓
                structured verifier-registry planner
                              ↓
          Behavior–Failure–Verifier–Lineage hypergraph
                              ↓
              budgeted high-order-aware retrieval
                              ↓
                     isolated verifier execution
                         ┌────┴────┐
                    allow stop   reproducible failure
                                      ↓
                            same producer continues
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

Security boundaries:

- project overrides require an exact trusted config hash;
- model stages run in fresh ephemeral threads with hooks/user config/rules isolated;
- writable verifier agents use disposable copies;
- task-start per-file hashes separate unchanged baseline oracles from candidate-authored artifacts;
- model-only suspicions, generated mocks, candidate checks, and source-display commands cannot
  become blocking evidence; an authoritative runtime observation or unchanged baseline oracle is
  required;
- verifier network access is off by default and requires a reviewed project opt-in;
- verifier execution must leave the producer checkpoint unchanged;
- evidence produced for one source hash cannot gate another.

The plugin embeds the same core as the Python package. `scripts/sync_plugin_runtime.py` replaces the
embedded tree atomically, and tests reject drift. Runtime state lives outside target repositories.
