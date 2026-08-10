# Contributing

Issues and focused pull requests are welcome. Before changing behavior, explain the affected
verification invariant and include a regression test.

```bash
python3 scripts/run_tests.py
python3 scripts/check_release.py
python3 scripts/sync_plugin_runtime.py --check
```

If core source changes, run `python3 scripts/sync_plugin_runtime.py` and commit the synchronized
plugin runtime. Do not commit `.graft` reports, session state, local trust stores, credentials, or
Codex transcripts.

Research contributions must distinguish operational fixture probabilities from measurements made
on a frozen calibration split. Hidden evaluation labels must never enter online selection.
