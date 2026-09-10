# Codex 测试指南

适用于当前工作区的本地单用户版本，默认 `normal` 模式。

**完整语料见 [`TESTING_CORPUS.md`](TESTING_CORPUS.md)**：按 8 个工具逐节列出可以直接发送的话、期望路由与验收点，含确认、证据边界、缺数据、社区资料等横切用例。机器可读子集在 [`tests/agent_behavior_cases.yaml`](tests/agent_behavior_cases.yaml)。本文下面的章节是其中较早、较窄的一部分。

## 接入前基线验证

2026-09-09 已在本机完成：

- 使用现有 Python 3.13 虚拟环境，执行 `pip install -e .` 更新安装。
- `pip check` 通过；`pytest -q` 返回 `228 passed`。
- 保留现有 Codex `destiny` 注册，命令指向本项目的 `.venv/bin/destiny-mcp`。
- 保留本地凭据和已有 OAuth 登录；真实账号档案读取通过。
- 验证脚本返回 `BUNGIE_PROFILE_CHECK=ok`、`MCP_TOOL_COUNT=8`、`VERIFY_OK`。

上述结果不代表所有功能已用真实账号逐一验证。游戏写入、整套装备及真实失败回滚未在本轮执行；相关回归测试使用测试数据。

Starside 本地资料层现已接入，测试方法见第 6 节。它支持资料检索、配装解析和库存核对，
但不能直接把网站配装转成完整的一键执行计划；武器、Perk、技能、模组和神器不能据此宣称已全部应用。

## 1. 在新任务中检查连接

先重启 Codex，或新开一个任务。旧任务可能继续使用旧服务进程或旧工具列表。
测试期间不要在多个任务、DIM 和游戏客户端中同时修改同一账号的装备。

先发送：

> 接下来测试 Destiny MCP。先只做查询，不移动、装备、锁定、保存或删除任何东西。请实际调用工具，不用记忆补数据；失败时告诉我工具名和错误原因，不展示任何密钥或令牌。先看看我的账号角色信息。

预期：调用 `player_assistant(intent="profile")`，返回当前授权账号的角色。与游戏里的职业、角色数量核对；无需再次提供 API Key 或 client secret。

默认应发现以下八个工具，不要把宿主自身的工具算进去：

```text
player_assistant
inventory_assistant
weapon_assistant
build_assistant
loadout_assistant
subclass_assistant
activity_assistant
world_assistant
```

## 2. 只读功能测试

逐条发送，不要一次要求跑完整个清单。把 `<职业>` 换成账号实际存在的猎人、术士或泰坦，物品名使用前一步查询返回的名称。

| 测试 | 可以直接发送的话 | 验收点 |
| --- | --- | --- |
| 库存概况 | 看看我的背包和仓库概况，只读。 | 使用 `inventory_assistant`，区分角色背包和仓库。 |
| 重复武器 | 找几组我持有的重复武器，列出实例 ID 和所在位置，不要处理它们。 | 同名不同版本不能直接当成完全相同物品；不自动锁定或移动。 |
| 副本对比 | 对比刚才那组武器的实际 Perk，建议 PVE 留哪把，只给建议。 | 使用 `weapon_assistant(intent="compare")`；每把建议对应具体实例。 |
| 全目录查询 | 从全游戏武器目录找几把手炮，不限我是否拥有。 | 使用 `catalog`；未检查库存的结果不能称为“我拥有”或“我没有”。 |
| 社区选取率 | 查一下刚才这把武器的 Perk 选取率，注明数据来源和版本。 | 使用 `popularity`；无快照时明确缺数据，不能编造实时百分比。 |
| 子职业 | 看看我的<职业>当前技能、星相和碎片，不修改。 | 使用 `subclass_assistant(intent="get")`，与游戏当前配置核对。 |
| 已存配装 | 列出我的<职业>已有配装，不保存、不覆盖任何配装槽。 | 使用 `loadout_assistant(intent="list")`；本地与官方配装来源分开，每项包含统一的 `build_template`。 |
| 社区配装 | 找 5 套<职业>社区方案，不读取我的账号。 | 使用 `build_assistant(intent="community", character="hunter", top_n=5, include_inventory=false)`；不要使用 `loadout_assistant`。只有可选网页归档提供完整模板；若 `build_count=0`，应说明随附资料不含完整配装，不能现场编造。 |
| 活动记录 | 看看我的<职业>最近几场活动记录。 | 使用 `activity_assistant(intent="history")`；时间和活动可以核对，无记录不应补写。 |
| 商人 | 看看班西现在卖哪些武器，以及这些商品本次实际的 Perk。 | 使用 `world_assistant(intent="vendor")`；不能拿武器总 Perk 池代替售卖 Roll。 |
| 周常 | 查一下本周活动，标明数据来源和不确定的部分。 | 使用 `world_assistant(intent="weekly")`；查询失败不能凭记忆给确定答案。 |

