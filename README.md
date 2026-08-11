# GRAFT

GRAFT is an external verification governor for Codex. It leaves the producer Codex loop free to
search, edit, test, and repair. When Codex reaches a changed Stop boundary, GRAFT asks a fresh
structured model to derive the current task's Behaviors and Failure Modes, asks another fresh model
to instantiate a heterogeneous verifier registry, selects complementary evidence under a budget,
and runs the selected verifiers.

The original research method is frozen in
[`docs/method-original-frozen.md`](docs/method-original-frozen.md). The DOCX named there remains the
authoritative specification. The consolidated Chinese definition of what GRAFT does, where LLMs
participate, what counts as hardcoding, and why a changed `Stop` is not by itself task completion is
recorded in [`docs/graft-core-definition-zh.md`](docs/graft-core-definition-zh.md).

## What changed in 0.5

Version 0.5 removes the product's previous language discovery, fixed command checklist, manually
authored failure rows, and exact fixture selector. Those were useful integration scaffolding but
were not the original GRAFT idea and could not generalize to unseen tasks.

The production path now has no closed list of languages, frameworks, repository layouts, game
mechanics, failure instances, or verifier combinations. General protocols are fixed—schemas,
sandboxes, lineage fields, budgets, state hashes, and verifier capabilities—but task semantics are
created at runtime from the raw multi-turn requirements and observable workspace.

```text
Codex producer session reaches Stop with a changed workspace
                         ↓
               freeze requirement + state hash
                         ↓
        LLM: construct task-specific Behaviors/Failures
                         ↓
        LLM: instantiate verifier candidates + lineage
                         ↓
       high-order common-failure-aware budget selection
                         ↓
       LLM reviewers / agentic execution / test agents
             ┌───────────┴───────────┐
      reproducible failure       sufficient evidence
             ↓                         ↓
 same Codex session continues       allow Stop
```

Deterministic commands are still valuable, but they are evidence used or produced by an agentic
verifier, supplied by the repository, or explicitly configured by the user. They are not a
hardcoded substitute for understanding the task.

## Install once, use in any directory

The recommended distribution is the self-contained Codex plugin. It bundles the Python core, so
users do not install GRAFT separately in every repository.

```bash
codex plugin marketplace add Zongwei9888/GRAFT --ref v0.5.0
codex plugin add graft@graft
```

Start a new Codex thread, open `/hooks`, inspect and trust the three GRAFT hook definitions. GRAFT
then uses its domain-neutral default in Git and non-Git directories alike. No `graft init` is
required for normal use.

Use installation as the global switch:

```bash
codex plugin remove graft@graft  # off globally
codex plugin add graft@graft     # on globally
```

Optional project overrides change budgets, models, policies, or general verifier templates:

```bash
graft init
graft config validate
graft config trust
graft config disable             # off only here
graft config enable
```

Verifier network access is off by default. Environments whose authoritative runtime services are
reachable only over the network may opt in with `graft init --verifier-network-access`; the
generated configuration must still be reviewed and hash-trusted.

A repository configuration is ignored until its exact hash is trusted. An untrusted override
falls back to the built-in dynamic GRAFT Original registry; it never falls back to a task-specific
command list.

See [installation](docs/installation.md), [configuration](docs/configuration.md),
[architecture](docs/architecture.md), and [Codex compatibility](docs/codex-compatibility.md).

## How one checkpoint works

1. `UserPromptSubmit` maintains ordered raw user requirements across the current task epoch.
2. `PostToolUse` stores hashes of tool facts; private Codex transcripts are not parsed.
3. `Stop` freezes the current source state. A producer message such as “done” is not treated as
   evidence and is not classified by task-specific keywords.
4. A read-only ephemeral model call builds Behaviors and Failure Modes from requirements,
   unchanged baseline repository rules, state, and an immutable bounded task-start text diff (including
   in non-Git directories). Candidate-added or modified files cannot introduce a new contract.
   Ambiguous alternatives remain competing hypotheses for discriminating checks rather than being
   silently removed.
5. A second isolated structured call instantiates verifier candidates from general capabilities,
   estimates the chance of producing *eligible reproducible evidence* (not merely noticing a
   suspicious pattern), and describes high-order shared blind spots.
6. The selector greedily maximizes risk-weighted expected detection per unit cost, with a best
   singleton fallback. Shared model, prompt, context, modality, test author, and oracle sources
   reduce apparent complementarity.
7. Selected reviewers and agents run in fresh isolated sessions. A repository-evidence agent can
   derive one direct argv check from visible project declarations, and writable verifier agents
   work only in disposable copies.
8. An observed authoritative-runtime failure, an unchanged baseline-repository oracle, or a
   requirement-derived executable counterexample against the actual candidate can continue the
   producer Codex session. Generated mocks, substitute implementations, candidate-authored
   contracts, source-review suspicions, errors, abstentions, and capability gaps do not become
   blocking evidence.

Reports and bounded immutable task-start text archives are source-bound and stored outside arbitrary target
repositories in the platform's GRAFT state directory. The original producer workspace is checked
again after verification so stale evidence cannot gate a newer state.

## Source checkout

Python 3.11+ is required. GRAFT has no third-party runtime dependency.

```bash
python3 scripts/run_tests.py
python3 scripts/sync_plugin_runtime.py --check

PYTHONPATH=src python3 -m graft.cli status --repo .
PYTHONPATH=src python3 -m graft.cli verify \
  --repo . \
  --requirement "Verify this exact task from its observable behavior"
```

`cli verify` is a standalone diagnostic entry point. During an ordinary Codex task, repair any
continuation evidence and let the Stop hook re-run automatically; invoking `cli verify` inside the
producer thread would bypass the session's `max_feedback_rounds` budget.

The compatibility installer for Codex builds without plugin support is:

```bash
PYTHONPATH=src python3 -m graft.cli install-codex --source-root "$PWD"
```

## Multi-turn sessions

GRAFT is not one-prompt-only. Clarifications and answers remain in one task epoch. A GRAFT
continuation is tagged and excluded from raw user requirements. After a checkpoint is accepted, the
next user request starts a new epoch. Repeated feedback against an unchanged workspace is
suppressed, and feedback rounds are bounded.

## Research status

The implementation exercises the frozen method, but its task-conditional probability estimates
are model estimates until calibrated on held-out tasks. Hidden benchmark labels must remain outside
the online selector. The first three profile-free dynamic Terminal-Bench 3 pairs provide no
positive effectiveness result; see the
[recorded results](experiments/terminal_bench/RESULTS.md). The implementation is therefore a
research prototype, not an established quality improvement.

Claims suitable for WSDM 2027 still require paired multi-task evaluation, calibration studies,
lineage ablations, budget curves, and comparison with native Codex, fixed checklists, single
reviewers, pairwise diversity, and run-all verification.

## Safety boundaries

- graph construction and semantic reviewers are fresh, read-only, ephemeral Codex sessions with
  hooks, user config, and repository rules disabled for isolation;
- writable test agents run only in disposable workspace copies;
- a model cannot make a blocking claim reproducible merely by setting a JSON flag or executing a
  source-display command—GRAFT requires an authoritative runtime artifact, an unchanged baseline
  oracle, or a numbered-requirement-derived check executed against the actual candidate in a
  disposable copy and mapped to the claimed failure mode;
- verifier findings are bound to the exact requirement/config/workspace checkpoint;
- failure policy defaults to fail-open with an explicit unresolved warning;
- the selector never sees hidden benchmark outcomes.
