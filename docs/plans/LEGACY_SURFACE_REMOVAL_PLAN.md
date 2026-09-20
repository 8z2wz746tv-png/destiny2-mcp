# 历史工具面剥离计划（已落地：存档到 `legacy/`）

**状态：已完成（2026-09-20）。** 决定记录在 ADR-008，兼容面口径在 `docs/COMPATIBILITY.md`。

## 一、一开始想做什么，后来改成了什么

| 阶段 | 方案 | 结果 |
| --- | --- | --- |
| 初版计划 | 先把"配装导入"搬成 `build_assistant` 的新 intent，再把 67 个旧工具**删掉** | 未执行 |
| 中途调整 | 先删掉两个已经坏掉的（`analyze_weapon`、`get_historical_stats`） | 执行后又还原 |
| **最终方案** | **不迁移、不删除**：16 个模块 + 配装导入整块**整体剥离到仓库根目录 `legacy/`** 存档；配装导入这个功能**本身不要了** | **已落地** |

改成存档的理由：删掉就查不到旧行为了，而存档的成本只是几个目录；配装导入的截图那条本来还是
Phase 1 脚手架（返回空 draft + 提示词，没有真正的识别能力），为一个没人要的功能维护迁移不划算。

## 二、为什么剥离（决定依据）

1. 那 67 个工具的既定口径是"默认屏蔽、不进主路径文档、不保证契约、**不单独修 bug**"，
   而这个口径下必然腐烂 —— 复核时 `analyze_weapon` 还在读 P4/P6 之前的 `result["perk_pool"]`
   （一直裸抛 `KeyError`），`get_historical_stats` 还在读 0.2.0 之前的 `{pve, pvp}`
   （**有数据也说"未找到统计数据"**）。
2. 逐个核对替代关系：**62/67 在 8 个聚合工具里都有对应**；没有对应的 5 个是 `raw_api_call`、
   `get_item_definition`、配装导入三件套。前两者与"数据分三档、不裸打 API"的设计相冲；
   配装导入只在 `full` profile 可达，却被 README 列为功能 —— 宣传与实际可达不一致。
3. 维护成本：profile 机制 + 两个环境变量 + 55 处引用（测试、语料、CI、安装脚本、技能、DSH 配置），
   每次改公共层都要替这 67 个没有参数守卫的工具做决定。

## 三、实际做了什么

| 位置 | 动作 |
| --- | --- |
| `legacy/tools/*_tools.py`（16 个模块） | 从 `destiny_mcp/tools/` 移入（`git mv`，保留历史） |
| `legacy/build_import/`、`legacy/build_import_service.py`、`legacy/tools/build_import_tools.py` | 配装导入整块移入 |
| `legacy/skills/build_article_skill.md`、`build_screenshot_skill.md` | 导入功能专属提示词移入（其余 5 个 `skills/*.md` 是 MCP prompt 在用的，留在原地） |
| `legacy/tests/test_build_import_skill_loading.py` | 导入功能的专属测试移入（`testpaths=["tests"]`，不会被收集） |
| `legacy/README.md` | 新增：里面有什么、为什么不维护、怎么复活、别怎么用 |
| `destiny_mcp/server.py` | 删 `_NORMAL/_EXPERT/_FULL_TOOL_MODULES`、`_tool_profile()`、`create_server` 的两个参数、`/health` 的 `tool_profile`、配装导入服务的装配 |
| `destiny_mcp/config.py`、`service_context.py` | 删 `TOOL_PROFILE`、`LEGACY_TOOLS_ENABLED`、`build_import_svc` |
| `tests/` | 删 `test_legacy_tool_surface.py`、`test_tool_profiles.py`；`test_tool_simulation.py` 改成只覆盖 8 个聚合工具（含"工具面恰好是这 8 个"）；`test_architecture_boundaries.py` 去掉已剥离工具的守卫；`test_exact_build_execution.py` 去掉用旧 `build_tools` 的对比测试、`CanonicalBuild` 改从 `build_contracts` 取 |
| `scripts/` | `run_corpus_all_rows.py`（legacy 行改成反向断言）、`install_skill.py`、`verify_starside.py`、`skills/destiny-mcp-setup/scripts/verify_mcp.py` 去掉 profile/开关 |
| `pyproject.toml` | `[tool.ruff] extend-exclude = ["legacy"]`；删掉 `destiny_mcp.build_import` 的 package-data 声明 |
| `README.md`、`docs/COMPATIBILITY.md`、`docs/testing/TESTING_CORPUS*.md`、`skills/destiny-mcp-setup/SKILL.md`、`.env.example` | 去掉 profile/历史工具面的说法；README 功能列表删掉"从配装文章或截图导入配装方案" |
| `docs/adr/008-legacy-tool-face-archived.md` | 新增：决定了什么、被否掉的三个选项与代价 |
| `~/.dsh/profiles/web/cordis.patch.yml` | 删掉 `DESTINY_MCP_TOOL_PROFILE: normal` 那一行（它已无意义） |

## 四、验收口径（都已执行）

- `pytest -q`：1586 通过（含干净 HOME）；`ruff check`（整仓）干净；`legacy/` 被 ruff 排除（注入一条
  违规确认它不报）。
- 语料 `mcp` 组：正常工具面 8 个；**旧 profile + 旧开关塞进去仍是 8 个、且没有 `get_inventory`**。
- 真机语料：全量 230 行 + 武器 17 + 护甲 25 + PvP 19。
- `skills/destiny-mcp-setup/scripts/verify_mcp.py`：真机握手、`BUNGIE_PROFILE_CHECK=ok`、8 个工具。
- DSH 重连后 `tools/list` 只有 8 个工具。

## 五、不做的事

- 不给 `legacy/` 加测试、不让它进 lint、不在 `server.py` 里挂回去（存档的价值是"能查"，不是"还能跑"）。
- 不保留任何"旧工具名的兼容分支"：工具名不存在就是不存在。
- 不为了留住配装导入而先做截图识别（那是一条独立的产品线，要做另开计划）。
