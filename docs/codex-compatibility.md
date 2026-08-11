# Codex compatibility

GRAFT integrates with the official Codex CLI through documented plugins, lifecycle hooks, and
`codex exec`. It does not patch Codex or depend on a private transcript format.

The Codex CLI source, SDK, and App Server are developed in the public `openai/codex` repository.
Official standalone, npm, and Homebrew installations are distribution channels for released CLI
builds; they are not separate GRAFT targets. A source build should be tested at an exact upstream
commit because the repository's main branch may be newer than an official release.

Check compatibility with:

```bash
codex --version
codex plugin --help
graft doctor
```

GRAFT 0.5.0 requires a compatible Codex build exposing
plugin marketplaces, command hooks for `UserPromptSubmit`, `PostToolUse`, and `Stop`, and the
non-interactive JSON event mode with structured output used by the task modeler, registry planner,
and isolated verifiers. Hooks still require user approval. Run the release smoke test against the
exact CLI version reported by `codex --version` rather than assuming compatibility from a stale
version string in this document.

The model service, account authentication, IDE extension, and cloud execution are not bundled with
GRAFT. Open-source CLI code does not turn the hosted Codex model into a local dependency.
