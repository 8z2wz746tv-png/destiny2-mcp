# ADR-008: 历史工具面整块剥离到 `legacy/`，工具面固定 8 个聚合工具

- Status: accepted
- Date: 2026-09-20
- Decision By: maintainer
- Scope: `destiny_mcp/server.py` 的工具注册、`destiny_mcp/config.py` 的环境变量、
  `docs/COMPATIBILITY.md` 的第三类入口、`legacy/`（新增）、README 与技能文档里的工具面说明

## Context

仓库里长期存在两套工具面：8 个聚合 assistant（`player_assistant` … `world_assistant`，按 `intent` 分派、
有参数守卫与统一信封）和 16 个模块里的 67 个低层工具（`get_inventory`、`search_weapons_by_type` …）。

低层那套的既定口径写在 `docs/COMPATIBILITY.md`：**只在 `full`/`expert` profile 且
`DESTINY_MCP_ENABLE_LEGACY_TOOLS=1` 时暴露；不进主路径文档、不保证契约、不单独修 bug。**
代价在 2026-09-20 复核时露了出来：`analyze_weapon` 还在读 P4/P6 之前的 `result["perk_pool"]`
（一直裸抛 `KeyError`，没有任何测试发现），`get_historical_stats` 还在读 0.2.0 之前的 `{pve, pvp}`
手写八键（**有数据也说"未找到统计数据"**）。这不是意外，是"不维护"的必然。

同时逐个核对过替代关系：67 个工具里 **62 个在 8 个聚合工具里都有对应**，只有 5 个没有 ——
`raw_api_call`（裸打 Bungie API）、`get_item_definition`（原始定义 JSON）、
以及配装导入三件套（`import_build_from_url/image`、`parse_build_from_article`）。
其中配装导入被 README 第 15 行列为功能，却只有在 `full` profile（两个环境变量）下才可达 ——
**对外宣传与实际可达不一致**，这本身就是个 bug。

维护成本是实打实的：profile 机制、两个环境变量、**55 处引用**（测试、语料、CI、安装脚本、
技能文档、用户自己的 DSH 配置），每次改公共层都要替这 67 个没有守卫的工具做一次决定。

**What changed**：这一轮先按"删掉两个已经坏掉的"处理（`analyze_weapon`、`get_historical_stats`），
随后决定不再逐个修、也不整体删除，而是**剥离存档**；并且**配装导入这个功能本身不要了**
（不做"搬进 `build_assistant` 成新 intent"的迁移）。

## Decision

1. 工具面**只有 8 个聚合工具**：`create_server()` 不再收 `tool_profile` / `legacy_tools`，
   `config.TOOL_PROFILE` 与 `config.LEGACY_TOOLS_ENABLED` 删除，`/health` 不再报 `tool_profile`。
   旧的 `DESTINY_MCP_TOOL_PROFILE` / `DESTINY_MCP_ENABLE_LEGACY_TOOLS` 变成无意义变量
   （设了也不报错，但不该再有人依赖）。
2. 16 个工具模块 + 配装导入整块（`build_import/` 领域包、`build_import_service.py`、
   `build_import_tools.py`、那两个提示词、它的专属测试）**移到仓库根目录 `legacy/`**，
   连同 `legacy/README.md`（说明状态、为什么不维护、怎么复活）。
   不是删除：代码留着可查，git 历史也完整。
3. `legacy/` **不进包**（`packages.find` 只收 `destiny_mcp*`）、**不进测试**
   （`testpaths = ["tests"]`）、**不进 lint**（`[tool.ruff] extend-exclude`）、
   也不上代码地图（`AGENTS.md` 里单列一句说明）。
4. 配装导入**不再提供**：README 的功能列表、技能文档里的相关宣传一并删掉。
   要重新捡起来，从 `legacy/` 或 git 历史取回，并按"新入口"的规矩补参数守卫、信封、语料与测试。
5. 被否掉的选项：
   - **就地修那两个坏工具**：违背"不单独修 bug"的既定口径，也治不了"没人发现"的病根。
   - **逐个删除**：删掉就查不到旧行为了；存档的成本只是几个目录。
   - **把配装导入搬成 `build_assistant` 的新 intent**：能力不丢，但要么维护一个没人要的功能，
     要么先把截图识别那条 Phase 1 脚手架真做出来 —— 明确不要，所以不搬。

## Consequences

- **破坏性**：工具面从 "8 / 27 / 75" 变成"只有 8"；任何依赖旧工具名的提示词、脚本或宿主配置都要改。
  语料 `mcp` 组加了一条**反向断言**：把旧的 profile/开关塞进环境变量也只该有 8 个工具。
- 少了一套"看起来还能用"的重复面：改公共层时不再需要替 67 个无守卫的工具做决定。
- 代价：`legacy/` 里的相对 import 是按原目录写的，搬回来才能跑（`legacy/README.md` 写明了怎么复活）；
  配装导入的能力暂时归零 —— 那是这次明确接受的损失。
- 以后要加工具就走 8 个聚合工具的 `intent`（并同时更新 intent 取值、参数归属、信封三张契约表与语料）。
