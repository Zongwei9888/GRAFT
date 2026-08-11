# Terminal-Bench evaluation

This directory contains reproducible Harbor adapters for `Native Codex` and
`Codex + GRAFT`. Hidden tests run only after the agent has stopped and are never
available to GRAFT's online graph builder, selector, or verifiers.

The current treatment is GRAFT Original v0.5: no task profile, command list,
fixture, or benchmark-specific verifier is installed. At a changed Stop
checkpoint, the plugin derives Behaviors and Failure Modes from the raw public
instruction and workspace, retrieves task-specific verifier candidates from
four domain-neutral capability templates, models their shared lineage, and
selects a set under budget. Any reproducible failure is returned to the same
Codex session.

## Requirements

- Harbor 0.20.0 or a compatible newer release;
- Docker;
- Codex credentials supported by Harbor;
- network access while the pinned Codex and GRAFT sources are installed.

Raw jobs under `experiments/terminal_bench/jobs/` are ignored by Git because
they contain local execution metadata and may contain sensitive agent logs.

## GRAFT Original v0.5 pilot

The first no-profile pilot uses the CPU-only Terminal-Bench 3 task
`terminal-bench/html-js-filter`:

```text
Terminal-Bench 3 dataset revision: 10
dataset sha256: 88433fbcecd1a3f81f7a71bff4cc76c394d0edbefb7e028f90d4109b639fefba
task sha256:    832a5b309edca4f1a7c728da5f1ca530c2712f20a0b7f1db6d1bb6e3171a8866
task checksum:  80fd6f91a2d84448f6f4df8b1a5d4e0f2d8824172b22d6a44ae05cbdcbec148c
```

Validate the environment with the official oracle:

```bash
harbor run \
  --dataset terminal-bench/terminal-bench-3@sha256:88433fbcecd1a3f81f7a71bff4cc76c394d0edbefb7e028f90d4109b639fefba \
  --include-task-name terminal-bench/html-js-filter \
  --agent oracle \
  --jobs-dir experiments/terminal_bench/jobs \
  --job-name tb3-html-js-filter-oracle-r10 \
  --n-concurrent 1
```

Then run the GRAFT treatment and native control. The repository root is placed
on `PYTHONPATH` only so Harbor can import the local adapters. The credential
path is supplied at runtime and is absent from checked-in configs:

```bash
PYTHONPATH="$PWD" harbor run \
  --config experiments/terminal_bench/configs/html-js-filter-graft-original-r10.json \
  --agent-setup-timeout-multiplier 3 \
  --agent-timeout-multiplier 3 \
  --agent-env CODEX_AUTH_JSON_PATH=/absolute/path/to/.codex/auth.json \
  --yes

PYTHONPATH="$PWD" harbor run \
  --config experiments/terminal_bench/configs/html-js-filter-native-r10.json \
  --agent-setup-timeout-multiplier 3 \
  --agent-timeout-multiplier 3 \
  --agent-env CODEX_AUTH_JSON_PATH=/absolute/path/to/.codex/auth.json \
  --yes
```

Both arms pin Codex CLI 0.147.0, `gpt-5.6-sol`, high reasoning, the same task
digest, and the same timeout multipliers. GRAFT's model calls, verifier calls,
continuations, time, and tokens are treatment cost; they are not added to the
native arm.

[`graft_original_codex_agent.py`](graft_original_codex_agent.py) installs the
exact recorded GRAFT Git commit and asserts that the external profile directory
contains no files. It archives the runtime state and a SHA-256 checksum before
Harbor deletes the container. This demonstrates absence of a task profile; it
is not a hostile security boundary because producer and governor still share a
container. A paper-scale experiment should place the controller in a host
process or sidecar.

## Historical v0.4 profile pilot

`session-window-debug-pair-r10.json` and `profiles/session-window-debug/` are
frozen negative historical artifacts. They used a hand-authored public verifier
profile and must not be loaded by the v0.5 product runtime. The pilot showed
that narrow fixtures can report full empirical coverage while missing latent
behaviors. They remain solely for reproducibility and for comparison with the
dynamic original-method implementation.

See [RESULTS.md](RESULTS.md) for scored outcomes, integration failures, costs,
and post-hoc analyses.