## 3. 护甲配装与无解诊断

先发送：

> 用我的<职业>现有护甲找三套方案，生命至少 100、手雷至少 100。保持这两个最低目标，不自动降低；只列候选，不装备。说明碎片属性是否计入，并列出五件护甲、模组和最终六维。

预期：使用 `build_assistant(intent="find")`；候选包含五个护甲部位、实际实例 ID、最终属性和可回传的 `canonical_build`。没有解也可以是正确结果，但必须说明无解，不能擅自放宽条件。

再测试金装确认：

> 保留刚才的职业和属性要求，指定<该职业的一件异域护甲原名>，先让我确认匹配到的金装，再继续找方案，仍然不装备。

预期：先展示金装候选。你选定后，继续保留原来的职业、目标、优先级及碎片设置，不能在重试时丢失条件。

如果没有满足条件的方案，再发送：

> 不降低原目标，分析还差什么；用当前穿着护甲为基线，最多替换两件，反推合法的待刷护甲目标。不要把待刷目标说成我已经拥有的装备。

预期：使用 `farm_target`，先查单件、单件无解再考虑两件；仍无解时明确返回。待刷数值必须来自工具，不能靠六维缺口相减自行拼出护甲。

## 4. 写入确认测试

这一节由你选择是否执行。实际装备前让角色停在轨道等允许换装的状态，保留原装备和技能配置的截图，并停止其他工具的装备操作。

### 先验证没有确认就不写入

选定第三节返回的一套候选，发送：

> 我想试候选 1。先列出将改变的护甲、模组和技能，只请求我的确认，不要执行。

预期：助手停在确认阶段。如果调用 `equip_build`，应使用 `confirmed=false`，并原样携带候选；游戏装备不变。不应为了“预览”先换装。

### 再由你明确批准

核对清单后，另发一条：

> 确认把刚才展示的候选 1 装备到我的<职业>。不要更换候选、重新按评分挑装备，也不要修改官方配装槽。完成后重新读取实际状态，逐项核对。

预期：使用服务端签发的原候选执行，不由助手手写 Hash 或实例 ID。核对实际五件护甲、模组、相关技能和六维；若失败，应清楚报告哪些步骤完成、哪些失败以及恢复结果。

当前精确配装执行以五件护甲为核心，可附带受支持的子职业配置，不等于任意网站配装的全套武器和神器导入。Bungie 多步写入不是数据库事务，不能保证任何网络故障下都能完整回滚。

### 可选：过期候选防护

重新生成一套候选但不装备，然后由你在游戏中更改一件护甲的模组，再要求执行刚才那套候选。

预期：旧库存快照不再匹配时拒绝执行，提示重新求解、重新确认；不能自动换成另一套候选。不要通过手改候选 JSON 或编造实例 ID 测试真实账号。

## 5. 登录与故障排查

本次复用已有登录，未重测浏览器授权流程。若专门测试重新登录，先关闭使用 Destiny MCP 的任务，再在本地终端运行：

