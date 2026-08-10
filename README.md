# GRAFT

> Alpha software: the Codex integration is runnable and tested; the bundled calibration rows are
> operational fixtures, not research evidence.

GRAFT is an external verification governor for Codex. It does not modify the Codex model or
agent loop. At a verifiable checkpoint it freezes the current source state, selects a calibrated
verifier subset under cost and set-level false-alarm constraints, executes the subset, and either:

- allows the turn to finish;
- returns reproducible failure evidence as a continuation prompt; or
- marks verification unresolved without pretending that an error or abstention was a pass.

This repository contains the first runnable Codex integration described in the WSDM 2027 plan.
Version 0.4 provides a self-contained Codex plugin, so one installation can govern Codex sessions in
arbitrary workspaces without copying GRAFT into every repository or installing a Python package.
The included calibration rows are **MVP fixtures**, not paper evidence. They must be replaced by
held-out public calibration data before experiments.

## Quick start

The public distribution is a repository-backed Codex marketplace. Pin the release tag for
reproducibility:

```bash
codex plugin marketplace add Zongwei9888/GRAFT --ref v0.4.0
codex plugin add graft@graft
```

Start a new Codex thread, open `/hooks`, review and approve the three GRAFT hook commands, then ask:

```text
Initialize safe GRAFT verifiers for this repository.
```

That explicit initialization discovers project checks and trusts the resulting configuration hash.
Repository-provided verifier commands are never executed before explicit trust. See
[installation](docs/installation.md), [configuration](docs/configuration.md), and
[architecture](docs/architecture.md). Codex distribution details are in
[compatibility](docs/codex-compatibility.md).

## Architecture

```text
Codex turn
    ↓ Stop hook
Session-aware checkpoint policy
    ↓ source + requirement + config hash
Exact empirical-scenario selector
    ↓ cost and set-FPR constraints
Command verifiers / isolated Codex reviewer
    ↓
Evidence gate
    ├── reproducible deterministic failure → continue Codex
    ├── passes                           → allow stop
    └── error/model-only suspicion       → unresolved
```

The same controller is available outside hooks for paper automation.

## Install as a Codex plugin

The recommended product distribution is [`plugins/graft`](plugins/graft). It bundles GRAFT Core,
three lifecycle hooks, and a status/configuration skill. Python 3.11+ is the only runtime
requirement; there is no `pip install` and no per-project hook copy.

For a source checkout, register the repository itself as a local marketplace and install from its
checked-in [marketplace](.agents/plugins/marketplace.json):

```bash
codex plugin marketplace add /absolute/path/to/GRAFT
codex plugin add graft@graft
```

A public Git host uses the same two commands with `Zongwei9888/GRAFT` instead of a local path.
After installation, start Codex, open `/hooks`, review the three plugin commands, approve their
exact hashes, and start a new thread. Hooks from installed plugins are not silently trusted.

The current Codex CLI exposes installation rather than a separate enable flag. Use these as the
plugin switch, then start a new thread:

```bash
codex plugin remove graft@graft  # off globally
codex plugin add graft@graft     # on globally
```

If the alternative user-level hooks below are also installed, removing the plugin disables only
the plugin copy; GRAFT remains active through those user hooks. Run `graft uninstall-codex` as well
for a complete off state. The two forms may safely coexist, but keeping only one avoids a redundant
launcher process per event.

Project behavior can be changed without uninstalling the plugin:

```bash
graft config disable  # off only in this project
graft config enable   # rediscover and enable checks
graft config trust    # approve the current config hash after review
```

A trusted `.graft/config.json` or user profile controls verifier selection, while an unconfigured
non-Git directory remains observe-only. If a project config is untrusted or changes after approval,
GRAFT ignores its commands and uses only the built-in safe Git fallback. Plugin hooks and older
user/project hooks are atomically deduplicated if they coexist.

## Alternative user-level hook installation

From this checkout, run:

```bash
PYTHONPATH=src python3 -m graft.cli install-codex --source-root "$PWD"
```

This earlier distribution remains useful for machines whose Codex build does not support plugins.
The installer:

- builds a managed, non-editable runtime at `~/.local/share/graft/runtime-venv`;
- exposes `graft` and `graft-hook` in `~/.local/bin` without replacing unrelated commands;
- merges three marked handlers into the existing user-level `~/.codex/hooks.json`;
- preserves other user hooks and makes a backup before changing an existing file.

Codex trusts hook definitions by exact hash. After installation, start a Codex session, open
`/hooks`, review the three user-level GRAFT definitions, and approve them. GRAFT deliberately does
not reproduce or bypass Codex's private trust store.

Then, from any directory:

```bash
graft doctor
graft status
```

Every Git repository receives a conservative fallback check (`git diff --check`) automatically.
To opt a project into its own compile/test/lint checks, review and commit a generated configuration:

```bash
cd /path/to/project
graft init
graft config validate
graft status
```

`graft init` currently discovers safe commands for Python, Node, Rust, and Go projects. It never
overwrites an existing `.graft/config.json` unless `--force` is explicit. The generated calibration
rows are operational fixtures for running the product; they are not research measurements and must
not be used in paper results.

