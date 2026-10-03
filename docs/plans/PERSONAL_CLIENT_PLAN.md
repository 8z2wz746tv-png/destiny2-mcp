# 计划：个人客户端（Destiny 2 Personal Agent）——设想评审与修正路线

状态：**评审稿，等你拍板**。不含代码实现，只定产品边界、模块归属与阶段顺序。
缘起：一份外部设想（产品方向 + 总体架构，含 §1–§36 共 36 节）。本文把那份设想逐条对着
**当前仓库**和**两个前身项目**核对了一遍，并补了 2026-09-30 的一次真机只读实测。

前身项目指 `/Users/husky/项目/Destiny_MCP`（服务端 + Web + Hermes + MCP 那一版，31,485 行 Python、
75 个前端文件、部署在 `sunsetshimmer.mindtype.cn`）。它的记录是这份评审里最省时间的部分。

---

## 一、先说结论

1. **方向是对的，而且是对前身项目最痛那件事的正面回答。** 前身项目的工程路线图第一节就是
   「单用户架构被用于多用户场景」：Hermes 的 `HERMES_HOME` 是进程级全局、MCP 注册表按服务器名
   共享、agent cache 与记忆路径都假定单实例 —— 为了多用户隔离，最后不得不把每一次带认证的对话
   塞进一个 OS 子进程（ProcessRuntime）。**砍掉多人 SaaS，等于把这条最贵的线整根拔掉。**
2. **设想里最大的一块其实已经建了 80%。** §27 的「Destiny Core」在今天的仓库里就是
   `destiny_mcp/services/` + `destiny_mcp/build/`：分层守门测试已经禁止 `tools/` 反向依赖服务层、
   禁止环、要求 `svc["…"]` 的 key 在 `service_context.py` 声明。**要做的不是"把业务逻辑从 MCP 里
   抽出来"，而是给核心加第二张皮**（一个非 MCP 的本地接口）。这比设想里读起来的工作量小一个数量级。
3. **Quest Guide 的成本结构被估计错了方向：难的不是代码，是内容。** 它的"状态"这半我已经实测
   能拿到（第三节）；难的是每个任务每个步骤的"下一步 / 在哪 / 注意什么"。这是内容工程，不是软件工程 ——
   前身项目在周常轮换上踩过同一个坑（六类轮换只有两类有官方接口，其余靠自维护表 + 锚点）。
4. **最危险的是 Phase 1：它把两个最大的未知量捆在了一起** —— 移动端运行时 + 移动端认证。
   这两个不拍板，Phase 1 会烂尾，而且烂在"客户端"这个最显眼的地方。
5. **建议顺序改成**：桌面第二张皮 → Quest Guide 三任务试点（量内容成本）→ UI 化 → Triumphs/Collections
   → Agent → 识别 → Overlay。**移动端单独一条线**，等决定 1、2 拍板。

---

## 二、逐条核对：设想里的模块，今天有什么

| 设想的模块 | 今天的对应物 | 状态 |
| --- | --- | --- |
| Account / Characters | `player_svc`、`player_resolver` | 已有 |
| Inventory | `inventory_svc`（清单/搜索/移动/装备/重复/锁定/邮政官） | 已有 |
| Weapon | `weapon_*` 五个服务（分析/对比/筛选/详情/选取率） | 已有 |
| Armor / Build | `build/` 求解器 + `build_svc` + 调谐写入 | 已有（比设想多） |
| Loadout | `loadout_svc`（本地配装 + Bungie 官方 20 槽） | 已有 |
| Subclass | `subclass_svc`、`fragment_svc`、`artifact_svc` | 已有 |
| Activity | `activity_svc`、`activity_counters_svc`、`pvp_weapon_svc`、`rotation_svc` | 已有 |
| World / Vendor | `vendor_svc`、`weekly_svc` | 已有 |
| Collection | `collection_service`（节点搜索、条目状态、节点状态） | **部分** |
| Quest | 只有 `track_quest`（切换"追踪"开关） | **没有步骤与进度** |
| Guide | 无；但 `starside` 知识层（22 份 Markdown + 网页归档 + `source_ref`）可以复用 | **没有** |
| Map | 无 | **没有** |
| Triumph | 无专门模块；**组件 900 已经在读**（图样进度用它，见 ADR-009） | **没有，但地基已有** |
| 社区知识 | `starside`（带 `source_ref` / `trust=untrusted_reference` / 更新时间） | 已有 |

