# 老 WebUI 改造成 DSH 插件（方案）

目标：把**真正部署过的那套老 web**（`~/项目/Destiny_MCP/webui`，界面名 **Ghost Intelligence**）里
**Destiny 专属的界面**搬进 DSH，做成一个客户端插件；数据继续走 `destiny2-mcp`。
**webnext（`~/项目/destiny-webui_next`）是废案，不参与**。

## 一、核到的现状（2026-10-27 实读）

| 项 | 事实 |
| --- | --- |
| 老 web 入口 | `webui/server.py`（545 行，`ThreadingHTTPServer`，默认端口 **8787**，`HERMES_WEBUI_PORT`）|
| 老 web 后端 | `webui/api/*`（auth / streaming / sessions / commands / config / render blocks / icons …）|
| 数据层 | `webui/inventory_gateway.py` 是**代理**（`_proxy_inventory_request` / `_request_upstream`），不是自己拼库存 |
| 老 web 前端 | `webui/src` 共 **85** 个 ts/tsx/css 文件：`components / features / store / styles / lib`（React + TS + Vite）|
| 界面 | 左栏=聊天/仓库/任务 + 技能/记忆/MCP 管理/设置；中栏=会话；右栏=欢迎页 + 建议问题 |
| DSH 插件形态 | npm 包；`main: lib/index.js`（host 半）+ `exports["./client"]: lib/client.js`（浏览器半）|
| DSH 客户端注册 API | `window.__ModuleLoader__.load({ id, factory })`；注册 `route` / `nav` / `page`（task-board 里 110 处 `route`、29 处 `nav`）|
| 参照模板 | `@linxin666/dsh-client-ui-task-board`（host `lib/index.js` 11.4k 行 + client 13.8k 行；① **不修改 DSH 源码** ② 有 cordis service 在两端通信）|

## 二、范围（砍掉一半以上）

**搬**：
1. **仓库页**（最值钱）：装备列表、详情、perk 面板、按类型分组；
2. **任务页**（若有独立数据源则看情况）；
3. **渲染块**（`api/destiny_render_blocks.py` 对应的卡片/面板渲染规则与样式 token）。

**不搬**：聊天壳、会话管理、流式输出、认证/登录、上传、命令系统 —— 这些 **DSH 本身就有**，
老 web 的 `server.py + api/*` 本质上是在重造一个 DSH，搬过来是白干。

> 一句话：**只搬 Destiny 的"皮和面板"，不搬"壳和会话"**。

## 三、架构

```
dsh-plugin-destiny（npm 包）
├── lib/index.js   host 半：一个 cordis service（如 destinyWeb），负责调 destiny2-mcp 的工具
└── lib/client.js  浏览器半：注册 route/nav，渲染仓库页等；数据全从 host service 拿
```

- **登录/授权不用插件管**：MCP 已有 OAuth（token 在 `~/.destiny_mcp`），插件只调工具；
- **host 半调 MCP**：照 `dsh-mnemon` 的做法（它是 host 侧插件，能起/用外部进程）；
- 装法：`~/.dsh/profiles/desktop/package.json` 加依赖 + `dsh.profile.bundles`，`cordis.patch.yml` 配一个 id。

## 四、阶段（P0 先验证风险，越小越好）

| 阶段 | 内容 | 验收 |
| --- | --- | --- |
| **P0** | 照 task-board 抄一个**空页面插件**：能出现在左栏导航、能开页、能调一次 MCP（读背包） | 页面出现 + 数据拿到 + HMR 生效 |
| **P1** | 搬**仓库页**（列表 → 详情 → perk 面板） | 与老 web 并排截图对比 |
| **P2** | 任务页 + MCP 管理页（如果有价值） | 同上 |
| **P3** | 渲染块/卡片 + 老 web 的 UI token 对齐 | 样式抽查 + 与 DSH 现有皮肤不打架 |

## 五、风险与未验（如实记）

- **DSH 客户端插件 API 没有公开文档**：只能读现成插件源码反推（task-board / git-graph / skill-explorer 都在
  `~/.dsh/profiles/desktop/node_modules/@linxin666/` 下），**会花时间**，但已确认"能加页面、能加导航"。
- **未验**：① client 半能否**直接**拿到 MCP 工具结果（还是要经过 host service 中转）；② 插件里有没有
  "页面路由"的正式 API（还是只能跟着 DSH 的固定位置）；③ HMR 需要 `pnpm run dev:web` 同时跑。
- **样式对齐**：老 web 是 Braytech 深色 + 金色；DSH 有皮肤体系，硬套可能"外来户"，P3 专门处理。
- **不做**：不改 DSH 源码、不做老 web 后端兼容层（违反"不做兼容"原则）。

## 六、状态

- 2026-10-27：**调研完成**（上表全部为实读）；**代码零落地**；等确认后从 **P0** 开始。