Remove only the user-level hook definitions with:

```bash
graft uninstall-codex
```

The managed runtime and central evidence are intentionally retained so uninstall is reversible and
does not destroy experiment records.

## Configuration and state resolution

For each Codex event, GRAFT resolves the actual Git root (or the current directory outside Git) and
uses this precedence:

1. a hash-trusted `<workspace>/.graft/config.json`;
2. the first matching `~/.config/graft/profiles/*.json` user profile;
3. generated safe Git fallback;
4. observe-only mode for an unconfigured non-Git directory.

Session state, hashed tool telemetry, reports, and event-deduplication markers live outside target
repositories. On macOS the default is
`~/Library/Application Support/GRAFT/workspaces/<workspace-id>/`; on Linux it is
`~/.local/state/graft/workspaces/<workspace-id>/`. This keeps arbitrary repositories free of GRAFT
runtime artifacts. Environment overrides are available through `GRAFT_CONFIG_HOME`,
`GRAFT_STATE_HOME`, `GRAFT_INSTALL_HOME`, and `GRAFT_BIN_HOME`.
Session state stores raw user prompts so requirements can be reconstructed across turns; tool inputs
and responses are stored only as hashes. Treat the central state directory as user-private evidence.

If project and user-level hooks both exist, Codex invokes both. A central atomic event claim makes
the second invocation a no-op, preventing duplicate prompts, verifier runs, and feedback loops.

The state model is session-aware rather than one-prompt-only. A Codex clarification and the user's
answer remain in the same task epoch; after an accepted task, the next user request starts a new
epoch and no longer inherits stale requirements. GRAFT-generated continuation prompts are tagged by
hash and never reinterpreted as user requirements.

## Run the source checkout locally

The implementation uses Python 3.11+ and has no runtime dependencies.

```bash
python3 scripts/run_tests.py

PYTHONPATH=src python3 -m graft.cli snapshot \
  --repo . \
  --requirement "Implement GRAFT"

PYTHONPATH=src python3 -m graft.cli verify \
  --repo . \
  --config .graft/config.json \
  --requirement "Implement a source-bound GRAFT governor"
```

An editable installation makes the `graft` command available:

```bash
python3 -m pip install -e .
graft verify --repo . --config .graft/config.json --requirement "..."
```

## Codex CLI adapter

Start a machine-readable Codex turn:

```bash
PYTHONPATH=src python3 -m graft.cli codex-run \
  --repo . \
  --prompt "Inspect the repository without changing files." \
  --sandbox read-only \
  --ephemeral \
  --isolate-config \
  --disable-hooks
```

Continue a persisted thread by adding `--thread-id <ID>` and omitting `--ephemeral`. The adapter
captures the JSONL event stream, final response, thread ID, usage, return code, stderr, and wall time.

Fresh Codex verifier threads use:

- read-only sandbox;
- ephemeral sessions;
- `--ignore-user-config` and `--ignore-rules`;
- `--disable hooks`, preventing recursive GRAFT invocation;
- a versioned JSON Schema verdict.

Model-only findings are non-blocking in the MVP because a fresh thread is not an independent
oracle. A deterministic reproducer or independent confirmation is required before GRAFT blocks.

## Project Stop Hook (development fixture)

The repository also contains `.codex/hooks.json` with `UserPromptSubmit`, `PostToolUse`, and `Stop`
handlers for source-checkout development. Codex requires project hooks to be reviewed and trusted.
The user-level installation above is the recommended path for ordinary use because it does not
require adding hooks to every repository. On another machine, or after changing a hook definition,
open `/hooks` in a new Codex session and approve the definitions again.

The handlers use `.graft/config.json`:

- `UserPromptSubmit` records ordered user requirements and separates GRAFT-generated prompts;
- `PostToolUse` records hashes of tool inputs/responses, avoiding raw secret-bearing output;
- `Stop` runs the checkpoint policy and the selected verifier subset.

The default budget selects `python-compile` and `unit-tests`; it deliberately excludes the more
expensive model reviewer. To exercise the isolated reviewer, adapt
`configs/codex-review-enabled.example.json` and use a budget/FPR setting justified by calibration.

Project wrapper scripts still resolve state and reports through the same central store, so running
both integration forms does not create two session histories.

## Safety and current limitations

- The hook defaults to fail-open on verifier infrastructure errors and surfaces `unresolved`.
- The universal fallback intentionally runs only `git diff --check`. Executing project-defined test
  scripts requires explicit project/profile configuration because those commands are repository
  code, not a safe assumption for every directory.
- Completion detection is a transparent heuristic. Paper experiments should call the controller at
  explicit checkpoints rather than evaluate this heuristic as part of the selector.
- The exact selector is capped at 24 candidates to avoid accidental exponential blow-ups.
- The current tree hash is workspace-scoped and does not rely on a parent Git repository.
- No hidden test label is available to the selector at runtime.
- The sample empirical scenarios are illustrative and must not appear as learned paper results.

Codex automation behavior follows the official documentation for
[`codex exec`](https://learn.chatgpt.com/docs/non-interactive-mode) and
[Codex hooks](https://learn.chatgpt.com/docs/hooks).
