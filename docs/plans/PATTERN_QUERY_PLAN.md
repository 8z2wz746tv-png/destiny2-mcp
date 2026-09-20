# 锻造图样查询 · 开发计划 v2（已拍板，开工）

状态：**已拍板**（2026-09-20：只做图样 183；红框库存留第二阶段；**来源联动做**）
范围：新增一个只读 intent（`weapon_assistant(intent="patterns")`）+ 一个服务模块 + 一个社区来源索引模块 + 两处登记（组件号、Manifest 表白名单）
不动：写入链路、`is_craftable` 现有语义（219 件那个口径保留，见第 3 节）、其他工具面

---

## 1. 游戏里那一页到底是什么（实测）

「收藏品 → 模式和催化」= 展示树 `2642502414` → `3442838224`，下面四组：

| 组 | 节点 hash | 内容 |
| --- | --- | --- |
| 主武器模式 | 127506319 | 81 条（7 种武器类型） |
| 特殊武器模式 | 3289524180 | 62 条（6 种） |
| 重武器模式 | 1464475380 | 40 条（5 种） |
| 异域催化 | 2744330515 | 141 条（动能 50 / 能量 55 / 威能 36） |

**合计 324 条，其中武器图样 183 条、异域催化 141 条。**

每条图样是一条 **record**（`recordTypeName`：中文「武器模式」/ 英文 `Weapon Pattern`），名字与武器完全同名，
描述「在此武器上完成深视共振萃取将会解锁其模式。」，目标 `progressDescription` =「模式进度」，
`completionValue` = 要萃取几次：

| 需要的次数 | 把数 | 稀有度 |
| --- | --- | --- |
| 5 | 148 | 传说 |
| 3 | 7 | 传说 |
| 2 | 4 | 传说 |
| 1 | 24 | 异域 16 + 传说 8 |

（口径与直觉一致：一般 5 把，金枪 1 把。）

## 2. 进度只能从组件 900 拿（实测，决定实现）

| 组件 | 里面有什么 | 结论 |
| --- | --- | --- |
| **900 profileRecords** | `records[记录hash].objectives[0].progress / completionValue` —— 就是游戏里那个「图样进度 4/5」 | **唯一出处** |
| 800 profileCollectibles | 图样解锁状态**不在**这里 | 今天用 `collectible_node` 查「模式和催化」只能回 `total=0`（带 `empty_reason`）—— 实测过，这不是 bug 是组件分工 |
| 1300 characterCraftables | 219 条、`visible` 全为 true；给的是"这把能塑形哪些 perk" | 给不出进度，而且 2.93 MB / 0.90 s |

成本（本机实测）：

| 步骤 | 冷 | 热 |
| --- | --- | --- |
| 走展示树（27 个节点） | 2 ms | 2 ms |
| 183 条图样 + 需求次数 + 关联武器（含 324 次物品名查询） | 511 ms | 2 ms（定义缓存） |
| 读组件 900（1.44 MB） | 2.55 s | 2.55 s（唯一的大头） |

→ 组件 900 要**每次现读**（除缓存外没有便宜路径），所以做 5 分钟 TTL 缓存，且只缓存提取后的
183 行状态，不缓存 1.44 MB 原文。

## 3. 两个口径：183 还是 219

- `is_craftable`（`inventory.recipeItemHash` 非空）= **219 件**，其中 36 件是 `（专家）/（失时）/（痛苦）`
  变体；图鉴不给它们单列图样 —— 用 219 会把变体重复算进去。
- 图鉴口径 = **183 条记录**。判据可以做到零歧义：记录名与可锻造物品名**精确相等**（实测 183/183 命中，
  无一例外；可锻造物品里也没有重名）。

新 intent 用 183 口径，并在响应里写明"另有 36 件变体不单列图样"，不把它们藏起来。

## 4. 设计

### 4.1 intent 与参数

- `weapon_assistant(intent="patterns")`；别名 `pattern` / `patterns` / `craft` / `锻造` / `图样` / `图样进度`
  （写进 `tools/_requests.py` 的意图表，别名要么永久要么登记待删）
- 参数全部复用已有归属，不新增：`weapon_name`（查单把）、`weapon_type`（按类型筛）、`limit` / `offset`（翻页）

### 4.2 响应形状

```json
{
  "ok": true,
  "summary": "锻造图样：已解锁 149 / 183；进行中 2 把；未开始 32 把。",
  "data": {
    "counts": {"total": 183, "unlocked": 149, "in_progress": 2, "not_started": 32},
    "by_group": [{"slot": "主武器模式", "total": 81, "unlocked": 66, "in_progress": 1, "not_started": 14,
                  "by_type": [{"weapon_type": "脉冲步枪", "total": 17, "unlocked": 13}]}],
    "patterns": {"total": 183, "returned": 20, "offset": 0, "next_offset": 20, "items": []},
    "variants_note": "另有 36 件（专家/失时/痛苦）变体不单列图样，图样记录挂在基础版上。",
    "read": {"component": 900, "cached": false, "elapsed_ms": 2550}
  }
}
```

列表行（尽量瘦）：`name` / `weapon_type` / `slot` / `tier` / `need` / `progress` / `status` / `item_hash`。

- 三档 status：`已解锁`（记录已完成）/ `进行中`（有进度未满）/ `未开始`
- **`未开始` 的措辞是红线**：账号里没有这条记录，只能说"接口未返回该图样的进度（游戏里是未解锁状态）"，
  不能写成"0/5"——我们没拿到 0，只是没拿到记录
