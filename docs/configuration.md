# Configuration

GRAFT resolves configuration in this order:

1. a reviewed and hash-trusted `<workspace>/.graft/config.json`;
2. the first matching user profile;
3. the built-in domain-neutral `graft-original` registry.

The third option works in any directory. Because the automatic completion trigger and selector are
not yet calibrated, the packaged fallback uses `checkpoint_mode: explicit`: ordinary chat and
coding turns only cross the zero-model-cost hook boundary, and verification is considered only when
the original task includes the exact `[graft:verify]` protocol token. Initialization is optional
and never discovers a language-specific command checklist.

## Optional project override

```bash
graft init                              # safe explicit mode
graft init --checkpoint-mode completion # controlled research mode
graft config validate
graft config trust
graft status
```

The frozen Original baseline is the default. The uncalibrated optimization is an explicit opt-in:

```bash
graft init --selection-policy value-aware
graft config validate
graft config trust
```

Network access for verifier agents is disabled by default. If the authoritative runtime under test
is a network service, initialize explicitly with:

```bash
graft init --verifier-network-access
graft config validate
graft config trust
```

This enables network access only for workspace-write verifier templates; read-only semantic model
stages remain network-disabled. Treat it as an environment capability and security decision, not a
task-specific verifier.

`graft init` writes the same general registry locally so a project can version its chosen budget,
models, sandbox policy, or verifier capabilities. Any edit revokes trust automatically. Until it is
trusted again, GRAFT ignores the file and uses the general built-in registry.

```bash
graft config disable  # project-only off switch
graft config enable
```

## Main v2 fields

| Field | Meaning |
|---|---|
| `method` | `graft-original` or the opt-in `graft-value-aware` |
| `enabled` | Project-level execution switch |
| `budget` | Maximum nominal verifier cost; value-aware accumulates it per task epoch |
| `checkpoint_mode` | `explicit` (safe default), or uncalibrated `completion`/`strict` research mode |
| `max_feedback_rounds` | Bounded continuations within one task epoch |
| `failure_policy` | `open` warns on unresolved; `closed` continues on unresolved |
| `modeling` | Models, timeouts, prompt provenance, and the value-aware completion gate |
| `verifier_templates` | General capabilities and isolation policy, not task instances |
| `verifier_templates[].network_access` | Explicit network policy for a verifier sandbox |
| `selection.strategy` | Frozen `original` or opt-in `value-aware` |
| `selection` | Hypergraph, net-value, uncertainty, cost, and resource-budget parameters |

The runtime modeler creates Behaviors and Failure Modes. The planner creates concrete verifier IDs,
objectives, prompts, target edges, actionable-detection estimates, lineage additions, and
shared-blind-spot scenarios. “Detection” here means producing evidence eligible for Stop feedback,
not simply expressing a source-level concern. Non-blocking candidates therefore contribute zero
Stop-gating utility. These task instances do not belong in a static project config.

The machine-readable schemas are in [`schemas/`](../schemas/). Config version 1 is intentionally
rejected by the product loader because it represented the fixed-fixture prototype.

## User profiles

Profiles allow a reviewed v2 override to match multiple repositories:

```bash
graft profile create team-policy \
  --from-config .graft/config.json \
  --files-all AGENTS.md
graft profile list
```

Profiles may match `files_all`, `files_any`, or `path_regex`. They live under the platform GRAFT
config directory or `GRAFT_CONFIG_HOME`.

## State and privacy

Raw user prompts, the task-start per-file hash manifest, and a bounded immutable task-start text archive
are retained locally to reconstruct multi-turn requirements, produce bounded semantic diffs, and
distinguish baseline authority from candidate-authored artifacts. Tool inputs and responses are
stored as integrity hashes plus bounded redacted previews, outcomes, paths and durations. Reports
contain model-derived task structures and evidence, so the state directory remains user-private.

The workspace state directory also contains content-free verifier cost observations keyed by
generic template ID. If token or currency usage is unavailable from Codex, it remains `unknown` and
is counted as such rather than converted to zero.

- macOS: `~/Library/Application Support/GRAFT/`;
- Linux: `~/.local/state/graft/`;
- Windows: `%LOCALAPPDATA%\GRAFT\`.

Overrides: `GRAFT_STATE_HOME`, `GRAFT_CONFIG_HOME`, `GRAFT_INSTALL_HOME`, and `GRAFT_BIN_HOME`.
