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

## 写代码的习惯

这些不是风格偏好，是踩过的坑换来的。能自动化的都配了守门测试——改代码前**先看它守什么**。

### 单一出处：一个事实只写一次

| 事实 | 唯一出处 | 守门 |
| --- | --- | --- |
| 错误码 | `destiny_mcp/error_codes.py` | `tests/test_error_codes.py`（禁止裸字符串；异常类名即契约） |
| 中文词表（六维/职业/位置/元素/旧名） | `destiny_mcp/vocabulary.py` | `tests/test_vocabulary.py`（官方名钉住、旧名必须指向存在的规范名、禁止再抄） |
| 活动统计形状 | `destiny_mcp/activity_stats.py` | `tests/test_activity_stats.py`（上游键清单当夹具，拼错或新增会红） |
| intent 取值 / 写作清单 | `tools/_requests.py` | `tests/test_skill_contracts.py`、`tests/test_ignored_parameters.py` |
| 参数归属 | `tools/_param_contracts.py` | 同上（认领了必须真读、没认领必须拒收） |
| 响应信封 | `tools/_responses.py` | 语料 `sweep` 组的信封规则（110 个 intent 全查） |
| 服务容器 | `service_context.py` | `tests/test_architecture_layers.py` |

不要复制一张表、一份映射、一段正则到第二个文件。要共享就抽模块，并加一条"禁止再抄"的扫描测试。

### 不许静默降级，也不许替调用方下结论

- **没算就不能说**：`analyze` 曾在没验证过的情况下断言"没有合法组合"，而同一组约束 `recommend` 能配出 100% 达标的方案（0.1.13 修）。诊断类输出只说自己算过的东西。
- **"没探成" ≠ "试过没有"**：探测失败记 `ok: null` + `not_probed` + `reason`，不能记成 `ok: false`（`_armor_ladder` 踩过）。
- **结论路径的 `except` 必须留痕**：重抛、写进返回值、或打日志；`tests/test_conclusion_paths.py` 扫。
- **可选数据可以降级，但要带 `warnings`**；坏的可选资料不能弄坏主结果。
- **缺值给 `None`，不编 `0`**；"没查到"和"没有"是两件事（`coverage_complete`、`unverifiable_reason`、`selectable_plug_status`）。

### 禁止兼容、不做补丁

- 改响应形状**旧键一个不留**（不双写、不留过渡分支）：0.2.0 活动统计从手写 8 键改行式，`activitiesEntered` 直接消失。
- 别名要么是**永久**（中文说法，如 `概况`/`重复武器`），要么**登记待删**（英文近义）；一律写进 `COMPATIBILITY.md`，并保证同组别名走同一段分派。
- 旧实现整段删掉，不保留"失败就退回旧路径"的分支。

### 分层与体量

- 依赖只能向下、禁止环、`svc["…_svc"]` 必须在 `ServiceContext` 里声明；新顶层模块要登记层号（`tests/test_architecture_layers.py`）。
- 模块超限先抽代码，**不抬上限**；上限只能下调（`tests/test_module_size_ratchet.py`）。
- 纯形状工厂与词表放低层；工具层只做分派、参数守卫与话术。

### 失败、写入与证据

- **失败必须是失败**：`ok=false` + `error.code` + 中文说明"缺什么/下一步"；裸抛异常算 bug（schema 层拒收是例外，见 `TESTING_CORPUS.md` 第十二章）。
- **改账号的操作**：服务端签发候选 + 用户明确 `confirmed=true` 才写；写完回读核对。
- **数据分三档不许混**：Manifest 定义 / 你的账号 / 社区资料（不可信参考，带 `source_ref` 与更新时间；出处与致谢见 README 的 Starside 段）。
- 上游故障如实说是上游，不编、不循环重试。

### 测试与验证

- 每个新守门都要**注入一次违规、确认变红、再恢复**——不验证就不知道它会不会咬人。
- 改响应形状：跑 `pytest` + 对应语料 runner + 基线 diff。
- 单测用替身、任何机器能跑（干净 `HOME` 下也要过）；真机脚本单独放，分工见 `TESTING_CORPUS.md` 第一张表。
- **声称之前先量**：性能改动给前后对比，文档里的数字必须来自实跑。

### 注释、文档与提交

- 注释写**为什么**（根因、踩过的坑、不能改的理由），不写"做了什么"：例 `a_p_i_error` 为什么长这样、`or ""` 防的是什么。
- 口径写进可执行断言，不靠人记；计划文档（`*_FORMAT_PLAN.md`）记实机证据与取舍。
- 提交信息见下面的「提交信息格式」：一行标题、一次只做一件事。
- 密钥永不进日志、提交与截图（`.env`、token、client secret）。

## 提交信息格式

一条提交 = **一行标题**，不写正文（细节在 CHANGELOG、计划文档与代码注释里）。

    <类型>：<做了什么>

- 类型用中文，现有取值：`版本号`（如 `0.2.0：…`）、`修复`、`重构`、`文档`、`语料`、`CI`、`错误码`、`词表`、`信封`、`兼容面`、`结论路径`，以及计划阶段的 `护甲 P3` / `武器 P4`。
- 标题写"做了什么"，不写「抓出的 XXX」这类腔调；不用 emoji、结尾不加句号。
- **一次提交只做一件事**：跨主题的改动拆成多条；发布提交也一样——内容照发，标题只留一行。
- 破坏性变更在标题里点明（例：`活动统计改为行式（破坏性）`），影响面写进 CHANGELOG。