- 排序：`进行中` → `未开始` → `已解锁`，同档按槽位/类型分组顺序，方便回答"我还在做哪几把 / 还差哪几把"

### 4.3 落点

| 改动 | 位置 |
| --- | --- |
| 新服务（目录 + 状态 + 拼装） | `destiny_mcp/services/pattern_service.py`（层 3，注册进 `ServiceContext`） |
| 组件号 | `services/profile_components.py` 新增 `PATTERNS = [900]`，注释写清"不并进 FULL：+1.44 MB / +2.5 s，只有一个用途" |
| Manifest 白名单 | `manifest_definitions.py` 的 `_VALID_TABLES` 加 `DestinyRecordDefinition`、`DestinyObjectiveDefinition`（这两张表至今没被用过） |
| 工具分支 | `tools/_weapon_branches.py`（分支响应按域放 `_*_branches.py`） |

目录判据**不认本地化类型字符串**（中英「武器模式」都不当判据）：走展示树拿分组，再用
「记录名 == 某件 `recipeItemHash` 非空物品的名字，且精确相等」筛出图样。这条同时把 141 条催化挡在门外
—— 催化记录名带「催化」后缀，用模糊匹配会误中同名武器，必须精确比。

### 4.4 守门与验证

- 单测（替身 manifest + 假 profile）：目录构建、状态三档、分页与筛选、`未开始` 措辞、
  催化不得混入、变体不重复计数；每个新守门都要注入一次违规确认变红
- 语料：新增 `patterns` 组（总览 / 单把 / 按类型 / 翻页 / 别名）
- 真机核对（**只有游戏里那一页能证明口径**）：已解锁 **149 / 183**、进行中两把
  **累积救赎 4/5（Accrued Redemption）**、**狂徒的奖励 2/5（Zealot's Reward）**
- 文档：`docs/COMPATIBILITY.md` 一行（新增 intent，非破坏性）、**新写一条 ADR**（上游限制类：图样进度
  只在组件 900，800/1300 都不是；以及 183 vs 219 的口径；编号取当时最大号 +1）、
  `docs/reference/bungie_api.md` 补一条实测、`skills/destiny2-mcp/` 两份文档、`AGENTS.md` 索引

## 5. 来源联动（已拍板：做）

本地已有 Starside「锻造武器来源」页（`data/starside/texts/activities/crafting/index.md`，
页面 id `page:crafting/index.html`，页内写 `Updated: 2026.8.30`）。实测它的形状与覆盖：

| 项 | 实测 |
| --- | --- |
| 结构 | 45 行表格，按 `heading` 分组：突袭 89 / 异域任务 70 / 地牢 33 / 目的地活动 29 / 仄出售 23（这些是名字出现次数） |
| 行 | `{heading, 行首标签, 名字列}`，名字在单元格里用 `、` 连写 |
| 命中 | 名字归一化后 **170 / 183**（表格） |
| 只在该页正文里 | 2（宿命、享乐主义 —— 地牢段落的散文里，如「二象性另会掉落宿命（尾王）」） |
| 该页没有 | 11（枯骨鳞片、太攀蛇-4fr、阿米特AR2、谜团、拉格茜尔-D、巴尔米拉-B、切分音-53、引力子尖刺、青龙/朱雀/玄武意图之刃 等） |

名字归一化（**只此一处**，要配守门测试）：去空白与间隔号、大小写折叠、去掉 `_v1.0.3` 这类版本后缀、
统一横线 —— 实测这四条把 IKELOS 四把、伊尔·约特两把、太攀蛇这类从"对不上"救回来。

输出形状（来源是**社区资料**，必须区别于账号/Manifest 数据）：

```json
"sources": {
  "available": true,
  "page": {"title": "锻造武器来源 · Starside", "url": "https://starside.work/crafting/index.html",
           "updated_at": "2026.8.30", "trust": "untrusted_reference"},
  "matched": 170, "total": 183,
  "note": "本地社区资料，非实时数据；10 把在这份清单里没有对应行（不是没有来源）。"
}
```
每行只带短文本：`"source": "突袭｜克洛塔的末日"`（表格命中）或 `"正文｜地牢"`（散文命中）；
没命中的行 `source: null`，并在 `note`/`warnings` 里说明"本地清单没有这一行"——**不能**写成"这把没有来源"。

落点：新模块 `destiny_mcp/services/starside_crafting_sources.py`（仿 `starside_rated_lists.py`：
自己建索引、`StarsideService.lookup_crafting_sources()` 转发），失败只降级成 `available: false` + warning，
不影响图样本身的账号结果。

## 6. 待拍板（已拍板 2026-09-20）

1. **范围**：只做图样 183 ✅
2. **红框库存**：留第二阶段 ✅（要做时先实测红框在 API 里怎么表示 —— 现在只看到插槽里那个
   「空深视插槽」plug，152 件持有副本都有，判定"有红框"还没验）
3. **来源联动**：做 ✅（见第 5 节）
4. **列表默认条数**：20 条 + `next_offset`

## 7. 明确不做

- 不改 `is_craftable` 的语义（那是"定义可锻造"，与分析/筛选口径一致，另加 36 件变体属正常）
- 不做萃取/解锁的写入或提醒（本工具只读账号）
- 不把组件 900 并进 `FULL`（会让所有武器查询都多拉 1.44 MB / +2.5 s）
