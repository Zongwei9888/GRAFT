# GRAFT Codex Plugin

This is the self-contained Codex distribution of GRAFT. Installing it provides:

- `UserPromptSubmit`, `PostToolUse`, and `Stop` lifecycle hooks;
- a bundled copy of GRAFT Core, so no `pip install` or per-repository setup is required;
- a `graft-verification` skill for status, configuration, and report questions.

The hooks run automatically in Codex sessions. At the end of a coding turn, GRAFT resolves the
workspace configuration, verifies the frozen source checkpoint, and either permits completion or
returns reproducible failure evidence to the same session. It fails open on plugin infrastructure
errors and never treats a model-only suspicion as deterministic proof.

Repository-provided verifier commands are disabled until the user explicitly initializes or trusts
the exact configuration hash. If that file changes, GRAFT falls back to `git diff --check` until it
is reviewed again. Ask Codex to initialize, enable, disable, validate, or trust GRAFT in the current
workspace; the bundled skill invokes the portable launcher.

Python 3.11 or later is required. After installation, review and trust the plugin command hooks
through `/hooks`, then start a new Codex thread.

For local source development, keep the embedded runtime synchronized with GRAFT Core:

```bash
python3 scripts/sync_plugin_runtime.py
python3 scripts/sync_plugin_runtime.py --check
```
