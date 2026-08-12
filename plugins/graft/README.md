# GRAFT Codex Plugin

This self-contained plugin brings the frozen GRAFT Original baseline and the opt-in value-aware
policy to Codex without modifying the Codex codebase and without per-repository installation.

Its three hooks capture raw multi-turn requirements, hash tool facts, and verify a changed Stop
checkpoint. At verification time, isolated structured LLM calls derive task-specific Behaviors,
Failure Modes, verifier candidates, and high-order shared blind spots. The selector retrieves a
complementary evidence set under budget, then isolated model reviewers, a repository-declared
deterministic-evidence agent, and agentic test/execution verifiers inspect the actual workspace.

No language, framework, game type, repository layout, failure instance, or fixed verifier
combination is encoded in the product path. JSON schemas, sandbox rules, generic capabilities,
lineage fields, and budget policy are fixed protocols.

Normal use requires no `graft init`. An optional trusted project config can override general
models, budgets, or capability templates. If that file is untrusted, GRAFT uses its domain-neutral
built-in registry.

The built-in default remains Original. Enable the uncalibrated optimization explicitly in a target
repository with `cli init --selection-policy value-aware`, review the file, then trust its hash.
The value-aware policy adds an LLM lifecycle gate, producer semantic evidence, No-Op, observed cost
history, task-epoch budgets and executable post-feedback promotion. It contains no task router or
language/benchmark-specific verifier rules.

Python 3.11+ and a compatible Codex CLI are required. After installation, review and trust the
plugin commands through `/hooks`, then start a new Codex thread.

The producer must not manually call `cli verify` after receiving continuation evidence. It should
repair the checkpoint and finish the turn; the Stop hook owns re-verification and enforces the
configured feedback-round budget. Manual verification is reserved for explicit user diagnostics.

For source development:

```bash
python3 scripts/sync_plugin_runtime.py
python3 scripts/sync_plugin_runtime.py --check
```
