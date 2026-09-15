# 兼容面规矩（别名与历史工具）

这个文件回答一个问题：**已经存在但"看起来多余"的入口，哪些必须留、哪些可以删、删之前要先做什么。**
写法是「实测 + 决定」，不是「感觉」：下面每一组别名都在本机真账号上跑过——同一组参数下
两两返回**逐字节相同**的 `data`（脚本见 `scripts/run_corpus_all_rows.py` 的同类做法，
`tests/test_intent_aliases.py` 负责防止以后偷偷跑偏）。

## 三类，三种待遇

| 类别 | 例子 | 待遇 |
| --- | --- | --- |
| **永久别名**（中文说法） | `概况` = `summary`、`重复武器` = `duplicates`、`角色`/`档案` = `profile` | **不许删**。中文用户会这么说，删了等于砍功能；它们必须永远和 canonical 走同一段代码 |
| **待删别名**（英文近义） | `search_catalog`/`all_weapons`/`global`/`search_all` = `catalog`；`selection_rates`/`perk_selection`/`selection`/`usage_rates` = `popularity` | **保留到 0.2.0**。现在只登记不宣传；`skills/destiny2-mcp/references/routing.md` 只写 canonical。删之前先看一圈真实调用日志 |
| **历史工具面**（69 个旧工具） | `get_inventory`、`search_items` … | 只在 `DESTINY_MCP_TOOL_PROFILE=full`（或 `expert`）**且** `DESTINY_MCP_ENABLE_LEGACY_TOOLS=1` 时暴露；不进主路径文档、不保证契约、不单独修 bug |

## 别名总表（实测等价）

| 工具 | canonical | 别名 |
| --- | --- | --- |
| `player_assistant` | `profile` | `get_profile`、`角色`、`档案` |
| | `search` | `search_player` |
| | `find` | `find_players`、`fuzzy` |
| `inventory_assistant` | `summary` | `summarize`、`概况` |
| | `duplicates` | `duplicate_weapons`、`find_duplicates`、`重复武器` |
| | `get` | `inventory`、`list` |
| | `search` | `find_item` |
| | `type` | `search_type` |
| | `equip_many` | `equip_items` |
| | `track_quest` | `quest_tracking` |
| `weapon_assistant` | `catalog` | `search_catalog`、`all_weapons`、`global`、`search_all` |
| | `compare` | `compare_duplicates` |
| | `perk_pool` | `perks` |
| | `popularity` | `selection_rates`、`perk_selection`、`selection`、`usage_rates` |
| `loadout_assistant` | `list` | `get` |
| `subclass_assistant` | `get` | `subclass` |
| `activity_assistant` | `stats` | `career`、`historical_stats` |
| | `weapon_history` | `weapons`、`weapon_usage`、`weapon_leaderboard` |
| | `aggregate` | `activity_aggregate`、`activity_stats` |
| | `leaderboards` | `leaderboard` |
| `build_assistant` | `community` | `starside` |

## 三个"看着像别名、其实不是"的坑

- **`move` ≠ `transfer`**：都在 intent 列表里，但参数名不同——`move` 读 `destination`（vault 或角色名，可配 `equip=true`），`transfer` 读 `to_character`（按实例转移）。拿 `destination` 调 `transfer` 会得到 `invalid_arguments`。
- **`community_build` ≠ `community`**：只在**没给** `community_build_id` 时两者返回相同（都走搜索）；给了 id 之后 `community_build` 会读整套模板并核对库存，这是另一件事。
- **`equip` / `equip_mod` / `item` / `lock` / `pull_postmaster`** 各自是独立行为，不是任何东西的别名。

独立行为（不是任何东西的别名）也逐条登记在 `tests/test_intent_aliases.py` 的 `STANDALONE` 里：
`pgcr`、`filter_rolls`、`save`、`modify`、`vendor`、`weekly` 这类各有各的分派，新增 intent 时
要么进别名表、要么进 `STANDALONE`，两个都不进测试就红。

## 加新入口的规矩

1. **别名不许有自己的分支代码**：必须落进 canonical 的那段 handler；`tests/test_intent_aliases.py` 会检查每个别名组在同一处分派里。
2. **新别名要登记在本文件**，并写清它是"永久（中文说法）"还是"待删（英文近义）"。
3. **canonical 只写在 `routing.md` 与工具说明里**；别名出现在 schema 的 enum 里就够了，不要在文档里教用户用别名。
4. **删别名前**：先把它从 `_requests.py` 的 Literal 里去掉、跑一遍 `pytest` 与
   `scripts/run_corpus_all_rows.py --group sweep`（110 个取值逐个真机跑），确认没有测试或语料还在用它。
