# Configuration

GRAFT resolves configuration in this order:

1. a reviewed and trusted `<workspace>/.graft/config.json`;
2. the first matching user profile in the GRAFT config directory;
3. the built-in `git diff --check` fallback for Git repositories;
4. observe-only mode outside configured Git repositories.

## Safe project setup

```bash
graft init
graft config validate
graft status
```

`graft init` discovers conservative commands for Python, Node, Rust, and Go and writes
`.graft/config.json`. Because the command is explicit, the generated configuration is also trusted.

Configurations committed by a repository are not trusted automatically. Review one, then run:

```bash
graft config validate
graft config trust
```

Trust binds the absolute workspace and the SHA-256 hash of the configuration. Any edit revokes
trust automatically. Until it is trusted again, GRAFT ignores its commands and uses the safe Git
fallback.

## Per-project switch

```bash
graft config disable
graft config enable
```

This leaves the plugin installed for other repositories. Disabling creates a trusted project-level
off override; enabling rediscovers safe checks if that override contains no verifiers.

## User profiles

Profiles let one reviewed configuration apply to matching repositories without copying it into
each repository. Start from an initialized project:

```bash
graft profile create python-default \
  --from-config .graft/config.json \
  --files-all pyproject.toml

graft profile list
```

Available matchers are repeatable `--files-all`, repeatable `--files-any`, and `--path-regex`.
At least one is required. Profiles are stored under:

- macOS/Linux: `~/.config/graft/profiles/` unless `XDG_CONFIG_HOME` is set;
- Windows: `%APPDATA%\GRAFT\profiles\`;
- override: `GRAFT_CONFIG_HOME`.

## Main fields

| Field | Meaning |
|---|---|
| `enabled` | Project-level execution switch |
| `budget` | Maximum total configured verifier cost |
| `max_set_fpr` | Maximum calibrated set-level false-positive rate |
| `checkpoint_mode` | `completion`, `strict`, or `explicit` |
| `max_feedback_rounds` | Maximum continuation rounds for one task epoch |
| `failure_policy` | `open` reports infrastructure uncertainty; `closed` blocks on it |
| `verifiers` | Command and read-only Codex-review candidates |
| `calibration` | Failure-detection and clean false-alarm scenario rows |

The machine-readable schema is [`schemas/graft_config.schema.json`](../schemas/graft_config.schema.json).
Generated calibration rows are operational fixtures. They are not research measurements.

## State and privacy

Raw user prompts are kept in the local session store so multi-turn requirements can be reconstructed.
Tool inputs and outputs are stored only as hashes. Default state locations are:

- macOS: `~/Library/Application Support/GRAFT/`;
- Linux: `~/.local/state/graft/`;
- Windows: `%LOCALAPPDATA%\GRAFT\`.

Override them with `GRAFT_STATE_HOME`, `GRAFT_CONFIG_HOME`, `GRAFT_INSTALL_HOME`, and
`GRAFT_BIN_HOME`. Do not publish the state directory: it may contain user prompts and repository
paths.
