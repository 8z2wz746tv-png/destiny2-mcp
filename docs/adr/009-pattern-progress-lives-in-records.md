# ADR-009: 锻造武器模式（红框）的进度只在 profile 记录组件（900）里

- Status: accepted
- Date: 2026-09-20
- Decision By: maintainer
- Scope: `destiny_mcp/services/pattern_service.py`、`destiny_mcp/services/profile_components.py`、
  `destiny_mcp/services/starside_crafting_sources.py`、`weapon_assistant(intent="patterns")`

## Context

用户要的是游戏里「收藏品 → 模式和催化」那一页的武器模式（玩家口中的红框进度，183 条；
游戏里那条写「模式进度 4/5」）。实测（2026-09-20，真机 + 本机 Manifest）：

- 那一页是**记录**结构：根展示节点 `2642502414` → 分组容器 → 主/特殊/重武器模式 → 武器类型 →
  183 条 record，另加「异域催化」141 条；每条图样记录的 `objectives[0]` 就是进度与需求次数
  （实测分布：5 次 148 把、3 次 7 把、2 次 4 把、1 次 24 把）。
- **组件 800（`profileCollectibles`）里图样解锁状态一条都没有**：拿节点 hash 查
  `collectible_node` 只能回 `total=0`（响应里那句"条目的解锁状态不在这个组件里"说的就是它）。
- **组件 1300（`craftables`）**：219 条、`visible` 全为 true、2.93 MB / 0.90 s，
  回答的是"这把能塑形哪些 perk"，**没有进度**。
- **组件 900（`profileRecords`）**：`records[记录hash].objectives[0].progress / completionValue`
  就是游戏里那个数字；1.44 MB / 约 2.5 s。
- `is_craftable`（`inventory.recipeItemHash` 非空）是 **219 件**，比图鉴多 36 件
  `（专家）/（失时）/（痛苦）`变体 —— 它们不单列图样，图样记录挂在基础版上。
- 36 件变体（专家/失时/痛苦）**塑形配置也不一样**（实测 2026-09-20，无一例外）：
  基础版图样条目的 `crafting.requiredSocketTypeHashes` 是 5 个栏位（框架/枪管/弹夹/特征1/特征2），
  变体只有 3 个（框架/枪管/弹夹）→ **三四号特性固定**；组件 1300 的实测一致
  （基础版每条插槽 11/19/15/19/19 项，变体 11/19/15 项）。而且变体的插槽里**没有「空深视插槽」**
  （换成强化插槽）→ 红框（深视共振）只会掉基础版。组件 1300 的键是**图样条目 hash**，不是武器 hash。
- 术语：玩家说「红框」（深视共振武器），游戏官方中文说「模式」（那条进度写「模式进度」），
  英文是 pattern、第三方工具常译作「图样」。三个词指同一件事，答话时用**玩家那个词**（响应里
  带 `terms` 对照表）。

**What changed（2026-09-20，用户拿游戏截图当场抓出来的事故）**：第一版只读了
`Response.profileRecords`，把 32 把已解锁的武器报成「未开始」、总数报成 149/183。
实测真因：**183 条模式记录里 151 条是档案级（`scope=0`）、32 条是角色级（`scope=1`）**，
角色级那批只在 `Response.characterRecords.<角色>.data.records` 里。三条独立证据：
① 那 32 条在 `characterRecords` 里三个角色全是 5/5（state 67）；
② 账号里对应着 33 件带 Crafted 标记的副本（已锻造 ⇒ 模式必然解锁）；
③ 用户截图里那些名字正好就是被误判的那一批。修正后本账号是 **181/183，进行中 2**。

## Decision

1. **模式进度只从组件 900 读，但两个作用域都要读**（`profileRecords` + `characterRecords`，
   同一记录号取进度最靠前的角色 —— 模式解锁是账号级的）。组件号登记在
   `services/profile_components.PATTERNS`；
   `900` **不并入 `FULL`**（并进去会让所有武器查询都多拉 1.44 MB / +2.5 s），
   读到的 183 行状态做一个 5 分钟 TTL 的进程内缓存，不缓存 1.44 MB 原文。
2. **组件没返回或整块为空时按失败处理**，绝不解释成"图样都没解锁"；
   "账号里没有这条记录"只能是**某一条**的状态（`未开始`，`progress: null`）。
3. **图样目录的判据是"记录名 == 某件 `inventory.recipeItemHash` 非空物品的名字（精确相等）"**，
   不认本地化类型字符串（中文「武器模式」/英文 `Weapon Pattern`），也不按 219 件口径算。
   实测该判据 183/183 命中、零歧义，并顺带把 141 条催化挡在门外（催化记录名带「催化」后缀，
   模糊匹配会误中同名武器）。
4. 变体（`（专家）/（失时）/（痛苦）`）不单列：单独问变体名时**指回基础版**，并把它的塑形配置
   一起给出去（可塑形栏位、三四号特性是否固定、有没有深视插槽）—— 这些都从 Manifest 现算，
   不写死一句话（上游改配置时答案跟着变）。

被否掉的选项：

- **组件 800 / 1300**：前者没有图样状态，后者没有进度（见上）。
- **扫全表按 `recordTypeName` 过滤**：中英名字不同（判据得写两份），而且要扫 6168 条记录定义。
- **把 219 件 `is_craftable` 当图样总数**：会把 36 件变体重复算进去，与游戏里那一页对不上。
- **图样进度落成本地生成物**：进度是账号状态，生成物只能省目录（目录建一次 511 ms、之后 2 ms），
  省不掉那 2.5 s 的账号读取，不值得多一份要维护的产物。

## Consequences

- 首次调用约 3 s（目录 511 ms + 组件 900 约 2.5 s），5 分钟内再次调用约 0.4 s；
  目录缓存只在进程内，Manifest 换了要重启才生效（与其他 Manifest 派生缓存一致）。
- 响应里**必然并存两个"可锻造"口径**：图样 183 条（图鉴）与 `is_craftable` 219 件（定义）。
  任何新入口都要说清用的是哪一个，别把 219 说成"图样数"。
- 来源来自 Starside「锻造武器来源」（社区资料，183 条里命中 172），
  必须带 `trust`/`updated_at`，且"清单里没有这一行"不能说成"这把武器没有来源"。
- **「未开始」只在一处两处都没有记录时才给**：档案级与角色级都查过才算"没有记录"。
  这条口径有回归测试（`tests/test_pattern_query.py` 的角色级三连），因为它是被真机抓出来的，
  不是设计出来的。
- 改这条决定要同时改：`pattern_service.py`、`starside_crafting_sources.py`、
  `tests/test_pattern_query.py`、`tests/test_crafting_sources.py`、
  `docs/plans/PATTERN_QUERY_PLAN.md`、`skills/destiny2-mcp/references/routing.md`。
