# Terminal-Bench evaluation

This directory contains Harbor adapters for paired `Native Codex` and
`Codex + GRAFT` trials. Harbor gives each trial a clean `CODEX_HOME`; the GRAFT
condition installs the released plugin and a task-specific public verifier
profile before delegating to the same Harbor Codex implementation used by the
native condition.

The evaluation keeps the benchmark evaluator isolated from GRAFT:

1. Codex sees the public task environment and instruction.
2. GRAFT may run only profile verifiers derived from the public contract.
3. Harbor runs hidden tests only after Codex and GRAFT have stopped.

Hidden tests and oracle source must never be copied into a GRAFT profile or used
for online selection and feedback.

## Requirements

- Harbor 0.20.0 or a compatible newer release
- Docker
- Codex credentials supported by Harbor
- network access while Harbor installs the pinned Codex and GRAFT releases

The adapter currently pins GRAFT `v0.4.0`. The job configuration also pins the
Codex CLI, model, reasoning effort and Harbor dataset content hash.

## Terminal-Bench 3 paired pilot

The first paired pilot uses the new CPU-only, single-container
`terminal-bench/session-window-debug` task. Its dataset and task identities are:

```text
Terminal-Bench 3 dataset revision: 10
dataset sha256: 88433fbcecd1a3f81f7a71bff4cc76c394d0edbefb7e028f90d4109b639fefba
task sha256:    638c00fd438a0289ba75f6bc536861831f4a8eab2b85064064038e1bcc91cfbb
```

First validate the task environment with the official oracle:

```bash
harbor run \
  --dataset terminal-bench/terminal-bench-3@sha256:88433fbcecd1a3f81f7a71bff4cc76c394d0edbefb7e028f90d4109b639fefba \
  --include-task-name terminal-bench/session-window-debug \
  --agent oracle \
  --jobs-dir experiments/terminal_bench/jobs \
  --job-name tb3-session-window-oracle-r10 \
  --n-concurrent 1
```

Then run the paired conditions. A custom agent import requires the repository
root on `PYTHONPATH`. The credential path is provided only at runtime and is not
stored in the checked-in config:

```bash
PYTHONPATH="$PWD" harbor run \
  --config experiments/terminal_bench/configs/session-window-debug-pair-r10.json \
  --agent-setup-timeout-multiplier 3 \
  --agent-env CODEX_AUTH_JSON_PATH=/absolute/path/to/.codex/auth.json
```

The pair config runs one native trial and one GRAFT trial sequentially with the
same task, Codex CLI 0.147.0, `gpt-5.6-sol`, high reasoning, container resources
and hidden evaluator. GRAFT's additional verification time and any continuation
tokens remain part of the treatment cost.

`--agent-setup-timeout-multiplier` changes only the allowance for downloading
the pinned runtimes. It does not increase Codex execution or verifier budgets.
Use it on both conditions when running a fresh pair; otherwise transient package
mirror latency can censor one arm before model execution.

## Public verifier profile

[`profiles/session-window-debug`](profiles/session-window-debug) contains three
candidates built before inspecting hidden tests or oracle source:

- CPython compile checking;
- independent public behavioral probes for merge, retention and watermark
  progress;
- SHA-256 integrity checking for task-declared read-only files.

The fixture scenario matrix makes the exact selector choose the behavioral and
integrity checks under a 1.9-unit budget. The original broken implementation
fails the behavioral verifier, while the official oracle output passes it and
the integrity check. These are operational fixtures, not paper calibration
data; paper results require held-out mutation calibration.

The adapter materializes this configuration as a matched user profile under
`GRAFT_CONFIG_HOME`, outside `/app`. It does not add `.graft/config.json` to the
benchmark workspace. This prevents ordinary repository discovery from exposing
the verifier list to Codex and keeps configuration files out of the source
snapshot. It is visibility separation, not a hostile security boundary: the
paper protocol must run the selector and private verifier definitions in a host
controller or sidecar that the generator cannot inspect.

## Evidence handling

Raw Harbor jobs are ignored by Git because they contain local execution
metadata and may contain sensitive agent logs. GRAFT writes ordinary reports to
each trial's `agent/graft-state/`. The adapter additionally creates
`agent/graft-state.tar.gz` and its SHA-256 checksum before the container is
removed, preserving exact JSON bytes even if Harbor redacts text log copies.

See [the recorded results](RESULTS.md). The earlier Terminal-Bench 2.0 run is an
integration smoke test; the Terminal-Bench 3 pair is the first comparative
pilot, not a statistically powered effectiveness result.
