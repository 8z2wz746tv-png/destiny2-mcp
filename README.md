# Destiny MCP

Destiny 2 装备管理 MCP Server，通过 AI Agent 管理武器和装备。

功能上与 DIM（Destiny Item Manager）类似，区别是走 MCP 协议，用自然语言操作。

## 功能

- 查询玩家信息、角色状态、背包物品
- 转移、装备武器和护甲
- 管理子职业配置（超能、手雷、碎片等）
- 查询武器 perk 池、对比副本、god roll 推荐
- 护甲配装与反推：库存无解时先查单件，单件不足再给合法的两件待刷方案
- 查询商人库存和每周重置活动
- 从配装文章或截图导入配装方案
- 保存和装备配装方案（含模组与碎片配置）
- 默认暴露 8 个聚合 assistant 工具，不把几十个低层工具直接铺给 Agent
- 随附 Starside 中文资料：Perk、武器推荐、护甲、技能、神器、机制和活动数据
- 可选 Starside 网页归档：提供社区配装模板，保留出处，可匹配账号库存
- 调谐（Tuning）参与配装求解：目标差一点时，求解器会实际尝试更换调谐，能补上就返回带
  `tuning_changes`（逐件 from/to 与六维变化）的候选，补不上则说明是额度不足还是无法让步。
  调谐只能在游戏内手动修改（Bungie 插槽接口返回 `This action can only be done in-game.`），
  工具输出的是「改哪几件、改成什么」的清单，`equip_build` 不会代替玩家改调谐
- 写入失败会如实报错：需要消耗能量的插槽写入要求 Bungie 应用具备 `AdvancedWriteActions` 权限，
  缺少权限时接口返回 `AccessNotPermittedByApplicationScope`，工具会指出这一权限，不会把失败报成成功

## 快速开始

这是个人本地版，推荐让本地 Agent 按下面的流程安装。用户只需要完成一次 Bungie 登录，之后 token 保存在本机。

