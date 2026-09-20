# 旧工具面移除计划（配装导入先接住）

**状态：待确认（2026-09-20）。** 这份文档只把"删什么、怎么删、怎么验"摆清楚，确认了再动代码。

## 一、为什么要删

1. 16 个模块、67 个旧工具的官方口径本来就是"默认屏蔽、不进主路径文档、不保证契约、
   **不单独修 bug**"（`docs/COMPATIBILITY.md`）。这轮已经有两个烂在里面
   （`analyze_weapon` 读早已不存在的 `perk_pool`、`get_historical_stats` 把有数据说成"未找到"），
   都是服务形状变了之后没人知道 —— 这不是意外，是"不维护"的必然。
2. **逐个核对过：62/67 在 8 个聚合工具里都有对应。** 只有 5 个没有：

   | 旧工具 | 8 个聚合工具里有对应吗 | 处理 |
   | --- | --- | --- |
   | `import_build_from_url` / `import_build_from_image` / `parse_build_from_article` | 没有。但 **README 第 15 行把"从配装文章或截图导入配装方案"列为功能**，而它只在 `full` profile（要两个环境变量）才可达 | **先接成 `build_assistant` 的 intent**，再删 |
   | `raw_api_call`（裸打 Bungie API） | 没有 | 删（与"数据分三档、不裸打 API"的设计相冲；要调接口有 `docs/reference/bungie_api.md` 与离线文档快照） |
   | `get_item_definition`（原始定义 JSON） | 没有 | 删（调试口；真需要再加正经 intent，而不是留一个没人维护的工具） |

3. 保留成本是实打实的：`server.py` 的 profile 机制、`config.py` 两个开关、
   **55 处引用**（测试、语料、CI、安装脚本、技能文档、你自己的 DSH 配置）。
   每次改公共层都得替这 67 个工具做一次决定，而它们没有测试、没有参数守卫、没有契约。

## 二、决定（待你确认）

1. **配装导入接成 `build_assistant` 的三个 intent**，能力不丢，且不再写账号：
   - `import_url`：传 `url` → 抓正文 + 返回解析提示词（`import_from_url` 的现有行为）
   - `import_article`：传 `content`（你把文章解析出来的 JSON）→ `CanonicalBuild` + 校验 + draft
     （参数名从 `llm_response` 改成 `content`；旧的 `parse_build_from_article` 就是这一步）
   - `import_image`：传 `image_base64` → 现状是**返回空 draft + 提示词**（`ScreenshotExtractor`
     还是 Phase 1 的脚手架，没有真正的图像识别）—— 这次只搬入口、**不改行为、也不假装它能识别**；
     要真做识别另开一页计划
   - 三个 intent 都**不写账号**：产出是配方，要执行仍走 `build_assistant(intent="equip_build")`
     的"候选签发 + `confirmed=true`"流程
2. **删掉整个历史工具面**：16 个模块 → 0；`full`/`expert` profile 与
   `DESTINY_MCP_ENABLE_LEGACY_TOOLS` 开关一起删 —— 工具面只剩 8 个聚合工具这一种。
3. `raw_api_call`、`get_item_definition` 一并删（上表已说明理由；这条你可以在确认时否掉）。

## 三、具体动作（文件级）

| 文件 / 目录 | 动作 |
| --- | --- |
| `destiny_mcp/tools/build_import_tools.py` | 删；三个入口搬进 `tools/_build_import_branches.py` + `assistants.py` 分发 |
| 其余 15 个旧工具模块 | 整文件删 |
| `destiny_mcp/server.py` | 删 `_NORMAL/_EXPERT/_FULL_TOOL_MODULES`、`_tool_profile()`、`create_server(profile, legacy_tools=…)` 参数；指令文本里去掉旧工具面的说法 |
| `destiny_mcp/config.py` | 删 `TOOL_PROFILE`、`ENABLE_LEGACY_TOOLS` 及注释 |
| `destiny_mcp/tools/_requests.py` | `BuildIntent` 加 `import_url` / `import_article` / `import_image` |
| `destiny_mcp/tools/_param_contracts.py`、`_param_docs.py` | 新参数认领（`url`→只 import_url；`content`→只 import_article；`image_base64`→只 import_image），重生成 `skills/**/routing.md` |
| `destiny_mcp/services/`、`destiny_mcp/build_import/` | **不动**（服务层保留，只换入口） |
| `tests/test_legacy_tool_surface.py`、`tests/test_tool_profiles.py` | 删 |
| `tests/test_tool_simulation.py` | 改成只覆盖 8 个聚合工具（现在它靠"假服务调用全部 75 个工具"来兜底，8 个的版本继续保留） |
| `scripts/run_corpus_all_rows.py` | 删 legacy/profile 相关行；`mcp` 组改成"**恰好** 8 个工具、且不含任何旧工具名" |
| `scripts/install_skill.py`、`scripts/verify_starside.py`、`skills/destiny-mcp-setup/**` | 去掉 profile 与 `ENABLE_LEGACY_TOOLS` 的说法；`--mcp` 不再往 DSH patch 写 `DESTINY_MCP_TOOL_PROFILE` |
| `README.md` | 删 profile/旧工具面段落；"从配装文章或截图导入配装方案"改成指向 `build_assistant(intent="import_*")` |
| `docs/COMPATIBILITY.md` | "历史工具面"那一类改成"**已于 2026-09-20 整类删除**"，写清 5 个没有替代的怎么处理 |
| `docs/adr/`（编号落盘时按索引顺延） | 新增一条 ADR：为什么删（不维护的重复面 + 宣传与实际可达不一致），以及"配装导入改成 intent"的决定 |
| `~/.dsh/profiles/web/cordis.patch.yml` | 删掉 `DESTINY_MCP_TOOL_PROFILE: normal` 那一行（改前把 diff 给你看） |

## 四、验收口径

- `pytest -q`（含**干净 HOME**）、`python -m ruff check`（整仓）
- 真机语料：`sweep` / `rows` / `mcp` 三组 + 四套（全量 230 行 / 武器 17 / 护甲 25 / PvP 19）；
  `mcp` 组从"恰好 8 个聚合工具"变成"**只有** 8 个、且任何旧工具名都调不到"
- 真机握手：`scripts/verify_mcp.py`（8 个工具 + `BUNGIE_PROFILE_CHECK=ok`）
- 导入链路真机走一遍：`import_url` →（按提示词解析）→ `import_article` → `build_assistant(intent="find")`
- DSH 重连后 `tools/list` 只有 8 个工具

## 五、顺序与提交划分（一次提交一件事）

1. 本文档（等你确认）
2. `build_assistant` 的三个导入 intent + 测试
3. 删旧工具面（server/config/16 个模块）
4. 清测试与语料
5. README / 技能 / COMPATIBILITY / 新增一条 ADR
6. 真机复验（语料 + 握手 + 导入链路）

## 六、风险与不做的事

- **破坏性**：删掉 profile 机制后，`DESTINY_MCP_TOOL_PROFILE` 与 `DESTINY_MCP_ENABLE_LEGACY_TOOLS`
  变成无意义变量（设了也不报错，但不该再有人依赖）；工具面从 "8 / 27 / 75" 变成"只有 8"。
- **不动**：8 个聚合工具的行为、写入确认流程、`build_import/` 与 `services/build_import_service.py`。
- 截图导入**现在没有识别能力**（Phase 1 脚手架）：这次只搬入口，不新增能力，也不在文档里吹它。
