# Installation

GRAFT is available in two equivalent distributions. The Codex plugin is the recommended path;
the Python package is a compatibility path for Codex builds without plugin support and for research
automation.

## Requirements

- Codex CLI with plugin and hook support;
- Python 3.11 or newer (`python3` on macOS/Linux, `py -3` on Windows);
- Git for source-aware checkpoints;
- the project's own build and test tools when project-specific verifiers are enabled.

GRAFT itself has no third-party runtime dependencies.

## Install the plugin from GitHub

Pin a release tag for reproducibility:

```bash
codex plugin marketplace add Zongwei9888/GRAFT --ref v0.5.0
codex plugin add graft@graft
```

Start a new Codex thread, open `/hooks`, inspect the three GRAFT commands, and approve their exact
hashes. Plugin hooks are not silently trusted.

Update or remove it with:

```bash
codex plugin marketplace upgrade graft
codex plugin add graft@graft

codex plugin remove graft@graft
codex plugin marketplace remove graft
```

## Install from a local checkout

For plugin development:

```bash
codex plugin marketplace add /absolute/path/to/GRAFT
codex plugin add graft@graft
```

For the user-level hooks compatibility distribution:

```bash
git clone https://github.com/Zongwei9888/GRAFT.git
cd GRAFT
python3 scripts/install.py
```

The installer creates an isolated runtime under the user data directory, preserves unrelated
Codex hooks, and backs up an existing hooks file before changing it.

Once `codex-graft` is published to PyPI, the package path is:

```bash
pipx install codex-graft
graft install-codex
```

`uv tool install codex-graft` is equivalent. Prefer an isolated tool installer over modifying the
system Python environment.

## Verify installation

```bash
graft --version
graft doctor
graft status
```

Plugin-only users can ask Codex: `Show the GRAFT status for this workspace`. No per-project
initialization is required; the dynamic registry works in arbitrary directories.

The default verifier sandbox has no network access. Projects that need verifiers to reach a live
service can create and review an opt-in override with
`graft init --verifier-network-access`, followed by `graft config trust`.

## Uninstall

Plugin users remove the plugin with `codex plugin remove graft@graft`. Package users run:

```bash
graft uninstall-codex
```

Uninstallation leaves reports and the trust store intact so evidence is not destroyed. Their
platform-specific locations are documented in [configuration.md](configuration.md).