安装前先看[前置条件与已知限制](#前置条件与已知限制)：平台支持、必须自备的 Bungie 应用、717 MB 的首次 Manifest、数据落在本机哪里，都写在那里。其中首次下载耗时最容易被误判成安装失败。

本项目提供两层 Agent 指引：

- [`destiny2-mcp` 通用 Skill](skills/destiny2-mcp/SKILL.md)：工具路由、证据范围、Starside 配装和确认边界；任何能读取 Markdown 的 Agent 都可以使用。
- [`destiny-mcp-setup` 安装 Skill](skills/destiny-mcp-setup/SKILL.md)：本地安装、OAuth、MCP 注册和故障排查。

安装目标是当前正在对话的那个 Agent：在 DSH 里提问就装进 DSH，在 Claude Code 里提问就装进 Claude Code。不需要给每个宿主都装一遍，也不需要把凭据交给第三方。

本文档不针对特定平台，但不同宿主的加载能力不同，按能力分三种落点：

| 宿主能力 | 落点 | 怎么做 |
| --- | --- | --- |
| 有 skills 目录 | DSH `~/.dsh/skills/`、Claude Code `~/.claude/skills/`、Codex `~/.codex/skills/` 等 | 运行 `scripts/install_skill.py`：默认只装当前宿主（靠环境变量识别），`--all` 才是全装 |
| 只读全局指令文件 | DSH `~/.dsh/AGENTS.md`、Claude `~/.claude/CLAUDE.md`、Codex `~/.codex/AGENTS.md` | 加 `--pointer`，写入一段指向文档的指针（幂等，重复运行不会堆积） |
| 只连 MCP、不读文件 | MCP 握手 `instructions` 里的线上地址 | 不需要额外操作，能联网的客户端可以自行读取 |

```bash
.venv/bin/python scripts/install_skill.py --list                  # 看当前宿主是谁、本机有哪些宿主
.venv/bin/python scripts/install_skill.py --dry-run               # 先看会改哪些文件（默认只对当前宿主）
.venv/bin/python scripts/install_skill.py                         # 装到当前宿主的 skills 目录
.venv/bin/python scripts/install_skill.py --mcp                   # 注册 MCP 服务器（DSH 直接写好；其它打印命令）
.venv/bin/python scripts/install_skill.py --all                   # 所有探测到的宿主（一般用不上）
.venv/bin/python scripts/install_skill.py --host dsh              # 指定宿主
.venv/bin/python scripts/install_skill.py --target <目录>          # 其它宿主的 skills 目录
.venv/bin/python scripts/install_skill.py --pointer <文件>         # 其它宿主的规则文件（如 ~/AGENTS.md）
```

仓库始终是唯一源头：脚本只做镜像复制、写入带标记的指针块，以及（DSH）幂等更新 MCP 注册；不改 Codex/Claude 的配置，也不碰宿主自己管理的目录（例如 Cursor 的 `skills-cursor`）。不装 Skill 也能使用：MCP 握手的 `instructions` 和 8 个工具的 schema 每个客户端都会收到，线上也有这份完整文档。

DSH 用户：`--mcp` 会往 `$DSH_HOME/profiles/web/cordis.patch.yml` 插入一条 `@deepseek-ai/dsh-mcp-client` 条目（带 `destiny2-mcp:mcp-begin/end` 标记，重复运行只更新不重复插入），工具以 `mcp__destiny__*` 出现；技能根目录是 `~/.dsh/skills/`，属于热发现，装完不需要重启。前提是该 profile 里装了插件：在 `$DSH_HOME/profiles/web` 下执行 `pnpm add @deepseek-ai/dsh-mcp-client`。

### 使用任意 Agent 安装

支持 `AGENTS.md` 的 Agent 通常会读取仓库级指引；其他 Agent 可以把下面的提示直接发给它：

```text
请先阅读这个项目的 README.md、AGENTS.md（如果你的平台支持）和
skills/destiny2-mcp/SKILL.md。
然后根据你当前 Agent 的 MCP 配置方式完成 Destiny MCP 安装和 Bungie OAuth 登录。
安装完成后运行 scripts/verify_mcp.py，确认 MCP_HANDSHAKE=ok、MCP_TOOL_SCHEMA=ok、
只读账号检查成功，以及默认 8 个工具都可用。
如果需要安装、OAuth、注册或排查问题，再阅读 skills/destiny-mcp-setup/SKILL.md。
不要让我在聊天中粘贴任何密钥、授权码或 token。
```

通用流程是：读取项目指引 → 创建环境并配置 `.env` → 完成 OAuth → 按当前平台注册 stdio MCP → 运行通用验证脚本 → 再运行 Agent 行为测试。MCP 注册成功不等于工具可用，必须完成真实握手和只读调用。

不同 Agent 的配置文件位置和 Skill 自动发现方式不同。本项目不假设某个平台一定支持 `AGENTS.md` 或自动安装 Skill；Agent 应使用自己的 MCP 配置入口，并把项目绝对路径写入命令。不要把 Bungie 凭据放进 MCP 配置或聊天消息。

如果浏览器没有成功回跳本机，不要直接打开 callback 地址；应打开 OAuth 命令生成的完整授权链接，并保持 OAuth 辅助进程运行。

### 1. 准备 Bungie 应用

在 [Bungie Developer Portal](https://www.bungie.net/en/Application) 创建或打开应用：

- 填写 API Key、OAuth client_id、OAuth client_secret 到 `.env`
- Redirect URL 添加：`https://localhost:8765/callback`
- 常用权限建议勾选：`ReadDestinyInventoryAndVault`、`MoveEquipDestinyItems`、`ReadDestinyVendorsAndAdvisors`
- 如果要让工具修改模组等更深层配置，再按 Bungie 页面提示开启 `AdvancedWriteActions`

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

历史工具默认屏蔽。那 69 个旧工具没有参数拦截、没有参数说明、返回契约也不统一，混在工具面里只会增加选错的概率，所以不论哪个 profile 都只暴露 8 个聚合工具。需要排查或兼容旧提示词时显式打开：

```bash
DESTINY_MCP_ENABLE_LEGACY_TOOLS=1   # 打开历史工具，配合下面的 profile 使用
```

| Profile | 默认（历史工具关闭） | 打开 `DESTINY_MCP_ENABLE_LEGACY_TOOLS=1` 后 |
|---|---|---|
| `normal` | 8 个聚合工具（推荐） | 8 个聚合工具 |
| `expert` | 8 个聚合工具 | 聚合工具 + 常用查询类旧工具，用于排查查询问题 |
| `full` | 8 个聚合工具 | 聚合工具 + 全部历史工具，用于兼容旧提示词或开发调试 |

### 3. 准备可选数据（Manifest / DIM Wish List）

**Manifest：建议先下预构建库。** 服务首次启动会从 Bungie 下载 Manifest 并建库，两个库合计约 717 MB（`342 MB + 342 MB`）。这一步只受网速限制：几百兆的下载加建库索引，通常要十几分钟到半小时，期间服务不会响应任何工具调用。想跳过就先取预构建好的数据库：

```bash
mkdir -p manifest
gh release download manifest-data-v1 -R 8z2wz746tv-png/destiny2-mcp -D manifest
```

也可以从 [Releases](https://github.com/8z2wz746tv-png/destiny2-mcp/releases) 页面手动下载后放进 `manifest/`。仓库不直接包含这两个库，因为单文件超过 GitHub 的 100 MB 限制。

这一步不影响安装能否成功，只影响第一次自检要等多久：没有本地库时 `scripts/verify_mcp.py` 会把超时自动放宽到 1800 秒并打印原因；如果 30 秒就超时失败，说明用的是旧版本自检脚本，加 `--timeout 1800` 即可。

**DIM Wish List：** MCP 启动时如果发现数据不存在会自动下载，也可以提前运行：

```bash
.venv/bin/destiny-mcp-fetch-wishlists
```

这份数据用于武器 god roll / PvE / PvP 推荐标注。下载失败不影响背包、转移、配装等基础功能，只会少一部分社区推荐评分。

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

# 或等价的模块方式（推荐写这个）
.venv/bin/python -m destiny_mcp
```

如果提示没有 OAuth token，先重新执行第 4 步登录。

不要用 `python -m destiny_mcp.server` 启动。那条路径在配装求解时会让子进程起不来（`build_validation_error`）：anyio 的 worker 会按路径重跑父进程主模块，而 `destiny_mcp.server` 是带相对导入的模块，重跑必然 ImportError。`python -m destiny_mcp` 是同一个入口的包级写法，支持 `-m`，也是给子进程用的那条。

个人版不需要 Docker。仓库里那份早期部署用的 `Dockerfile` 因为长期引用不存在的 `src/` 目录（`docker build` 必失败）已经删除；需要容器化请自行基于 `python:3.12-slim` 写一份，入口是 `python -m destiny_mcp`。

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

行为测试还要确认写操作先展示目标并等待确认、社区模板不会直接传给 `equip_build`、以及不完整扫描不会被回答成「账号没有」。记录实际工具、intent 和关键参数，不要记录密钥、Token 或未经脱敏的账号日志。

## 前置条件与已知限制

- 写入权限：读操作不受限制；消耗能量的插槽写入需要在 Bungie 应用里勾选 `AdvancedWriteActions`。
  没有该权限时接口返回 403 `AccessNotPermittedByApplicationScope`，工具会指出这一权限；
  这属于权限配置问题，重试不会成功。
- 调谐只能游戏内修改：Bungie 的免费插槽接口对调谐返回 `This action can only be done in-game.`
  （ErrorCode 1663）。`find`/`recommend` 返回的 `tuning_changes` 是给玩家的手动清单，
  照着在游戏里改完，方案里的六维才成立；`equip_build` 只更换护甲与模组。
- 无解诊断耗时较长：真正配不出来时会给「六维阶梯」（逐级放松目标实采），实测 80–165 秒；
  求解器还会先尝试调谐补齐。MCP 客户端超时建议设为 300 秒（DSH 的 `toolCallTimeoutMs`）。

第一次运行前需要了解的现状：

| 项 | 现状 |
| --- | --- |
| Python | 3.12+（只在 3.13 上长期实测） |
| 平台 | macOS 实测通过；Linux 应当可用但未逐一验证；Windows 未实测——命令要换成 `.venv\Scripts\...`，且登录助手生成临时证书依赖 `openssl`，缺了就用 `destiny-mcp-oauth --manual`（自检脚本已按平台分支，不再用 POSIX 权限位判 Windows） |
| 网络 | 需要能访问 `bungie.net`、GitHub（下预构建 Manifest）、PyPI |
| Bungie 应用 | 每个使用者必须用自己的 API Key + Confidential client_id/secret，回调地址填 `https://localhost:8765/callback`（创建应用即分配 key，不需要等审批）；不要共用同一份 key，写操作还要在门户里额外勾选 `AdvancedWriteActions` |
| 首次运行 | 要下载并建库约 717 MB Manifest，几十分钟内不可用；可先取预构建库跳过（见第 3 步）。DIM 愿单在启动时自动下载，失败只降级 |
| 构建/测试 | `pip install -e .` 只装运行依赖；跑 `pytest` 需要 `pip install -e ".[dev]"`。干净克隆（没有 `.env`）时 `tests/test_bungie_client_lifecycle.py` 与 `tests/test_exact_build_execution.py` 共 5 条会失败，先按第 2 步建 `.env` 即可全绿 |
| Docker | 不支持。早期那份 `Dockerfile` 引用了不存在的 `src/` 目录，已删除 |
| 数据落在哪 | Token 在 `~/.destiny_mcp/tokens.json`（0600）。每次工具调用的审计日志写在 `~/.destiny_mcp/audit/YYYYMMDD/`，含调用参数与最多 5 万字符的结果摘要，明文保存、不加密、不上传；不想要就删该目录。没有遥测，也不向本项目之外的服务器上报任何内容 |

已知功能限制（细节与复现话术见 [TESTING_CORPUS.md](TESTING_CORPUS.md) 的「已知问题」）：

- 单进程、单用户；不要同时开两个实例操作同一账号，跨进程互斥不在设计范围内。
- 社区资料是本地快照：哈希只能证明快照来源，不能证明数值适用于当前游戏版本。
- 大规模配装求解很慢（术士全套组合可到 2 亿量级，会直接返回 `precision="not_computed"` 并给出收窄建议）；只有 T5 护甲做词条反推。
- 部分上游失败消息会把 Bungie 返回的原文带出来；写入成功后暂不返回 `next_actions`。
- 不支持多账号切换或双实例。

## 技术栈

- Python 3.12+
- [MCP](https://modelcontextprotocol.io/) (FastMCP)
- [aiobungie](https://github.com/nxtlo/aiobungie) (Bungie API 客户端)
- Pydantic v2

## 架构

单进程、单用户的模块化单体。所有 Bungie 写操作在进程内共享账号锁，写入完成、失败或取消后，依赖账号状态的缓存都会失效。不要同时启动多个实例操作同一 Bungie 账号，跨进程互斥不在设计范围内。

对外始终只暴露 8 个聚合工具（历史工具要显式打开），内部按意图校验参数并委托领域服务；配装组合计算在独立工作进程中运行，每个服务实例最多同时计算一个任务，默认 300 秒预算（`DESTINY_BUILD_TIMEOUT_SECONDS` 可调，只计算真正在计算的时间，排队不计入）；超时或取消会终止该计算进程，不阻塞 MCP 请求循环。

服务器通过 `create_server()` 显式注册工具。认证或 Manifest 初始化失败时停止启动，`/health` 仅在初始化完成后的生命周期内返回就绪。配装导入输出 `BuildRecipe`，可执行候选使用 `ExecutableBuild`，并继续校验一次性候选凭据和库存快照。

```
destiny_mcp/
├── server.py          # MCP Server 入口，生命周期管理
├── config.py          # 环境变量配置
├── service_context.py # 有类型的服务上下文
├── build_contracts.py # 配装需求、兼容格式与可执行计划
├── bungie_client.py   # Bungie API 客户端
├── manifest.py        # 游戏数据清单管理
├── models/            # Pydantic 数据模型
├── tools/             # MCP 工具定义（默认 8 个 assistant；69 个历史工具需显式打开）
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

> **数据来源与致谢**：本项目的社区资料（`share/` 下的 Markdown 文档、`data/starside/` 的网页归档、社区配装模板）来自 **Starside**（<https://starside.work/index.html>），经网站作者许可随附与再分发。这些是**参考数据、不是 Bungie 官方数据**，版权归原作者与上游来源；工具在响应里保留 `source_ref`（`url` / `updated_at` / `trust=untrusted_reference`）以便逐条溯源。社区配装不能一键执行，社区评分不等于官方推荐。细节见 [COMMUNITY_DATA_NOTICE.md](COMMUNITY_DATA_NOTICE.md)。

Starside 是现有工具的本地资料层，不新增第九个工具，也不把账号凭据发送给网站。武器分析、Perk 描述、碎片详情、异域护甲及套装详情会附带 `community_references`。社区内容与 Bungie/Manifest 结果分开，缺少或损坏资料不会阻止原有官方查询。

仓库随附作者授权的 22 份 Markdown 文档，覆盖 Perk、武器框架与推荐、异域装备、护甲套装、子职业、神器、Boss 数据及游戏机制。克隆仓库或通过 wheel 安装后会自动发现，不需要解压 ZIP、抓取网站或手动导入。可用 `STARSIDE_SHARE_PATH` 指向更新后的文档目录。

此前的 schema v2 网页归档仍受支持，并可与 Markdown 合并查询；它额外包含 108 个社区配装块。仓库已随附该归档中运行所需的部分，但原始抓取页与素材不入库，详见下文「本地数据与更新」。Markdown 数据包不含完整角色配装模板，不能根据武器推荐表自行拼成「热门配装」。具体许可与来源边界见 [`COMMUNITY_DATA_NOTICE.md`](COMMUNITY_DATA_NOTICE.md)。

### 查询与匹配

| 入口 | 社区用途 |
| --- | --- |
| `build_assistant(intent="community")` | 按 `query`、`character`、`scenario`、`category` 搜索配装；用 `community_build_id` 读取完整模板并校验 Manifest |
| `weapon_assistant(intent="community")` | 用 `weapon_name` / `perk_name` 搜索武器、Perk 资料 |
| `subclass_assistant(intent="community")` | 用 `query` 搜索技能、碎片、神器说明 |
| `activity_assistant(intent="community")` | 用 `query` 搜索副本、活动及输出资料 |
| `world_assistant(intent="community")` | 用 `query` 跨分类搜索；`community_category` 可选 `armor` / `mechanics` / `sources` 等 |

资料查询先返回短摘要和 `knowledge_id`；再次调用同一入口、传入该 ID 可读取详情。`community_section="text"` 读取正文，`tables` 读取带表头及合并单元格信息的表格，`links` 读取原页面引用的外链。按响应的 `next_offset` 翻页；正文以字符计，表格以行计，搜索以条目计。搜索完整扫描后再分页，`limit` / `top_n` 不会缩小扫描范围。

配装列表只返回玩家已存配装和 Bungie 官方槽位，不是社区推荐列表。每项都使用统一的 `build_template` 结构：`class`、`weapons`、`armor`、`artifact`、`stat_targets`、`source`。官方槽位还保留 `slot_number`、`native_character_id`、名称/图标/颜色 hash；这些是执行和展示元数据，不是社区热度或推荐依据。指定社区 ID 后才返回原文、结构化要求、校验结果和可选库存匹配。`include_inventory=false` 不读取账号；默认 `true` 时，仅在明确指定或唯一命中模板后读取库存。库存结果区分已持有、当前 Perk 命中、缺少、名称不确定、未读取或未验证的要求。套装按不同护甲部位检查候选，不把四个头盔算成四件可穿套装。重复模组会保留。

### 执行边界

接入完成的是资料检索、模板解析和库存核对，不是网站配装的整套一键执行。社区模板不会生成伪造的 `canonical_build`，也不会绕过现有金装和写入确认。`loadout_assistant` 的官方槽位可以通过 Bungie 原生槽位 ID 执行，但其 `build_template` 仍不能替代服务器签发的 `canonical_build`；模板结构统一不代表社区资料已经完成可执行性验证。`solver_handoff` 仅提供部分护甲求解参数，必须经用户确认后使用；它没有自动转换武器、技能、模组、神器、多个同时生效的套装、注解或数值范围。

未知名称不会报成缺少；当前 Perk 不匹配不代表该武器没有可切换的合适 Perk。技能解锁、模组能量与插槽兼容、神器解锁、属性可行性和注解中的特殊条件仍需另外验证。缺少这些验证时，工具会返回 `execution_eligible=false`，不能据此声称已完整复现网站配装。

### 本地数据与更新

随附 Markdown 默认从源码根目录 `share/` 或安装前缀的 `share/destiny-mcp/community` 读取。适配器按文档、标题层级和表格行建立检索条目，并保留 `{pvp|...}`、`{enh|...}`、`{unsure|...}`、`{note|...}` 的语义；图片缺失不会阻止文字查询。

可选网页归档目录是 `DATA_PATH/starside`，未配置 `DATA_PATH` 时使用项目下的 `data/starside`。适配器读取 `index.json`、索引列出的 `records/` 和两个 `exports/` JSON 文件。不执行原始网页或脚本，也不在查询时访问 Starside。

仓库已随附该归档中 MCP 运行需要的部分（`index.json`、`records/`、`exports/`、`categories/`、`metadata/`、`texts/`，约 17 MB），克隆后即可直接查询，不需要额外下载。归档里的 `pages/`（原始 HTML）与 `assets/`（3824 个图标与前端资源）运行时不会被读取，因此不入库；需要完整原始页时用 `scripts/fetch_starside.py` 重新抓取，抓取会补全这两部分。

仅接受完成状态的 schema v2 归档；失败项、待处理 URL、缺失页面、无效路径或损坏 JSON 会拒绝使用。文件更新后自动重载。每份引用都携带页面地址、页面更新时间、抓取时间、快照 ID 和内容哈希；这些只能证明本地快照来源，不能证明数值适用于当前游戏版本。PvP、强化和待验证数值保留为 `[pvp]`、`[enh]`、`[unsure]` 标记。

作者 Markdown 更新后重启 MCP 即可加载；服务也会在文件变化后自动重建内存索引。已有网页归档可继续使用。再次抓取网站前仍应确认网站作者及上游来源允许的使用范围，并遵守站点规则。代码的 MIT 许可不自动涵盖 Starside 或其引用的第三方内容。

真实只读复测：

```bash
.venv/bin/python scripts/verify_starside.py
# 可选：额外读取账号库存，仍不执行装备、转移等写入
.venv/bin/python scripts/verify_starside.py --inventory
```

自然语言测试话术、期望路由与验收标准统一放在 [TESTING_CORPUS.md](TESTING_CORPUS.md)（含 8 个工具与 108 个 intent 的语料、环境与流程、已知问题）；八个工具面全部 intent 的可执行体检与字段级契约在 [TESTING_CORPUS_FULL.md](TESTING_CORPUS_FULL.md)，一条命令跑完：`.venv/bin/python scripts/run_corpus_all_rows.py`。代码或文档更新后，重启 Agent 宿主或新开一个任务，避免继续使用旧的服务进程。

## License

MIT，全文见 [LICENSE](LICENSE)。

这个许可只覆盖本仓库的软件。随附的社区资料（`share/` 下的 Starside Markdown、`data/starside/` 里的归档）是按网站作者的许可再分发的，不因为 MIT 而改变归属；运行时才下载的 Bungie Manifest、DIM 愿单等第三方数据同理。细节见 [COMMUNITY_DATA_NOTICE.md](COMMUNITY_DATA_NOTICE.md)。仅供个人非商业使用，本项目与 Bungie 无关、也未获其背书。
