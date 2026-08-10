---
name: graft-verification
description: Inspect, configure, or explain the GRAFT verification governor for Codex. Use when the user asks about GRAFT status, verifier configuration, checkpoint evidence, reports, enablement, or project initialization. GRAFT lifecycle hooks run automatically and do not require this skill for ordinary coding tasks.
---

# GRAFT Verification Governor

GRAFT is an external evidence gate. It does not plan or repair code for Codex. Its lifecycle hooks
record the task and tool facts, then verify changed source state when Codex is ready to stop.

## Commands

Resolve the plugin root as the directory two levels above this `SKILL.md`. Run the bundled launcher
with an absolute path so the command works from any repository:

```bash
python3 <plugin-root>/scripts/graft_plugin.py cli status --repo .
python3 <plugin-root>/scripts/graft_plugin.py cli doctor --repo .
python3 <plugin-root>/scripts/graft_plugin.py cli config validate --repo .
python3 <plugin-root>/scripts/graft_plugin.py cli verify --repo . --requirement "..."
```

Only run the following command when the user asks to configure or enable project-specific checks,
because it creates `.graft/config.json`:

```bash
python3 <plugin-root>/scripts/graft_plugin.py cli init --repo .
```

Only trust a repository configuration after showing the user its path, verifier commands, and
validation result. Trust is bound to the configuration hash and is automatically revoked on edits:

```bash
python3 <plugin-root>/scripts/graft_plugin.py cli config trust --repo .
python3 <plugin-root>/scripts/graft_plugin.py cli config untrust --repo .
```

For a project-only switch, use `cli config enable` or `cli config disable`. These do not install or
remove the global plugin.

## Interpretation

- `project`: use the repository's reviewed and hash-trusted `.graft/config.json`.
- `profile:*`: use a matching user profile.
- `safe-git`: run only the conservative `git diff --check` fallback.
- `observe`: collect session facts but do not enforce verification.
- an untrusted or changed project config never executes commands; Git workspaces use `safe-git`.
- `allow`: selected verifiers passed; this is evidence, not proof of correctness.
- `continue_with_evidence`: a reproducible blocking failure should be returned to the same Codex
  task for autonomous repair.
- `unresolved`: never reinterpret an error, abstention, or model-only suspicion as a pass.

Do not describe generated calibration fixtures as paper results. Research claims require held-out
calibration and hidden evaluation labels that are unavailable to the online selector.
