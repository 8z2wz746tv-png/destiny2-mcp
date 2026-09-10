---
name: destiny2-mcp
description: Use the local Destiny 2 MCP for account queries, community build research, inventory and perk checks, loadouts, and confirmed equipment actions. Read this when an agent connects to this repository or needs to choose between account, Manifest, and Starside data.
metadata:
  short-description: Route Destiny 2 MCP queries safely across agents
---

# Destiny 2 MCP

This is a platform-neutral usage guide. An agent may read this file directly from GitHub; it does not require Codex-specific Skill installation.

## First decision: what evidence does the user want?

- Player/account state, owned items, current rolls, or saved loadouts: use an account-aware intent.
- All game definitions or possible rolls: use the Manifest/catalog intent; this does not prove ownership.
- Community explanations or tables: use the relevant `community` intent; results can come from bundled author Markdown or an optional local web archive and are not official recommendations or live search.
- Community builds require the optional archive and a returned `community_build_id`. The bundled Markdown contains reference material and weapon recommendations, not complete character builds.
- A write such as move, equip, save, delete, or modify: read the current state first, show the exact target, and wait for explicit confirmation before sending `confirmed=true`.

Read only the relevant reference before a complex request:

- [routing.md](references/routing.md) for the per-tool intent index, the parameter rules, community categories, and the rating scales.
- [evidence-and-completeness.md](references/evidence-and-completeness.md) for scope, pagination, missing data, and warnings.
- [community-builds.md](references/community-builds.md) for Starside templates and inventory matching.
- [account-loadouts.md](references/account-loadouts.md) for local and Bungie official loadouts.
- [execution-safety.md](references/execution-safety.md) before any operation that can change game or local account state.

## Non-negotiable boundaries

1. Never infer account ownership from Manifest results, community templates, or a previous conversation.
2. Never call `loadout_assistant` to answer a community-build question.
3. Never call `catalog` to answer what the player owns; use `filter_rolls` or an account inventory query.
4. `build_template`, community records, `farm_options`, and `solver_handoff` are not executable builds.
5. Only a server-returned, instance-bound `canonical_build` may be sent to `equip_build`.
6. If a response is incomplete, failed, or has an uncertainty/coverage warning, report that limitation instead of filling it from model memory.
7. A parameter the chosen `intent` does not read is rejected with `ignored_parameter`; switch to the intent named in the message instead of retrying the same call.
8. The `community` intent of `weapon`, `build`, `subclass` and `activity` searches only its own category. A zero result there does not mean the archive lacks the topic; retry with `world_assistant(intent="community")` and name the category searched.

## Installation handoff

This file explains usage, not platform registration. Follow the repository's `README.md` and `skills/destiny-mcp-setup/SKILL.md` for installation and OAuth. If the host does not support Codex Skills, load this Markdown as project instructions and use its MCP configuration method.