**14 个核心模块里 11 个已经存在。缺 4 个（Quest / Guide / Map / Triumph），其中 3 个的成本远低于
设想的读感。** 设想的 §33 Phase 2「把现有能力整理成 Inventory/Weapon/Build…」基本是已经做完的事。

---

## 三、本次真机实测（2026-09-30，只读）

### 3.1 任务进度：能读，而且够用（**新事实**）

组件 301（`ItemObjectives`）**当前项目一个都没读**，本次首次实测：

- 该账号返回 **49 条带目标的物品**：`itemType=12`（任务）10 条、`26`（悬赏）17 条、
  `3`（武器，击杀类目标）19 条、`0` 3 条；
- 进度能还原成人话：`37/50 已完成活动`、`50/50 微型冲锋枪击杀`、`1/1 已找到凋零之羽`；
- 任务链的**步骤总数**能还原：任务物品的 `setData.itemList` 就是全部步骤，
  `暗地孤灵` = 7 步、`溺水迷宫` = 11 步（每个步骤是一个独立的 itemHash，带 `trackingValue` 排序）。

也就是说，设想 §13 里那张「Step 3 / 8 + 完成 XXX 活动 0/3」的卡片，**数据这一半是成立的**。
剩下那一半（第几步）要做一次算法验证，见第八节。

### 3.2 地图：只有「导演地图」这一层

本地 Manifest 里（实测）：

- 142 个目的地，其中 58 个有 `activityGraphEntries` → 指向一张 `DestinyActivityGraphDefinition`；
- 活动图的 `nodes[]` 每项都带 **`position {x, y, z}`** 和 `activities[{nodeActivityId, activityHash}]`
  —— 这正是游戏里导演地图上那些节点的坐标与对应活动；
- `DestinyLocationDefinition.locationReleases[]` 给出
  `destinationHash + activityGraphHash + activityGraphNodeHash`，
  而 11,002 条目标定义里有 **1,202 条带 `locationHash`** —— 这就是「这个目标去哪做」的自动链路；
- **但 `worldPosition` 是空的**：关卡内部的坐标、路线、隐藏门、Boss 房一律没有。

**结论：能给"去哪个目的地、哪个节点"，给不了"进门之后往哪走"。** 这直接改写设想的 §16
（那张 `Landing Zone → NPC → Mission Entrance → Objective → Boss` 的路径图不是数据，是内容）
与 Phase 6 的定位。

### 3.3 本地数据量：Triumph / Collection 的原料都在

`DestinyRecordDefinition` 6,168 条、`DestinyPresentationNodeDefinition` 2,375 个、
`DestinyCollectibleDefinition` 12,255 条、`DestinyObjectiveDefinition` 11,002 条 —— 全在本地库里。
组件 900（`profileRecords`）已经在读（图样进度），Records 的进度形状就是
`records[hash].objectives[0].progress / completionValue`。

### 3.4 Manifest 体积：手机端「按表裁」不够，必须「按字段裁」

- 磁盘现状：`manifest/destiny_manifest.sqlite3` 343 MB + `destiny_manifest_zh.sqlite3` 342 MB = **685 MB**；
- 只挑客户端核心要的 23 张表，仍占 JSON 总量的 **90%（268 MB）** —— 因为
  `DestinyInventoryItemDefinition` 一张就 **184.7 MB**（38,894 条，含图标/插槽/描述）；
- 想给手机端做子集，只能按字段裁（丢图标路径、插槽表、多语言描述）。**这件事必须先量一次**，
  它决定手机端能不能本地跑核心，还是只能做瘦客户端。

---

## 四、三个必须先拍板的决定

### 决定 1：客户端运行时（决定整个成本）