```bash
cd "/Users/husky/项目/destiny2-mcp"
.venv/bin/destiny-mcp-oauth --no-open --timeout 900
```

使用该进程刚生成的 Bungie 登录链接，完成登录和授权。回调必须是 `https://localhost:8765/callback`，不能改成数字 IP。仅在确认地址为本机 localhost 且登录助手仍运行时，处理本地自签名证书提示。成功应包含“Bungie 登录完成”和令牌保存结果；不要把回调 URL 或凭据粘到聊天里。

重新登录会替换本地令牌，不要先删 `.env` 或令牌文件。曾在聊天中公开过的凭据，建议在 Bungie 开发者后台轮换后更新本地 `.env`。

安装复查命令：

```bash
cd "/Users/husky/项目/destiny2-mcp"
.venv/bin/python -m pip check
.venv/bin/python skills/destiny-mcp-setup/scripts/verify_mcp.py
.venv/bin/python -m pytest -q
```

- 没有工具：先新开任务或重启 Codex，再跑验证脚本。
- 验证脚本必须同时得到 `BUNGIE_PROFILE_CHECK=ok`、`MCP_TOOL_COUNT=8` 和 `VERIFY_OK`，仅注册成功不算通过。
- 回调连接被拒绝：检查登录助手是否仍运行，不要不断刷新旧的带授权码 URL。
- 账号读取失败：检查网络、Bungie 服务状态和本地登录；不要先反复卸载重装。
- 配装求解超时：如实记录；不要通过擅自降目标掩盖失败。
- 写入失败：停止后续操作，重新读取实际状态。不要假设没有变更，也不要自动循环重试。

反馈问题时记录：测试话术、目标职业、工具名与 intent、脱敏错误码、预期结果、实际结果，以及游戏状态有无变化。不要附 `.env`、`tokens.json` 或未经检查的完整日志。

## 6. Starside 接入测试

2026-09-10 作者 Markdown 数据包适配验证：

- 全量回归 `283 passed`，包含 Markdown 语义、复杂表格、旧归档兼容、配装迁移、官方 20 槽位和跨 Agent Skill 契约回归。
- 真实 MCP 握手返回 `BUNGIE_PROFILE_CHECK=ok`、`MCP_TOOL_COUNT=8`、`VERIFY_OK`。
- `scripts/verify_starside.py` 通过；从 MCP 实际识别 22 份作者文档，并验证武器、子职业、护甲和活动路由。当前机器同时安装了可选归档，因此也分页遍历了全部 108 套模板。
- 构建 wheel 后在全新临时虚拟环境安装，能自动定位安装前缀中的 22 份文档并查询“辉耀炽热”。
- 实际 MCP 查询断言覆盖“辉耀炽热”“傍晚 SI4”“圣贤保护者”“冰霜护甲”和“被腐化的卡丽”；没有执行游戏写入。
- `scripts/verify_mcp.py` 是平台无关入口；它检查真实 MCP 握手、工具 schema、只读账号调用和默认 8 个工具，不依赖 Codex。
- 使用系统构建后端完成 wheel 检查，接入模块已包含，归档和凭据未包含；项目虚拟环境未因此增加构建依赖。
- 一套模板的库存读通不代表全部模板适合当前版本，也不代表所有模组、技能和神器已验证。
- 官方槽位改为输出统一的 `build_template`；`slot_number` 和 `native_character_id` 仍用于 Bungie 原生槽位执行，模板本身不是 `canonical_build`。

先重启 Codex 或新开任务，然后逐条发送：

