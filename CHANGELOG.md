# Changelog

按日期倒序。版本号来自 `pyproject.toml`，tag 用 `v<版本>`。

## 0.1.4 — 2026-09-13

**修 0.1.3 实测跑出来的 5 个问题**（都是护甲那轮改动暴露的）：

- **`rarity` 中文值被静默忽略**：参数说明写着"传说/异域"可用，但代码只映射英文，取不到就
  直接跳过过滤 —— 问"我有哪些异域腿甲"会把传说件一起端回来（实测 `异域`=26 件、`exotic`=8 件）。
  现在中英同结果，**不认识的稀有度直接报错并列出可用取值**（照 `item_type` 的封闭词表做法）。
- **`recommend`/`find` 组合规模超限会跑到客户端超时**：术士同参数 `analyze` 秒回
  "2.43 亿组合超上限、未计算"，而 `recommend` 会真的去枚举，客户端拿到
  `-32001 Request timed out`。现在两道门共用同一个估算与同一句收窄建议，
  超限立刻返回 `not_computed` + 四条可操作建议（**不是**"无解"）。
- `equip_mod` 的 `alternatives[].stat_bonus_hashes` 口径不一致（原始 hash，还把非六维的
  "费用"属性算进去）→ 改成与 `to.stat_bonus` 同一套六维可读键。
- `with_slot_keys` 会把展示字段塞进 `canonical_build.items[]`（目前 pydantic 容忍，但违背
  "canonical 只放可执行内容"）→ 递归时跳过 `canonical_build` 子树，并加断言。
- `intent="item"` 的 `next_actions` 还写着"换模组后续阶段提供" → 改成指向 `equip_mod` 的正确用法。

顺带修了差异闸门自己的一个小 bug：`--allowlist` 传相对路径时，报错分支会
`relative_to` 崩掉，把真正的字段消失吞成一条 traceback。

语料：护甲章节 18 → 20 行（新增稀有度中英一致/乱填报错、组合规模超限两行），
冒烟 ⭐ 25 条；`diff_weapon_baseline.py` 的路径处理加注释说明。

## 0.1.3 — 2026-09-13

**护甲：统一格式 + 换单个模组 + 无解时的六维阶梯。**

以前护甲在六个地方出没、字段各不相同，而且**换不了单个模组**（默认工具面里没有写入入口，
legacy `apply_mod` 又不确认直接改账号）。这一版按 `ARMOR_FORMAT_PLAN.md` 的 P0–P6 做完：

- **统一载荷**（`ArmorPayload`）：`identity`（`slot`/`slot_display`/`gear_tier`/`archetype`/套装）+
  `instance`（光等/位置/能量/大师/调谐）+ **三层属性** `roll`/`base`/`final` + 插槽清单。
  列表保持轻量（只加 `slot`/`slot_display`/`gear_tier`/`armor_system`，`bucket_type` 保留），
  要看插槽走新 intent。
- **新 intent `inventory_assistant(intent="item")`**：单件护甲的完整载荷。词条本体槽与
  `intrinsics` 标 `editable=false`。
- **新 intent `inventory_assistant(intent="equip_mod")`**：换一个模组。`confirmed=false` 给
  「哪件护甲、哪个槽、从什么换成什么、能量怎么变」的确认请求，确认后才写；校验实例在不在该角色身上、
  该槽收不收这个模组、能量够不够。**legacy `apply_mod` 收编**到同一条确认路。
- **无解时的 `ladder`**：`shortfall`（差多少）、`ceiling`（同一套约束下**同时**能达到的上限，
  实采）、`single_stat_ceiling`（单项上限，两者不能混）、`trials`（逐级放松试了哪些档）、
  `suggestion`（最小可行降档，**只是提议**，不自动降目标）。
- **装备确认逐件预览**：`candidates[0].items_preview` 给五件的光等/能量/现有模组/将要装的模组。
- 实机勘测修掉的两个真问题：老护甲也带 `gearTier: 0`（按字段分族会误判成 3.0）；
  异域护甲的固定属性分布在 `intrinsics` 里（不算进 `roll` 会把大师等级算成 30）。
- 文档：`routing.md` 补护甲统一键口径、`find` vs `recommend` 分工（**要装备走 `find`**）、
  `ladder` 读法；`TESTING_CORPUS.md` 新增「十六、护甲」章与逐行脚本
  `scripts/run_corpus_armor_rows.py`（12 行）；护甲基线 19 例（`--surface armor`）。

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