- **桌面：Python 侧车是自然选择。** 项目本来就是本地进程，MCP 已经支持 HTTP 传输
  （`MCP_TRANSPORT` / `MCP_HOST` / `MCP_PORT`，默认 stdio）。代价要重新算，别照抄前身项目的结论：
  前身项目拒绝侧车的理由是"SaaS 下重复生产状态、OAuth 存储、进程监督与升级责任"，
  **那个理由在个人客户端里不成立**。但打包 CPython + 685 MB Manifest + 首次建库
  （README 实测"几十分钟内不可用"）+ 签名与更新，是实打实的工程量。
- **手机：Python 核心基本不可行**（不能内嵌 CPython，685 MB 构建期也不现实）。三条路：
  ① 手机做 PC 的瘦客户端（局域网或中转）；② 用 Dart 重写核心；③ 只把「任务随航」这一小块
  预生成后增量同步到手机。
  **建议 ① + ③**，理由：③ 正好是设想里手机端真正的差异化（第二条屏的任务随航），
  而 ① 让手机不必承担 Manifest 与求解器的重量。

### 决定 2：认证（手机上是产品级阻塞）

Bungie 要求 confidential client（`client_secret`）；项目还实测出一条反直觉的事实：
**授权 URL 上不能带 `scope`**（带上 100% 登录失败，Bungie 回 `invalid_scope`，令牌 scope 由
Developer Portal 的应用配置决定）。由此：

- 桌面：可以让每个用户自己建一次 Bungie 应用（今天就是这么做的，可接受）；
- 手机：要么把 secret 塞进安装包（可被提取，且与现有"每人自己的 key、不共用"这条口径冲突），
  要么引入一个中转服务（与"本地优先、云端不是必要条件"直接冲突）。

**这条不拍板，移动端不要开工。** 需要的话我可以先去查 Bungie 官方政策原文再定（本次没查，不装懂）。

### 决定 3：Agent 放在哪里

设想把 Agent 当客户端的内置能力。但今天全部能力都在 MCP 上，而 Agent 一直由宿主提供
（DSH / Claude / Cursor）。三条路：

| 路线 | 成本 | 评价 |
| --- | --- | --- |
| ① 客户端自建 Agent（tool-calling 循环 + 对话存储 + 模型费用） | 最高 | 要重新实现一遍宿主已经做好的事 |
| ② 客户端内嵌 MCP 客户端，连本地服务器 | 最低 | **建议**：复用全部工具契约、参数守卫、确认门槛与守门测试 |
| ③ 先不做内置 Agent，只做确定性 UI | 低 | 可作为 ② 之前的过渡 |

**建议 ②，并且要重新认识 MCP 的定位**：设想 §26 把 MCP 只当"对外开放接口"（给 Claude/Cursor 用），
实际上它同时是**内部最好的 RPC 面** —— 八个工具、127 个 intent 别名、参数归属表、信封与错误码
全都已经被测试钉住了。把 MCP 当内部接口用，等于让"UI 走的那套"和"Agent 走的那套"天然是同一套。

---

## 五、修正后的阶段

| 阶段 | 做什么 | 验收 / 产出 |
| --- | --- | --- |
| **P0 拍板 + 一次测量** | 定下决定 1/2/3；量一次"客户端最小 Manifest 子集"（按字段裁）；把这几条写成 ADR | 三条 ADR + 一份体积实测数 |
| **P1 第二张皮** | 在 `services/` 之上加一个本地 HTTP/JSON 面（与 `tools/` 同层的另一个门面）；Flutter 桌面客户端接它；先做 Inventory / Loadout / Build 三屏 | UI 与工具层调**同一批服务**，用现有分层守门证明没有第二套逻辑 |
| **P2 Quest Guide 试点（3 个任务）** | 只做：当前任务 → 当前步骤 → 下一步 → 去哪（导演地图节点）→ 攻略片段 | **产出是内容工作量的实测**："一个任务要多少小时"，不是功能 |
| **P3 Triumphs + Collections** | 组件 900 扩到全量 Records + PresentationNode 树；800 已有基础 | 注意 900 的体积（183 条图样就 1.44 MB / 2.5 s）与两个作用域（档案级 + 角色级，ADR-009 踩过） |
| **P4 Guide 规模化 + Map 数据层** | 把 P2 的流程变成可批量维护的数据表（像 `data/rotations.py` 那样带锚点与更新时间）；地图只做导演层 | 数据表 + 过期口径 |
| **P5 移动端（瘦客户端）** | 手机连 PC 的本地 HTTP 面；离线只缓存"当前任务 + 指导片段 + 地图节点" | 前提：决定 2 已解决 |
| **P6 游戏内识别 / Overlay** | 到这一步再谈 | 见第六节的"别自己做 OCR" |