| 测试话术 | 验收点 |
| --- | --- |
| 用本地资料解释辉耀炽热的效果，区分强化效果并附来源和更新日期。 | 使用 `weapon_assistant` 的社区查询或 Perk 描述附带的社区资料；引用标记不丢失。 |
| 用本地资料查傍晚 SI4，列出三号位、四号位推荐 Perk 和获取地点。 | 使用 `weapon_assistant(intent="community")`；两列 Perk 不串列，结果来源为 `author_markdown`。 |
| 查圣贤保护者的 2 件和 4 件效果。 | 使用 `world_assistant(intent="community", community_category="armor")`；能下钻正文并保留作者文档更新时间。 |
| 查被腐化的卡丽生命值，并说明来源边界。 | 使用 `activity_assistant(intent="community")`；表格返回 `299440` 和上游链接，但仍标记为社区实测而非 Bungie 实时数据。 |
| 找 5 套猎人社区配装，只看模板，不读取我的账号。 | `build_assistant(intent="community", character="hunter", top_n=5, include_inventory=false)`；显示总命中数和下一页位置，不把 5 当全量。 |
| 列出我已有的猎人配装 | `loadout_assistant(intent="list", character="hunter")`；只显示账号已存配装，不能把它当社区推荐。每项检查 `source`、`slot_number`（官方）和 `build_template`。 |
| 再看下一页，保留相同筛选条件。 | 原样使用 `next_offset`，ID 不重复、不漏页。 |
| 读取刚才第 2 套的完整模板，检查我的库存，不装备。 | 用返回的 `community_build_id`；返回 `selected_build`，区分已持有、缺少、未解析和未验证。 |
| 看这套模板里的重复模组、套装、六维范围和注解，列出目前不能自动验证的要求。 | 同模组出现两次不能去重；同套 2 件 + 4 件取 4 件，不同套分开；`~` 不变成 0，范围不变成单一下限。 |
| 查副本攻略，注明哪些只是外部文档链接。 | `activity_assistant(intent="community")`；外链详情 `body_archived=false`，不能编造外部文章内容。 |
| 查本地输出表，保留表头、条件、PvP 和待验证数值。 | `world_assistant(intent="community", query="DPS")` 搜索，再以 `knowledge_id` 和 `community_section="tables"` 读取。零散摘要不能作为完整排名。 |
| 这套可以直接一键装备吗？只解释，不操作。 | 明确返回不能直接执行完整社区模板；不能把 `solver_handoff` 当完整候选，也不能偷换成只装备护甲。 |

搜索详情调用形状如下，ID 必须来自实际搜索响应，不要凭空编造：

```json
{
  "intent": "community",
  "knowledge_id": "<搜索返回的 knowledge_id>",
  "community_section": "text",
  "offset": 0
}
```

正文单次最多 6000 字符；表格和外链单次最多 20 行/条。存在 `next_offset` 表示还没读完。
表格/外链是所属整页范围，不一定仅对应搜索命中的一项，响应的 `detail_scope` 会说明。
`source.inline_semantics_preserved=false` 的普通索引摘要不保留全部内联语义，
比较数值应优先读 `description` 条目或带标记的表格详情。

自动复测命令：

```bash
.venv/bin/python -m pytest -q tests/test_starside_markdown.py
.venv/bin/python -m pytest -q tests/test_starside_integration.py
.venv/bin/python -m pytest -q
.venv/bin/python scripts/verify_starside.py
.venv/bin/python scripts/verify_starside.py --inventory
```

新克隆默认只有随附 Markdown，因此 `STARSIDE_AUTHOR_DOCUMENT_COUNT=22` 和
`STARSIDE_BUILD_ARCHIVE=not_installed` 同时出现是正常结果。安装可选 schema v2 网页归档后，
验证脚本才要求配装分页和详情检查通过。

离线回归使用合成数据，覆盖归档缺失/损坏、分页、多个配装块、无损数值语义、严格名称匹配、
未知 Perk、同部位套装计数和防止模板直接执行，不需要复制真实归档或暴露账号数据。

Codex MCP 配置参考：[官方 MCP 文档](https://developers.openai.com/codex/mcp)。本项目的 Bungie 登录使用自己的 `destiny-mcp-oauth`，不是远程 MCP 的 `codex mcp login`。
