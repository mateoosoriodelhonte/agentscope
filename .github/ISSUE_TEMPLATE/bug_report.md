---
name: Bug report
about: Something in AgentScope does not work as documented
title: ""
labels: bug
assignees: ""
---

<!--
Security vulnerabilities must not be reported here. Use a private GitHub security advisory:
https://github.com/mateoosoriodelhonte/agentscope/security/advisories/new (see SECURITY.md).

Do not paste API keys, tokens, database URLs with passwords, or private task data. AgentScope
output is redacted, but check before you paste.
-->

## What happened

A clear description of the problem.

## What you expected

What should have happened, with a link to the documentation that describes it if there is one.

## How to reproduce

Commands, dataset or configuration (synthetic data only), and the steps that trigger the
problem:

```sh
uv run agentscope ...
```

## Output

The relevant output, error message and exit code (`echo $?`). For dashboard problems, the
browser console and the failing request (status code and response body).

## Environment

- `agentscope version`:
- Commit (if running from a checkout):
- Operating system:
- Python version:
- Node.js version (dashboard problems):
- Storage: SQLite / PostgreSQL (version)
- Provider: fake / Ollama (version, model) / OpenAI-compatible
- Relevant `AGENTSCOPE_*` settings (names and non-secret values only):
