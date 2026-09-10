# Destiny MCP

Destiny 2 装备管理 MCP Server — 通过 AI Agent 管理武器和装备。

类似 DIM（Destiny Item Manager），但通过 MCP 协议与 AI 交互，支持自然语言操作。

## 功能

- 🔍 查询玩家信息、角色状态、背包物品
- 📦 转移、装备武器和护甲
- 🎯 管理子职业配置（超能、手雷、碎片等）
- 🔫 查询武器 perk 池、对比副本、god roll 推荐
- 🛡️ 自然语言护甲配装与反推：库存无解时先查单件，单件不足再给合法两件待刷方案
- 🛒 查询商人库存和每周重置活动
- 📥 从配装文章/截图导入配装方案
- 💾 保存/装备配装方案（含模组和碎片配置）
- 🧭 默认暴露 8 个聚合 assistant 工具，避免 Agent 被几十个低层工具干扰
- 本地 Starside 资料接入：配装模板、Perk、护甲、技能、机制和副本资料，保留出处并匹配账号库存

## 快速开始

这是个人本地版，推荐让本地 Agent 按下面流程安装。用户只需要完成一次 Bungie 登录，之后 token 会保存在本机。

本项目提供两层 Agent 指引：

- [`destiny2-mcp` 通用 Skill](skills/destiny2-mcp/SKILL.md)：工具路由、证据范围、Starside 配装和确认边界；任何能读取 Markdown 的 Agent 都可以使用。
- [`destiny-mcp-setup` 安装 Skill](skills/destiny-mcp-setup/SKILL.md)：本地安装、OAuth、MCP 注册和故障排查；其中部分步骤是 Codex 专用的。

Skill 文件放在 GitHub 仓库中，不代表已经自动安装到某个平台的 Skill 目录。是否自动发现取决于 Agent 平台；如果平台不会自动发现，请按下面的提示让 Agent 直接读取这些 Markdown 文件。

### 使用任意 Agent 安装

支持 `AGENTS.md` 的 Agent 通常会读取仓库级指引；其他 Agent 请把下面的提示直接发送给它：

```text
请先阅读这个项目的 README.md、AGENTS.md（如果你的平台支持）和
skills/destiny2-mcp/SKILL.md。
然后根据你当前 Agent 的 MCP 配置方式完成 Destiny MCP 安装和 Bungie OAuth 登录。
安装完成后运行 scripts/verify_mcp.py，确认 MCP_HANDSHAKE=ok、MCP_TOOL_SCHEMA=ok、
只读账号检查成功，以及默认 8 个工具都可用。
如果需要安装、OAuth、注册或排查问题，再阅读 skills/destiny-mcp-setup/SKILL.md。
不要让我在聊天中粘贴任何密钥、授权码或 token。
```

通用流程是：读取项目指引 → 创建环境并配置 `.env` → 完成 OAuth → 按当前平台注册 stdio MCP → 运行通用验证脚本 → 再运行 Agent 行为测试。MCP 注册成功不等于工具可用；必须完成真实握手和只读调用。

不同 Agent 的配置文件位置和 Skill 自动发现方式不同。本项目不假设某个平台一定支持 `AGENTS.md` 或自动安装 Skill；Agent 应使用自己的 MCP 配置入口，并把项目绝对路径写入命令。不要把 Bungie 凭据放进 MCP 配置或聊天消息。

如果浏览器没有成功回跳本机，不要直接打开 callback 地址；应打开 OAuth 命令生成的完整授权链接，并保持 OAuth 辅助进程运行。

### 1. 准备 Bungie 应用

