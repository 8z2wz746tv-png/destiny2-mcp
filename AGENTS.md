# Agent Instructions

This repository contains a local Destiny 2 MCP server with Bungie OAuth authentication.

For any agent, not only Codex:

- Read `README.md` first for the platform-neutral installation and verification flow.
- Read `skills/destiny2-mcp/SKILL.md` for tool routing and evidence boundaries.
- Read `skills/destiny-mcp-setup/SKILL.md` only for installation, OAuth, registration, or setup troubleshooting.
- Run `scripts/verify_mcp.py` for the real MCP handshake; do not treat registration alone as proof of readiness.

## Skill maintenance

`skills/destiny2-mcp/` is the single source for the agent-facing guide. Nothing in it may assume one host: the same folder is loaded by hosts with a skills directory, and hosts without one get a pointer block plus the public URL advertised in the MCP handshake.

- After editing anything under `skills/destiny2-mcp/`, run `scripts/install_skill.py` so the installed copy keeps up. It installs into **the host that is running you** by default (DSH: `~/.dsh/skills/`, Claude Code: `~/.claude/skills/`, Codex: `~/.codex/skills/`, detected from the environment); `--list` shows the current host, `--host <name>` picks one, `--all` installs into every detected host, `--pointer` refreshes the instruction-file pointers, and `--mcp` registers the MCP server (writes the DSH profile patch; prints the paste-ready command for the others).
- A new user installs this for the agent they are talking to, not for every agent on the machine: do not spread copies into Codex and Claude "just in case".
- Never write into a directory the host manages itself (for example Cursor's `skills-cursor`); use `--target` or `--pointer <file>` for unlisted hosts.
- `tests/test_skill_contracts.py` checks `references/routing.md` against the code: intent coverage, parameter ownership, write intents, community categories. A red test there means the document is stale, not that the check is too strict.
- `tests/test_skill_install.py` checks the delivery layer: pointer idempotence, mirror sync, and that `config.ROUTING_GUIDE_URL` matches the installer's URL.
- `destiny_mcp/error_codes.py` is the only place `error.code` may come from: literal codes live in `ErrorCode`, exception classes are turned into codes by `code_for_exception()` (the class name *is* the contract), and write failures use `write_failed(intent)`. `tests/test_error_codes.py` rejects bare string codes and pins the exception→code mapping.
- `destiny_mcp/vocabulary.py` is the only place Chinese labels live (six stats, classes, locations, elements, legacy stat names). Labels are the Manifest's official Chinese strings; input aliases may be many but must resolve to one key. `tests/test_vocabulary.py` pins the official names, checks aliases round-trip (legacy names must point at *existing* labels), and rejects re-copied tables.
- `tests/test_conclusion_paths.py` guards diagnosis/conclusion output: a conclusion module may fall back to weaker wording, but never silently — the `except` must re-raise or leave a trace (append / reason / log). Add new conclusion modules to `_CONCLUSION_MODULES` there.
- `tests/test_architecture_layers.py` guards the module graph: imports may only go downwards (`_LAYERS`), no two modules may import each other, and every `svc["…_svc"]` key used in `tools/` must be declared in `service_context.ServiceContext`. A new top-level module must be registered there, otherwise the test fails on purpose — decide its layer instead of letting the rule silently skip it.

## Setup-related tasks

For installation, reinstallation, OAuth login, Codex MCP registration, or setup troubleshooting:

1. Read `skills/destiny-mcp-setup/SKILL.md` completely before taking action.
2. Follow that Skill through a real MCP handshake and verification; registration alone is not sufficient.
3. Run `skills/destiny-mcp-setup/scripts/verify_mcp.py` and require `BUNGIE_PROFILE_CHECK=ok` plus all eight tools in the normal profile.
4. Preserve unrelated worktree changes and inspect existing `.env`, OAuth tokens, and Codex MCP entries before changing them.

Never print or request secrets in chat. This includes `.env` contents, Bungie API keys, OAuth client secrets, authorization codes, access tokens, refresh tokens, and callback URLs containing authorization codes. Ask users to enter credentials locally in `.env`.

After registering or changing the MCP server, tell the user to restart Codex or open a new task so the new server is discovered.

## 提交信息格式

一条提交 = **一行标题**，不写正文（细节在 CHANGELOG、计划文档与代码注释里）。

    <类型>：<做了什么>

- 类型用中文，现有取值：`版本号`（如 `0.2.0：…`）、`修复`、`重构`、`文档`、`语料`、`CI`、`错误码`、`词表`、`信封`、`兼容面`、`结论路径`，以及计划阶段的 `护甲 P3` / `武器 P4`。
- 标题写"做了什么"，不写「抓出的 XXX」这类腔调；不用 emoji、结尾不加句号。
- **一次提交只做一件事**：跨主题的改动拆成多条；发布提交也一样——内容照发，标题只留一行。
- 破坏性变更在标题里点明（例：`活动统计改为行式（破坏性）`），影响面写进 CHANGELOG。
