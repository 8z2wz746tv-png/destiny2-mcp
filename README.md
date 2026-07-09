# Destiny MCP

Destiny 2 装备管理 MCP Server — 通过 AI Agent 管理武器和装备。

类似 DIM（Destiny Item Manager），但通过 MCP 协议与 AI 交互，支持自然语言操作。

## 功能

- 🔍 查询玩家信息、角色状态、背包物品
- 📦 转移、装备武器和护甲
- 🎯 管理子职业配置（超能、手雷、碎片等）
- 🔫 查询武器 perk 池、对比副本、god roll 推荐
- 🛡️ 推荐最优护甲配装方案（100 韧性/纪律等）
- 🛒 查询商人库存和每周重置活动
- 📥 从配装文章/截图导入配装方案
- 💾 保存/装备配装方案（含模组和碎片配置）

## 快速开始

### 环境变量

复制 `.env.example` 为 `.env` 并填入 Bungie API 凭据：

```bash
cp .env.example .env
```

### 安装

```bash
pip install -e .
```

### 运行

```bash
# stdio 模式（默认）
destiny-mcp

# 或直接运行
python -m destiny_mcp.server
```

### Docker

```bash
docker build -t destiny-mcp .
docker run --env-file .env destiny-mcp
```

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
├── tools/             # MCP 工具定义（~45 个工具）
├── services/          # 业务逻辑层（25+ 个 service）
├── utils/             # 工具函数
└── build_import/      # 配装导入模块
```

## License

MIT