在 [Bungie Developer Portal](https://www.bungie.net/en/Application) 创建或打开应用：

- 填写 API Key、OAuth client_id、OAuth client_secret 到 `.env`
- Redirect URL 添加：`https://localhost:8765/callback`
- 常用权限建议勾选：`ReadDestinyInventoryAndVault`、`MoveEquipDestinyItems`、`ReadDestinyVendorsAndAdvisors`
- 如果要让工具修改模组/更深层配置，再按 Bungie 页面提示开启 `AdvancedWriteActions`

### 2. 本地安装

```bash
cd /path/to/destiny2-mcp
python3 -m venv .venv
.venv/bin/python -m pip install -e .
cp .env.example .env
```

然后编辑 `.env`，填入 Bungie 应用凭据。

默认工具面是 `normal`，只暴露面向自然语言的聚合入口：

```bash
DESTINY_MCP_TOOL_PROFILE=normal
```

可选值：

| Profile | 用途 |
|---|---|
| `normal` | 推荐默认值，只暴露 8 个 assistant 聚合工具 |
| `expert` | 聚合工具 + 常用查询类旧工具，用于排查查询问题 |
| `full` | 聚合工具 + 全部历史工具，用于兼容旧提示词或开发调试 |

### 3. 预热 DIM Wish List（可选）

MCP 启动时如果发现 DIM wish list 数据不存在，会自动下载。Agent 也可以提前运行：

```bash
.venv/bin/destiny-mcp-fetch-wishlists
```

这份数据用于武器 god roll / PvE / PvP 推荐标注。下载失败不会影响背包、转移、配装等基础功能，只会少一部分社区推荐评分。

### 4. 登录 Bungie

让 Agent 运行：

```bash
.venv/bin/destiny-mcp-oauth --no-open
```

命令会打印一个 Bungie 登录链接。用户打开链接并完成授权后，脚本会自动保存 token：

```text
~/.destiny_mcp/tokens.json
```

如果浏览器没有成功回跳本机，可以用手动兜底：

```bash
.venv/bin/destiny-mcp-oauth --manual
```

也兼容源码运行方式：

```bash
.venv/bin/python scripts/oauth_setup.py --no-open
```

### 5. 接入本地 Agent

MCP stdio 配置示例：

```json
{
  "mcpServers": {
    "destiny": {
      "command": "/path/to/destiny2-mcp/.venv/bin/destiny-mcp",
      "cwd": "/path/to/destiny2-mcp",
      "env": {
        "DESTINY_MCP_ROOT": "/path/to/destiny2-mcp"
      }
    }
  }
}
```

### 6. 直接运行检查

```bash
.venv/bin/destiny-mcp

# 或直接运行
.venv/bin/python -m destiny_mcp.server
```

如果提示没有 OAuth token，先重新执行第 4 步登录。

个人版不需要 Docker。

### 7. 通用 MCP 验证

这一步不依赖 Codex、Claude、Cursor 或其他特定 Agent：

```bash
.venv/bin/python scripts/verify_mcp.py
```

成功结果至少应包含：

```text
MCP_HANDSHAKE=ok
MCP_TOOL_SCHEMA=ok
BUNGIE_PROFILE_CHECK=ok
MCP_TOOL_COUNT=8
VERIFY_OK=Destiny MCP is ready
```

脚本不会打印 API Key、OAuth Secret、Access Token、Refresh Token 或玩家档案内容。若只需要离线检查 Skill 和测试用例，不需要 OAuth，可运行：

```bash
.venv/bin/python -m pytest -q tests/test_skill_contracts.py
```

### 8. 跨 Agent 行为测试

连接成功后，使用 [`tests/agent_behavior_cases.yaml`](tests/agent_behavior_cases.yaml) 中的自然语言用例，在目标 Agent 内逐条测试。重点确认 Agent 能区分：

- `filter_rolls`：账号当前武器副本；
- `catalog`：全游戏 Manifest 候选；
- `build_assistant(intent="community")`：Starside 社区模板；
- `loadout_assistant(intent="list")`：玩家已存配装和官方槽位。

行为测试还要确认写操作先展示目标并等待确认，社区模板不会直接传给 `equip_build`，以及不完整扫描不会被回答成“账号没有”。记录实际工具、intent 和关键参数，不要记录密钥、Token 或未经脱敏的账号日志。

## 技术栈

- Python 3.12+
- [MCP](https://modelcontextprotocol.io/) (FastMCP)
- [aiobungie](https://github.com/nxtlo/aiobungie) (Bungie API 客户端)
- Pydantic v2

## 架构

本项目采用单进程、单用户的模块化单体架构。所有 Bungie 写操作在进程内共享账号锁，
写入完成、失败或取消后，依赖账号状态的缓存都会失效。不要同时启动多个实例操作同一
Bungie 账号；跨进程互斥不在本项目的设计范围内。

`normal` profile 对外保持 8 个聚合工具，内部按意图校验参数并委托领域服务；配装组合
计算在独立工作进程中运行，每个服务实例最多同时计算一个任务，默认 60 秒超时
（包含排队时间）；超时或取消会终止该计算进程，不阻塞 MCP 请求循环。

服务器通过 `create_server()` 显式注册工具。认证或 Manifest 初始化失败时停止启动，
`/health` 仅在初始化完成后的生命周期内返回就绪。配装导入输出 `BuildRecipe`，
可执行候选使用 `ExecutableBuild`，并继续校验一次性候选凭据和库存快照。

```
destiny_mcp/
├── server.py          # MCP Server 入口，生命周期管理
├── config.py          # 环境变量配置
├── service_context.py # 有类型的服务上下文
├── build_contracts.py # 配装需求、兼容格式与可执行计划
├── bungie_client.py   # Bungie API 客户端
├── manifest.py        # 游戏数据清单管理
├── models/            # Pydantic 数据模型
├── tools/             # MCP 工具定义（默认 8 个 assistant，full 模式保留旧工具）
├── services/          # 业务逻辑层（25+ 个 service）
├── build/             # Armor 3.0 配装求解与合法刷取目标反推
├── utils/             # 工具函数
└── build_import/      # 配装导入模块
```

## 默认工具分组

`normal` profile 下只暴露：

| 工具 | 覆盖范围 |
|---|---|
| `player_assistant` | 玩家搜索、档案、角色概况 |
| `inventory_assistant` | 背包/仓库查询、精确重复武器扫描、移动、装备、批量装备、邮政官、锁定、任务追踪 |
| `weapon_assistant` | 武器分析、指定实例对比、perk 池、全武器目录筛选、perk 选取率、god roll |
| `build_assistant` | 护甲配装推荐、候选、诊断、合法 Armor 3.0 单件/两件刷取目标反推、确认后精确装备 |
| `loadout_assistant` | 玩家已存配装、Bungie 官方配装槽读取/写入/清空；社区模板走 `build_assistant` |
| `subclass_assistant` | 子职业、碎片、神器查询和确认后修改 |
| `activity_assistant` | 活动历史、PGCR、统计、武器历史、排行榜 |
| `world_assistant` | 周常、商人、收藏品/解锁状态 |

## Starside 本地资料

Starside 作为现有工具的可选资料层，不新增第九个工具，也不把账号凭据发送给网站。
武器分析、Perk 描述、碎片详情、异域护甲及套装详情会附带 `community_references`。
社区内容与 Bungie/Manifest 结果分开，缺少或损坏归档不会阻止原有官方查询。

本机 2026-09-09 快照已接入 136 个页面和 108 个配装块。这些是特定快照的数量，
不是代码里的上限。归档属于本地可选数据，不随 Git 仓库或 Python 安装包分发。

### 查询与匹配

| 入口 | 社区用途 |
| --- | --- |
| `build_assistant(intent="community")` | 按 `query`、`character`、`scenario`、`category` 搜索配装；用 `community_build_id` 读取完整模板并校验 Manifest |
| `weapon_assistant(intent="community")` | 用 `weapon_name` / `perk_name` 搜索武器、Perk 资料 |
| `subclass_assistant(intent="community")` | 用 `query` 搜索技能、碎片、神器说明 |
| `activity_assistant(intent="community")` | 用 `query` 搜索副本、活动及输出资料 |
| `world_assistant(intent="community")` | 用 `query` 跨分类搜索；`community_category` 可选 `armor` / `mechanics` / `sources` 等 |

资料查询先返回短摘要和 `knowledge_id`；再次调用同一入口、传入该 ID 可读取详情。
`community_section="text"` 读取正文，`tables` 读取带表头及合并单元格信息的表格，
`links` 读取原页面引用的外链。按响应的 `next_offset` 翻页；正文以字符计，表格以行计，
搜索以条目计。搜索完整扫描后再分页，`limit` / `top_n` 不会缩小扫描范围。

配装列表只返回玩家已存配装和 Bungie 官方槽位，不是社区推荐列表。每项都使用统一的
`build_template` 结构：`class`、`weapons`、`armor`、`artifact`、`stat_targets`、`source`。
官方槽位还保留 `slot_number`、`native_character_id`、名称/图标/颜色 hash；这些是执行和展示元数据，
不是社区热度或推荐依据。指定社区 ID 后才返回原文、结构化要求、校验结果和可选库存匹配。
`include_inventory=false` 不读取账号；默认 `true` 时，仅在明确指定或唯一命中模板后读取库存。
库存结果区分已持有、当前 Perk 命中、缺少、名称不确定、未读取或未验证的要求。
套装按不同护甲部位检查候选，不把四个头盔算成四件可穿套装。重复模组会保留。

### 执行边界

**接入完成的是资料检索、模板解析和库存核对，不是网站配装的整套一键执行。**
社区模板不会生成伪造的 `canonical_build`，也不会绕过现有金装和写入确认。
`loadout_assistant` 的官方槽位可以通过 Bungie 原生槽位 ID 执行，但其 `build_template` 仍不能替代
服务器签发的 `canonical_build`；模板结构统一不代表社区资料已经完成可执行性验证。
`solver_handoff` 仅提供部分护甲求解参数，必须经用户确认后使用；它没有自动转换
武器、技能、模组、神器、多个同时生效的套装、注解或数值范围。

未知名称不会报成缺少；当前 Perk 不匹配不代表该武器没有可切换的合适 Perk。
技能解锁、模组能量与插槽兼容、神器解锁、属性可行性和注解中的特殊条件仍需另外验证。
缺少这些验证时，工具会明确返回 `execution_eligible=false`，不能声称已完整复现网站配装。

### 本地数据与更新

默认目录是 `DATA_PATH/starside`，未配置 `DATA_PATH` 时使用项目下的 `data/starside`。
适配器读取 `index.json`、索引列出的 `records/` 和两个 `exports/` JSON 文件，
不执行原始网页、脚本，也不自动抓取外部文章。

仅接受完成状态的 schema v2 归档；失败项、待处理 URL、缺失页面、无效路径或损坏 JSON
会拒绝使用。文件更新后自动重载。每份引用都携带页面地址、页面更新时间、抓取时间、
快照 ID 和内容哈希；这些只能证明本地快照来源，不能证明数值适用于当前游戏版本。
PvP、强化和待验证数值保留为 `[pvp]`、`[enh]`、`[unsure]` 标记。

已有归档可直接使用。更新抓取前应确认网站作者及上游来源允许的使用范围，并遵守站点规则。
项目已有 `scripts/fetch_starside.py` 支持归档和恢复；不要将原始归档推送 GitHub。
代码的 MIT 许可不涵盖 Starside 或其引用的第三方内容。

真实只读复测：

```bash
.venv/bin/python scripts/verify_starside.py
# 可选：额外读取账号库存，仍不执行装备、转移等写入
.venv/bin/python scripts/verify_starside.py --inventory
```

更多自然语言测试及验收标准见 [TESTING.md](TESTING.md)。代码更新后重启 Codex 或新开任务，
避免继续使用旧服务进程。

## License

MIT
