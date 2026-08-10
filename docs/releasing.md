# Releasing

Before the first public release, replace the generic publisher identity in `pyproject.toml`,
`LICENSE`, and `plugins/graft/.codex-plugin/plugin.json` with the final maintainer/team identity.
Once the GitHub slug is fixed, add `homepage`, `repository`, and project URLs rather than publishing
placeholder URLs.

Release procedure:

1. update the semantic version in `pyproject.toml`, `src/graft/__init__.py`, and the plugin manifest;
2. update `CHANGELOG.md` and versioned installation examples;
3. run `python3 scripts/sync_plugin_runtime.py`;
4. run `python3 scripts/run_tests.py`;
5. run `python3 scripts/check_release.py`;
6. validate the plugin with the current Codex plugin validator;
7. create and push an annotated `vX.Y.Z` tag.

The tag workflow builds a wheel and source distribution, performs release checks, and attaches the
artifacts to a GitHub Release. Configure PyPI Trusted Publishing separately before adding automatic
PyPI publication.
