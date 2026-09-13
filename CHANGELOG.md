# Changelog

按日期倒序。版本号来自 `pyproject.toml`，tag 用 `v<版本>`。

## 0.1.2 — 2026-09-13

**装给正在跟你说话的那个 Agent。**

以前的 `install_skill.py` 会把技能铺给**本机探测到的每一个宿主**（Codex、Claude、Cursor…），
对一个新用户来说这是错的：他是在某个 Agent 里提问，只想让**那个** Agent 用上这套 MCP。

- 默认只装当前宿主，靠环境变量认：`DSH_HOME`/`DSH_SHELL` → dsh、`CLAUDECODE` → claude、
  `CODEX_*` → codex；认不出来才退回旧行为（已存在的都装）。`--all` 保留全装，
  `--host <name>` 显式指定，`--list` 会标出"← 当前"。
- 新增 DeepSeek Harness 宿主：技能根 `~/.dsh/skills/`（DSH 会**热发现**，装完不用重启），
  全局指令文件 `~/.dsh/AGENTS.md`。
- 新增 `--mcp`：注册 MCP 服务器。DSH 直接幂等写入 `$DSH_HOME/profiles/web/cordis.patch.yml`
  （带 `destiny2-mcp:mcp-begin/end` 标记，先备份、重复跑只更新），工具以 `mcp__destiny__*`
  出现；Claude Code / Codex 只**打印**可以直接粘的 `claude mcp add` / `codex mcp add` 命令，
  不替用户改它们的配置。
- README / AGENTS.md / 安装 Skill：把"装给提问的 Agent"写成显式规则，并补上 DSH 的注册步骤。

首次安装的耗时预期也写进了 README：`pip install -e .` 约 5 分钟（下依赖，无进度条）、
预构建 Manifest 685 MB 约 5 分钟。

## 0.1.1 — 2026-09-13

**修 P0：干净环境装出来起不来。**

- 根因：`pyproject.toml` 只写了 `mcp[cli]>=1.27.2`，从零安装解析到 **mcp 2.2.0**；
  2.x 把 `mcp.server.fastmcp` 改名成 `MCPServer`，服务在 import 阶段就炸，
  自检报 `VERIFY_FAILED=MCPError: Connection closed`。开发机装着 1.x，本地测不出来。
- 改成 `mcp[cli]>=1.27.2,<2`，并新增 `tests/test_dependency_bounds.py`
  （上界 + lock 钉 1.x + 代码确实用 v1 API，三条一起才算完整约束）。
- 验证方式：从 GitHub 克隆到干净目录 → `python -m venv .venv` → `pip install -e .`
  → 下载预构建 Manifest → OAuth 登录 → `verify_mcp.py` 全绿（8 工具、
  `BUNGIE_PROFILE_CHECK=ok`）→ 12 项真机冒烟全部符合文档。此时装到的是 mcp 1.30.0。

`v0.1.0` 的 tag 停留在修复前，请用 `v0.1.1`。

## 0.1.0 — 2026-09-13

第一次公开快照：本地运行的 Destiny 2 MCP 服务器，通过 8 个面向自然语言的聚合工具
（`player_assistant` / `inventory_assistant` / `weapon_assistant` / `build_assistant` /
`loadout_assistant` / `subclass_assistant` / `activity_assistant` / `world_assistant`，
共 108 个 intent）读取 Bungie 账号、Manifest 定义与本地社区资料。

**能做什么**

- 只读为主：角色概况、模糊找人、仓库/背包检索、武器词条与 god roll 标注、护甲词条反推、
  商人货架、单场结算、收藏品解锁状态、官方配装与本地配装读取。
- 写入类（转移、装备、改模组、存配装）一律先返回确认请求，`confirmed=false` 时**不触碰账号**；
  装备配装还要求传回服务端签发的 `canonical_build`，自拼 hash 会被拒。
- 证据分层：账号数据、Manifest 定义、社区资料三类来源在响应里分开放，缺数据就说缺数据，
  不把"没扫完"答成"你没有"。

**安装与运行**

- Python 3.12+；`pip install -e .`；自带 OAuth 登录助手与 MCP 自检脚本。
- 首次启动需要约 717 MB Manifest（可先取 `manifest-data-v1` 预构建库）。
- 只暴露 8 个聚合工具；69 个历史工具要 `DESTINY_MCP_ENABLE_LEGACY_TOOLS=1` 才出现。

**这一版为公开发布做的准备**

- 修正 `README` 推荐的启动方式：`python -m destiny_mcp.server` 会让配装求解的子进程起不来，
  改为 `python -m destiny_mcp`。
- 自检脚本不再误报：`DESTINY_OAUTH_REDIRECT_URI` 未写进 `.env` 时按运行时默认值判定；
  缺 Manifest 时自动把超时放宽到 1800 秒并打印原因；venv 路径与 token 权限检查按平台分支
  （Windows 不再必失败）。
- 登录助手缺 `openssl` 时给人话提示并指向 `--manual`，不再裸抛 traceback。
- `equip_build` 传错 `canonical_build` 时改成中文说明（缺哪些字段、该先跑哪个 intent），
  不再直接甩 pydantic 的英文堆栈。
- 护甲模组筛选补 `match` 口径：词表外的词只在名字/描述里蒙中时标 `kind="keyword"` 并给 warning，
  词表内 0 条时说明"本地数据里没有"，不再让 `速度` 这类词安静返回一堆无关模组。
- 补 `LICENSE`（MIT）与 `pyproject` 的 license 元数据；README 增加「前置条件与已知限制」，
  写明平台支持、审计日志落盘位置（`~/.destiny_mcp/audit/`，明文、不上传）、
  跑测试需要 `pip install -e ".[dev]"`。
- 删除长期失效的 `Dockerfile`（引用了不存在的 `src/`）；补 `tests/conftest.py`，
  干净克隆（没有 `.env`）也能跑全量测试。

**已知限制**：见 README「前置条件与已知限制」与 `TESTING_CORPUS.md` 的「已知问题」。
