---
name: graft-verification
description: Inspect, configure, enable, disable, or explain the GRAFT Original verification governor for Codex. Use when the user asks about GRAFT status, dynamic verifier construction, checkpoint evidence, reports, or project policy. Lifecycle hooks run automatically during ordinary coding tasks.
---

# GRAFT Original Verification Governor

GRAFT is an external evidence gate, not Codex's planner. On a changed Stop checkpoint, isolated
structured model calls derive task-specific Behaviors, Failure Modes, verifier objectives, and
shared blind spots from raw multi-turn requirements and observable workspace state. Do not describe
GRAFT as a fixed test checklist or infer verifier behavior from a language/framework name.

Resolve the plugin root as the directory two levels above this `SKILL.md`, then use the portable
launcher:

```bash
python3 <plugin-root>/scripts/graft_plugin.py cli status --repo .
python3 <plugin-root>/scripts/graft_plugin.py cli doctor --repo .
python3 <plugin-root>/scripts/graft_plugin.py cli config validate --repo .
python3 <plugin-root>/scripts/graft_plugin.py cli verify --repo . --requirement "..."
```

Normal use in an arbitrary directory needs no initialization. Only run `cli init` when the user
asks for a versioned project override of budgets, models, policies, or general verifier templates:

```bash
python3 <plugin-root>/scripts/graft_plugin.py cli init --repo .
```

Show the generated path and validation result before trusting a repository override:

```bash
python3 <plugin-root>/scripts/graft_plugin.py cli config trust --repo .
python3 <plugin-root>/scripts/graft_plugin.py cli config untrust --repo .
```

`cli config enable` and `cli config disable` are project-only switches.

Interpretation:

- `graft-original-default`: domain-neutral runtime modeling and dynamic verifier retrieval;
- `project`: reviewed v2 project policy bound to an exact hash;
- `profile:*`: a matching reviewed user policy;
- `allow`: selected task-specific evidence passed within the residual-risk threshold;
- `continue_with_evidence`: a reproducible blocking failure returns to the producer Codex session;
- `unresolved`: evidence is incomplete, errored, abstained, model-only, or lacks capability.

Never turn `unresolved` into a pass. Never call model identities independent merely because they
run in fresh threads. Research claims require held-out calibration and evaluation labels that are
unavailable to the online selector.
