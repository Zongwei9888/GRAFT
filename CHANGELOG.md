# Changelog

All notable changes follow semantic versioning.

## Unreleased

- keep continuation re-verification inside the Stop hook so producer-side manual `cli verify`
  calls cannot bypass `max_feedback_rounds`;
- give the dynamic behavior modeler 180 seconds of the existing 600-second Stop window after a
  Terminal-Bench pilot exposed timeouts on a large generated checkpoint;
- add a profile-free, source-pinned Terminal-Bench 3 adapter and matched Native Codex control.
- preserve a per-file task-baseline manifest so candidate-authored documentation, tests, and code
  cannot be promoted into authoritative task contracts;
- require LLM evidence to name its oracle origin and target failure modes; permit blocking from
  observed authoritative runtime, unchanged baseline oracles, or numbered-requirement-derived
  executable counterexamples against the actual candidate while rejecting generated substitutes;
- reject shared-lineage verifier plans that omit the frozen method's high-order blind-spot model;
- add an explicit, reviewed opt-in for network access in workspace-write verifier sandboxes;
- record a second negative profile-free Terminal-Bench 3 pair and its self-referential contract
  failure without crediting the post-hoc correction as effectiveness evidence.
- record the prospective `bun-sourcemap-leak` negative pair, including the corrected provenance
  behavior, missed executable counterexample, and lack of causal continuation feedback;
- make the selector assign zero Stop-gating utility to non-blocking findings and redefine planner
  detection estimates as the probability of producing eligible reproducible feedback.

## 0.5.0 - 2026-08-10

- freeze the original DOCX method as the authoritative implementation contract;
- replace language-specific discovery and hand-authored calibration fixtures with structured LLM
  construction of task-specific Behaviors, Failure Modes, verifier candidates, and shared blind
  spots;
- replace exact empirical-fixture enumeration with the original cost-aware hypergraph greedy
  selector and best-singleton fallback;
- add isolated semantic, agentic-execution, and disposable-copy test-agent capabilities;
- bind evidence to source and requirements, require an observed reproducer before model findings can
  block, and reject stale evidence after workspace mutation;
- make the dynamic registry work in arbitrary Git and non-Git directories without initialization;
- retain the fixed Terminal-Bench v1 profile only as a rejected historical negative experiment.

## 0.4.0 - 2026-08-10

- prepare the repository for public GitHub distribution;
- add cross-platform packaging, release checks, and CI metadata;
- add project enable/disable, config validation, and reusable user profiles;
- require explicit hash trust before executing repository-provided verifier commands;
- reject verifier working directories that escape the workspace;
- document plugin, package, configuration, privacy, and release workflows.

## 0.3.0 - 2026-08-09

- add a self-contained Codex plugin with lifecycle hooks and bundled runtime;
- add global hooks installation, session-aware checkpoints, and event deduplication;
- add command and isolated Codex-review verifiers.
