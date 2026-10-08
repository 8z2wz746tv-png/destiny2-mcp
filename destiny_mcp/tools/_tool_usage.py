"""八个工具的**能力清单与用法**（模型一定会读的那一份）。

为什么单独一个模块、并用 `description=` 挂到工具上：**工具描述是唯一保证在模型上下文里的东西**
（skill 可能没被加载，MCP 只保证 advertise 一个 URL）。2026-10-06 盘点发现：
130 个 intent 里 **94 个在工具 docstring 里连名字都没有** —— 模型只看到 schema 里那串枚举名，
不知道每个是干什么的，于是"工具功能用不全"。

**每条一行的写法**（由 `tests/test_tool_usage_docs.py` 守门）：
- 本工具**每一个 intent 都必须出现**（缺一个就红 —— 能力不许只存在于 skill 里）；
- 每行都要有**用途**（不是只列名字）；
- 顺手写清"该换哪个 intent"：模型最常犯的错不是找不到工具，是**用错入口**
  （例：问"这把枪是 T 几"要读账号，不该用不读账号的 `perk_pool`）。

深层口径与踩坑仍写在 skill（`references/routing.md`）—— 这里只保证**能力与用法可发现**。
"""

from __future__ import annotations

TOOL_USAGE: dict[str, str] = {
    "player_assistant": """玩家/账号聚合入口：搜索玩家、模糊找人、读取角色档案。

- `profile`（别名 `get_profile`、`角色`、`档案`）：**我的角色列表与档案**（光等、职业、最近游玩、凯旋分），默认入口。
- `search`（别名 `search_player`）：按**完整 BungieName**（`名字#1234`）精确搜人，要拿 `membership_id` 时用。
- `find`（别名 `find_players`、`fuzzy`）：名字**记不全**时模糊搜候选；`include_profile=true` 才顺带读档案（慢约 5 秒）。
下一步：拿到角色后看装备用 `inventory_assistant`；看战绩用 `activity_assistant`。""",
    "inventory_assistant": """背包/仓库聚合入口：读写清单、按名/按类型查、找重复武器、换模组。

读（不传 `confirmed`）：
- `summary`（别名 `summarize`、`概况`）：数量概况（每类多少件、仓库占多少）。
- `get`（别名 `inventory`、`list`）：列清单；`location` 选 `vault`/角色名（**问"我身上穿着什么"就用角色名** —— 已装备的件在里面，带 `is_equipped`）；`item_type` **只认 weapon/armor/all**（按「手炮」这类具体类型列请用 `intent="type"` + `type_name`）。
- `search`（别名 `find_item`）：**按名字找东西**（`item_name`）。
- `type`（别名 `search_type`）：按**类型**列武器（`type_name`，如"手炮"）—— 按名字请用 `search`。
- `duplicates`（别名 `duplicate_weapons`、`find_duplicates`、`重复武器`）：**重复武器分组**（要不要分解/留哪把）。
- `item`：某件护甲的完整载荷（插槽、能量、三层属性）—— 换模组前先读它拿 `socket_index`。
- `mods`：某位角色**已装备护甲**上的模组（可只读一位角色）。
写（**必须 `confirmed=true`**，服务端先给候选）：
- `move`（别名 `transfer`，目标 `destination`=vault 或角色名；`equip=true` 顺带装上）：搬运。
- `equip`：装备一件（会给"先顶下、再装目标"的两步计划，信封里有 `replaces` 说明会顶掉谁）。
- `equip_many`（别名 `equip_items`）：批量装备（传 `item_instance_ids`）。
- `equip_mod`：换护甲模组；**三格全满时用 `socket_index` 指名换哪一格**。
- `pull_postmaster`：从邮政官取回。`lock`：锁定/解锁。`track_quest`（别名 `quest_tracking`）：追踪任务。
下一步：要装一整套配装用 `build_assistant(intent="equip_build")`（一次转移+装备+模组）。""",
    "weapon_assistant": """武器聚合入口：分析、副本对比、Perk 池、选取率、全量候选、锻造图样。

**先分清"读账号"与"读全量"**：`analyze`/`compare`/`filter_rolls`/`patterns` 读**你的账号**；
`catalog`/`perk_pool`/`god_roll`/`popularity` 读 **Manifest 与社区数据**（不看你有没有）。
- `analyze`：这把武器**你的副本**的完整信息（含 `gear_tier`、六维、perk）—— 问"我这把怎么样"用它。
- `filter_rolls`：**账号内按当前插槽筛**（要 `required_perks` 之类）—— 问"我有没有带某个 perk 的"用它。
- `catalog`（别名 `search_catalog`、`all_weapons`、`global`、`search_all`）：从**全量 Manifest** 按类型+perk 找武器。
- `perk_pool`（别名 `perks`）：这把枪**可能 roll 到**的 perk 池。
- `god_roll`：社区**推荐 roll**。`popularity`（别名 `selection_rates`、`perk_selection`、`selection`、`usage_rates`）：perk 选取率与热门组合。
- `compare`（别名 `compare_duplicates`）：**多个副本次对**（传 `item_instance_id`）—— 决定留哪把用 `inventory_assistant(intent="duplicates")` 或它。
- `type`：按**武器类型**列（`weapon_type`）。`info`：单把武器的定义级信息。
- `stats`：武器数值。`perk_description`（别名 `perk`）：**某个 perk 的效果文本**（传 `perk_name`，不是武器名）。
- `catalyst`：催化。`patterns`（别名 `pattern`、`craft`、`锻造`、`图样`、`图样进度`、`模式进度`、`红框`、`红框进度`）：**锻造图样进度**（游戏里"4/5"那条，读账号组件 900）。
- `community`：本地社区武器/perk/DPS 资料（不可信参考，带来源与更新时间）。
下一步：决定装备哪把用 `inventory_assistant(intent="equip")`；问该不该刷用 `popularity`/`community`。""",
    "build_assistant": """配装聚合入口：推荐、查候选、失败诊断、照抄社区模板、确认后装备。

- `recommend`（默认）：按数值目标推荐**候选行**（含 `execution_id`）。
- `find`：**按硬约束找一套能装的**（指定金装/套装/属性下限）；社区模板给的参数直接照传。
  ⚠️ **组合规模只由五个部位的候选件数决定，与你给什么目标无关** —— 不钉金装时常常超上限，`find`/`recommend` 会直接回「没算（这不是配不出来）」并给收窄选项。**指定一件金装是最有效的收窄**（下界目标救不了超限）。「尽量高」写 `priority_stats`，硬下限才写 `*_target`。
- `analyze`：**失败诊断** —— 说清"差多少 / 是哪条前提卡的"，目标达不到时看它。
- `farm_target`：反推**该去刷哪一件**（`baseline`、`max_replacements`）。
- `equip_build`：**装备**（先 `confirmed=false` 拿预览，同意后 `confirmed=true`；传 `execution_id`）。
  **目标格满时会自动腾出一件**放仓库（穿着的/锁定的/本次要装的/官方配装在用的都不动），腾走的件记在 `steps` 的 `make_room` 里 —— **别让玩家自己去游戏里腾**；腾不出来会如实拒绝。
- `armor_mods`：**护甲模组有哪些**（换模组用 `inventory_assistant(intent="equip_mod")`）。
- `exotic_armor`：金装护甲候选。`set_bonus`：套装的 2/4 件效果。
- `community`（别名 `starside`）：搜本地社区配装（返回 `build_id`）。
- `community_build`：读**完整模板 + 账号比对**；出口的 `next_actions` 会告诉你**照 `solver_handoff.arguments` 跑 `find`**（社区模板本身不是可执行凭据）。
下一步：`find`/`recommend` 之后拿 `execution_id` 走 `equip_build`；装完可 `loadout_assistant(intent="snapshot_official")` 存进游戏内槽。""",
    "loadout_assistant": """账号配装：读已存的、存/穿/删、以及**游戏内官方配装槽**。

- `list`：**全部已存配装**（官方槽 + 本地），每行给 `loadout_id`。
- `get`：某一套的**逐件装备/模组/子职业**（要 `loadout_id`）。
- `save`：把**当前装备**存成一套（要 `name`）。
- `delete`：删一套（要 `loadout_id`）。
- `equip_loadout`：**穿上**已存的一套（要 `loadout_id` + `confirmed=true`）。
- `search_identifiers`：官方槽可用的**名称/图标/颜色** hash（Bungie 只让存 hash，所以槽位显示成"配装 N"）。
- `snapshot_official`：把**当前装备**存进游戏内官方槽（`slot_number` 1–20；**覆盖前先 `list` 看清那一槽是什么**）。
- `update_official_identifiers`：改官方槽的名称/图标/颜色（三个 hash 都要给）。
- `clear_official`：清空一个官方槽。
下一步：想把社区/求解出来的套装存进游戏内槽 —— 先 `build_assistant(intent="equip_build")` 装上，再 `snapshot_official`。""",
    "subclass_assistant": """子职业与赛季神器：读配置、查选项、确认后修改。

- `get`（别名 `subclass`）：**当前子职业配置**（超能/近战/手雷/星象/碎片）。
- `options`：某元素某类别的**可选清单**（要 `element` + `component`：`super`/`melee`/`grenade`/`aspect`/`movement`/`class_ability`）。
- `fragments`：某元素的**碎片列表**（碎片不是 `component`，别传给 `options`）。
- `fragment_details`：**某个碎片的效果**（传 `fragment_name`）。
- `artifact`：赛季神器（可给 `character` 看身上那件与背包里能换的）。`artifact_mod`：某颗神器模组的详情（要 `artifact_mod_hash`）。
- `modify`：**改配置**（`changes` 形如 `{"super": "金色枪"}` 或 `{"subclass": "烈日"}`，要 `confirmed=true`）。
- `equip_artifact_mod`：装神器模组。`equip_artifact`：换神器（`artifact_name`）。
- `community`：本地社区技能资料（搜索或按 `knowledge_id` 读详情；资料不代表账号已解锁）。
下一步：配装要求某元素/碎片时先 `modify` 再 `build_assistant(intent="equip_build")`。""",
    "activity_assistant": """活动/战绩聚合入口：历史、单场结算、生涯统计、游戏内计数器、武器使用、排行榜。

**统计口径三档别混**：`stats` 是**统计接口**（只有 Daily/AllTime，**没有赛季**）；
`counters` 是**游戏内计数器**（组件 1100，**有赛季**）；`history` 只给最近 N 场。
- `history`：最近 N 场活动（默认 20，`mode` 筛模式）—— "最近打得怎么样"用它。
- `pgcr`：**单场结算**（要 `activity_id`，从 `history` 里拿）。
- `stats`（别名 `career`、`historical_stats`、`activity_stats`）：生涯 PvE/PvP 统计（`period` 只能给 career）。
- `counters`：游戏内计数器（`query` 按名筛，`period=season/act` 只有它支持）。
- `raid_report`：**突袭/地牢报表**（完成数/导师/无瑕/全程，`mode` 选 raid/dungeon）。
- `raid_scan`：逐场索引（报表里"全程/最短用时"的来源；按副本分块续扫）。
- `weapon_history`（别名 `weapons`、`weapon_usage`、`weapon_leaderboard`）：武器使用排行（全模式）。
- `pvp_weapons`：**纯 PvP 武器榜**（最近 N 场结算聚合，不是生涯；`mode` 选 pvp 家族，gambit 要单说）。
- `aggregate`（别名 `activity_aggregate`）：活动累计排行。
- `leaderboards`（别名 `leaderboard`）：玩家排行榜（`statid`）；`clan_leaderboards` 要 `group_id`。
- `community`：本地社区活动/副本资料。
下一步：想看某场细节用 `pgcr`；想对比赛季用 `counters`（`stats` 给不了赛季）。""",
    "world_assistant": """世界/周常聚合入口：本周、商人、轮换、收藏品、社区机制资料。

- `weekly`：本周概要。`weekly_full`：完整周常清单。
- `rotations`（别名 `轮换`、`周常轮换`、`这周`）：**本周轮换表**（特色突袭/地牢、夜幕/宗师词缀与掉落、上维挑战、异域任务、泉源）。
- `vendor`：**商人货架**（`vendor_name` 留空先列有哪些商人；给名字/别名/hash 直接看货）。
- `search_collectible_nodes`：按关键词**搜收藏品节点**（先拿 `collectible_node_hash`）。
- `collectible_node`：某个**展示节点**的解锁状态（要 `collectible_node_hash`）。
- `collectible_item`：**某件物品**的收藏状态（按 `item_name`）—— 查"我解锁了没"用它。
- `community`：本地社区资料（**唯一能跨分类搜的入口**：`community_category` 选 builds/weapons/armor/subclass/activities/mechanics/sources/other）。
下一步：要开某活动的图用 `rotations`；要查某件东西解锁没解锁用 `collectible_item`；机制问题用 `community`。""",
}
