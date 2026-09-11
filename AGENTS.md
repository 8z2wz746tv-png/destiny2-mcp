# Agent Instructions

This repository contains a local Destiny 2 MCP server with Bungie OAuth authentication.

For any agent, not only Codex:

- Read `README.md` first for the platform-neutral installation and verification flow.
- Read `skills/destiny2-mcp/SKILL.md` for tool routing and evidence boundaries.
- Read `skills/destiny-mcp-setup/SKILL.md` only for installation, OAuth, registration, or setup troubleshooting.
- Run `scripts/verify_mcp.py` for the real MCP handshake; do not treat registration alone as proof of readiness.

## Skill maintenance

`skills/destiny2-mcp/` is the single source for the agent-facing guide. Nothing in it may assume one host: the same folder is loaded by hosts with a skills directory, and hosts without one get a pointer block plus the public URL advertised in the MCP handshake.

- After editing anything under `skills/destiny2-mcp/`, run `scripts/install_skill.py` so the installed copies keep up; `--list` shows which hosts were detected, `--pointer` refreshes the instruction-file pointers.
- Never write into a directory the host manages itself (for example Cursor's `skills-cursor`); use `--target` or `--pointer <file>` for unlisted hosts.
- `tests/test_skill_contracts.py` checks `references/routing.md` against the code: intent coverage, parameter ownership, write intents, community categories. A red test there means the document is stale, not that the check is too strict.
- `tests/test_skill_install.py` checks the delivery layer: pointer idempotence, mirror sync, and that `config.ROUTING_GUIDE_URL` matches the installer's URL.

## Setup-related tasks

For installation, reinstallation, OAuth login, Codex MCP registration, or setup troubleshooting:

1. Read `skills/destiny-mcp-setup/SKILL.md` completely before taking action.
2. Follow that Skill through a real MCP handshake and verification; registration alone is not sufficient.
3. Run `skills/destiny-mcp-setup/scripts/verify_mcp.py` and require `BUNGIE_PROFILE_CHECK=ok` plus all eight tools in the normal profile.
4. Preserve unrelated worktree changes and inspect existing `.env`, OAuth tokens, and Codex MCP entries before changing them.

Never print or request secrets in chat. This includes `.env` contents, Bungie API keys, OAuth client secrets, authorization codes, access tokens, refresh tokens, and callback URLs containing authorization codes. Ask users to enter credentials locally in `.env`.

After registering or changing the MCP server, tell the user to restart Codex or open a new task so the new server is discovered.
