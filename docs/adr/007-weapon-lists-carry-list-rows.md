# ADR-007: 「我有哪些武器」只给列表行，插槽明细走单件查询

- Status: accepted
- Date: 2026-09-20
- Decision By: maintainer
- Scope: `weapon_assistant(intent="type")` 的载荷、`services/weapon_payload.py` 的 `LIST_ROW_KEYS`、
  `services/weapon_detail_service.py` 的 `list_view`

## Context

`weapon_assistant(intent="type")`（"我手炮都有哪些"）回答的是**列表问题**，但它给的是**完整模板**：
每件 `{weapon, sockets, options, stats, perks_complete, notes}`。真机实测（同一账号）：

- 默认 20 件 = **184 KB**（解码后紧凑 JSON；线上文本 38.8 万字符），其中 **2/3** 是 `sockets`（定义级插槽池）
  与 `options`（这一件能换成什么）；
- 为了这两块，每次还要多取两个组件（305 已装 plug、310 可换项），profile 从 **1.86 MB 涨到 10.16 MB**
  （实测 0.63s → 3.79s）；
- 命中 124 件时，服务把**全部 124 件**的插槽池都造出来，再截断到 20 件。

同时仓库已经有一条既有规矩（`services/weapon_payload.py` 开头第 2 条）："列表类用精简身份块，
完整模板只给单把武器或对比"。`catalog`/`filter_rolls` 的 `matched[]` 早就是精简行，**只有 `type` 例外**。

## Decision

`type` 改成**列表行**（`weapon_payload.list_row` = `LIST_ROW_KEYS` + 副本字段 + `stats` + `notes`）：

- **给**：身份字段（名字/中英名/稀有度/类型/框架/射速/可否锻造/图标）、挑枪要用的五项
  （`damage_type`/`ammo_type`/`gear_tier`/`item_level`/`roll_summary`）、副本字段
  （`instance_id`/`location`/`power`/`is_equipped`/`locked`/`tracked`，**摊平**在行首，与 `matched[]` 同一写法）、
  `stats`、逐条 `notes`；
- **不给**：`sockets`、`options`、`perks_complete`，以及逐把的本地资料四块（`farming`/`popularity`/
  `community`/`sources`）——整张列表的清单仍在顶层 `farming_list`，单把的结论用 `info`/`analyze`；
- **要明细的入口是 `weapon_assistant(intent="compare", weapon_name=…, item_instance_id=…)`**：
  一次调用拿这一件的插槽池与可换项（列表行里有名字与实例 ID，够拼出这次调用）。
  不接受"再加一个 `instance` intent"——`compare` 已经是按副本给完整模板的那个口子；
- 默认条数 20 → **10**，并补 `offset`/`next_offset` 翻页（`truncated` 表示"后面还有"，
  最后一页必须是 `false`）；
- 服务层用 `list_view=True` 表示"这次是列表行"：**不请求 305/310、不构造 sockets/options**，
  并且不写"没有可换部件数据（310 未返回）"那条说明 —— 没读的东西不许说成没有；
- 顺带把"先给全部命中件造明细、再截断"改成**排序 → 切页 → 只给这一页造**（顺序不变，输出不变）。

## Consequences

- 列表回答从 **184 KB → 23.2 KB**（同样 20 件是 44.7 KB），线上文本 387,598 → 41,537 字符；
  热调用 0.70s → 0.31s（另外那 0.6s 是 `list_weapon_catalog` 的全量索引遍历，已单独记忆化）。
- **破坏性**：`weapons.items[].weapon.*` 的路径全部改成行首字段（`items[].name`、`items[].gear_tier`…），
  旧键一个不留（不双写）。`skills/destiny2-mcp/references/routing.md`、`docs/testing/TESTING_CORPUS*.md`、
  武器语料脚本、`tests/test_weapon_keys_snapshot.py` 与武器基线都跟着改了，登记见 `docs/COMPATIBILITY.md`。
- `get_weapon_details_by_type` 的 `list_view` 与 `include_selectable_plugs` **互斥**（后者要把可换项挂在插槽上）：
  同时传会 `ValueError`，而不是静默返回"没有可选 perk"让社区配装核对得出错误结论。
- 以后想往列表行加字段，先回答"少了它，'我有哪些、哪件值得看'还答得出来吗"，
  并同时改 `LIST_ROW_KEYS`、键快照测试与基线 —— 行内不许再出现整块插槽数据。
