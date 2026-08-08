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

## 快速开始

这是个人本地版，推荐让本地 Agent 按下面流程安装。用户只需要完成一次 Bungie 登录，之后 token 会保存在本机。

供 Codex Agent 使用的完整安装、OAuth 与故障排查流程见
[`destiny-mcp-setup` Skill](skills/destiny-mcp-setup/SKILL.md)。

### 使用 Agent 安装

仓库根目录的 [`AGENTS.md`](AGENTS.md) 会让 Codex Agent 自动发现安装指引。可以把下面的提示直接交给 Codex 或其他代码 Agent：

```text
请先完整阅读仓库根目录的 AGENTS.md 和 skills/destiny-mcp-setup/SKILL.md，
然后按 Skill 完成 Destiny MCP 安装、Bungie OAuth 登录、Codex 注册和真实验证。
不要让我在聊天中粘贴任何密钥、授权码或 token。
```

安装前 Agent 能通过仓库中的 `AGENTS.md` 和 Skill 获取流程；MCP 注册并完成握手后，Codex 才能看到运行时工具。不要直接打开 `https://localhost:8765/callback`，应打开 OAuth 命令生成的完整授权链接，并保持 OAuth 辅助进程运行。

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

## 技术栈

- Python 3.12+
- [MCP](https://modelcontextprotocol.io/) (FastMCP)
- [aiobungie](https://github.com/nxtlo/aiobungie) (Bungie API 客户端)
- Pydantic v2

## 架构

```
destiny_mcp/
├── server.py          # MCP Server 入口，生命周期管理
├── config.py          # 环境变量配置
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
| `loadout_assistant` | 本地配装、Bungie 官方配装槽读取/写入/清空 |
| `subclass_assistant` | 子职业、碎片、神器查询和确认后修改 |
| `activity_assistant` | 活动历史、PGCR、统计、武器历史、排行榜 |
| `world_assistant` | 周常、商人、收藏品/解锁状态 |

## License

MIT
