# Architecture

GRAFT is an external verification governor, not a replacement planner.

```text
Codex session
  ├─ UserPromptSubmit → ordered task requirements
  ├─ PostToolUse      → hashed execution facts
  └─ Stop             → frozen source checkpoint
                              ↓
                    budget/FPR-constrained selector
                              ↓
                    isolated verifier execution
                         ┌────┴────┐
                    allow stop   reproducible failure
                                      ↓
                              same Codex session continues
```

The plugin bundles the same Python core used by the package. `scripts/sync_plugin_runtime.py`
copies the source into the plugin, and CI rejects drift. Session state and reports live outside the
target repository. Event claims deduplicate plugin, user-level, and project hook installations.

Security boundaries:

- project verifier commands require a trusted configuration hash;
- Codex reviewers run in a fresh ephemeral read-only thread with hooks disabled;
- model-only findings are non-blocking unless independently reproduced;
- working directories cannot escape the workspace;
- verifier infrastructure failures are unresolved rather than reinterpreted as passes.

The current exact selector enumerates candidate subsets and is intentionally capped at 24
candidates. The operational calibration bundled with generated configs exists to exercise the
product; academic evaluation must replace it with held-out data.
