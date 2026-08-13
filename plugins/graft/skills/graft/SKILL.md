---
name: graft-verification
description: Inspect, configure, enable, disable, or explain the GRAFT verification governor for Codex, including the frozen Original baseline and opt-in value-aware policy. Use when the user asks about GRAFT status, dynamic verifier construction, checkpoint evidence, reports, or project policy. Hooks observe lifecycle boundaries; packaged verification is explicit until automatic triggering is calibrated.
---

# GRAFT Verification Governor

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

Normal use in an arbitrary directory needs no initialization. The packaged fallback is explicit:
the original coding request must contain the exact `[graft:verify]` protocol token before GRAFT can
purchase verification. `completion` and `strict` are controlled research modes, not proven product
defaults. Only run `cli init` when the user asks for a versioned project override of budgets,
models, policies, or general verifier templates:

```bash
python3 <plugin-root>/scripts/graft_plugin.py cli init --repo .
python3 <plugin-root>/scripts/graft_plugin.py cli init --repo . --checkpoint-mode completion
python3 <plugin-root>/scripts/graft_plugin.py cli init --repo . --selection-policy value-aware
```

During an ordinary coding task, never run `cli verify` in response to GRAFT continuation
evidence. Repair the evidenced behavior and finish the turn normally. The Stop hook will verify
the new checkpoint automatically and enforce `max_feedback_rounds`; manual verification would
bypass that session budget. Use `cli verify` only when the user explicitly asks for a standalone
manual verification.

Show the generated path and validation result before trusting a repository override:

```bash
python3 <plugin-root>/scripts/graft_plugin.py cli config trust --repo .
python3 <plugin-root>/scripts/graft_plugin.py cli config untrust --repo .
```

`cli config enable` and `cli config disable` are project-only switches.

Interpretation:

- `graft-original-default`: domain-neutral runtime modeling and dynamic verifier retrieval;
- `graft-value-aware`: explicit research opt-in with completion gating, No-Op, semantic producer
  evidence, observed costs, task-epoch budgets, and post-feedback promotion;
- `project`: reviewed v2 project policy bound to an exact hash;
- `profile:*`: a matching reviewed user policy;
- `allow`: selected task-specific evidence passed within the residual-risk threshold;
- `continue_with_evidence`: a reproducible blocking failure returns to the producer Codex session;
- `unresolved`: evidence is incomplete, errored, abstained, model-only, or lacks capability.

Never turn `unresolved` into a pass. Never call model identities independent merely because they
run in fresh threads. Research claims require held-out calibration and evaluation labels that are
unavailable to the online selector.