---

## 六、Quest Guide 的成本真相（最该先读的一段）

- **API 给状态，不给下一步。** 实测已经把状态这半解决了（3.1）；"下一步"没有任何接口。
- **内容三件套只能人工整理**：步骤文案、地点与地图节点、注意事项。P2 用三个任务量出单价，
  再决定规模。前身项目的周常轮换是同一个模式：只有两类有官方数据，其余是自维护表 + 锚点，
  没锚点的（遗失区域）**只给候选**（ADR-010）。
- **必须接受"攻略会过期"**，并在响应形状里写清楚：
  `guide_source` / `guide_updated_at` / 未验证标记；**没有整理过的步骤就说"这个步骤还没有攻略"**，
  不许让模型自己补。这正是仓库既有的"不许静默降级、不许替调用方下结论"纪律。
- **别自己做 OCR。** 前身项目专门为"截图 → 配装"写过一条决定，结论是用多模态模型看图、
  不引入 Tesseract/YOLO（理由：Destiny 2 中文装备名有特殊字体与背景，OCR 准确率低，
  而模型能理解"虫狙"这类游戏术语）。当前项目在剥离历史工具面时也留了一句：
  "不为了留住配装导入而先做截图识别（**那是一条独立的产品线，要做另开计划**）"。
  Phase 6 要做的话，正确起点是"把截图交给多模态模型"，而不是先搭 OCR 管线。

---

## 七、明确不做

沿用设想已经明确的三条（云端多人、第一阶段实时视觉识别、Overlay 优先），再补三条：

1. **不做第二套业务逻辑。** UI 与 Agent 必须调用同一批服务；这条要用分层守门证明，而不是靠约定。
2. **不在手机端复刻全量 Manifest。** 先按字段裁量一次（3.4），量完再决定手机端跑核心还是做瘦客户端。
3. **不把 LLM 当数据库**（同意设想 §5），并且补上它的推论：**客户端必须把算好的结论结构化返回**，
   而不是把原始 JSON 丢给模型 —— 这个项目已经为体积付过一次学费（列 1260 件物品 ≈ 511 KB ≈ 13–15 万 tokens）。

---

## 八、待实测 / 未知（诚实清单）

| 事项 | 现状 |
| --- | --- |
| 任务链"当前第几步"的还原算法 | 只证明了 `setData.itemList`（步骤总数）+ 持有步骤能对上，**没在 3 个以上真实任务上验证**过 |
| objective 文案在中文库的缺失比例 | 本次 49 条里 2 条查不到文案；要按既有口径给 `null` + 原因，不能编 |
| 组件 900 全量 Records 的体积与耗时 | 只知道 183 条图样 = 1.44 MB / 2.5 s，全量未测 |
| Manifest 按字段裁剪后的体积 | 未测。按表裁只有 90%（268 MB），必须按字段裁 |
| Bungie 对 overlay / 截图的政策原文 | **没查**，不当作已知 |
| 手机端认证的合规边界 | 见决定 2，未查原文 |

---

## 九、给未来 agent 的接口约定

动这份计划里的东西时：

- **新模块先登记层号**（`tests/test_architecture_layers.py` 的 `_LAYERS`）；第二张皮与 `tools/` 同层，
  只允许调 `svc["…"]`，key 必须在 `service_context.py` 声明。
- **新组件号写进 `services/profile_components.py`**（301 要新登记，并在注释里写清"谁需要它、干什么"）。
- **Quest / Guide / Triumph 的输出必须带不确定性口径**：没整理的攻略、查不到的文案、没锚点的数据，
  照 `tests/test_conclusion_paths.py` 的规矩留痕，不许静默降级。
- **文档改动登记进 `AGENTS.md` 的文档索引**，否则 `tests/test_agent_docs.py` 判红。
- **写入仍然走既有的确认门槛**（服务端签发候选 + 用户明确 `confirmed=true` + 回读核对），
  客户端不得绕过。
