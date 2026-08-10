# Security policy

GRAFT is alpha software that executes explicitly configured local verification commands. Review
project configuration before trusting it and keep the default fail-open policy until the verifier
environment is understood.

Report vulnerabilities through GitHub Private Vulnerability Reporting after the repository is
published. Do not open a public issue for command injection, trust bypass, path escape, credential
exposure, or sandbox bypass reports.

Supported security fixes currently target the latest minor release. GRAFT does not bypass Codex
hook approval, sandbox, or workspace policy. The local state store may contain raw user prompts and
repository paths and should be protected as user-private data.
