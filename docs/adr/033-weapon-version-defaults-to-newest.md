# ADR-033: 同名多版本武器默认取最新一版（判据 index）

- Status: accepted
- Date: 2026-10-10
- Decision By: maintainer
- Scope: `services/weapon_profile.py`（`find_weapon` / 新增 `weapon_versions`）、所有按名字查武器定义的出口

## Context

同一把枪每次复刻都是一个新的 `item_hash`。2026-10-10 实测：**2208 条武器定义 / 1318 个名字里，
580 个名字（44%）是多版本** —— 用户问「鹰月」的 perk 池时，工具给的是 index **23927** 的旧版，
而新版是 index **35200**（老版本在游戏里**已经刷不到**）。

根因两处，都实测确认：

1. `find_weapon()` 取 `manifest.search(...)` 结果里的**第一条**，而 search 只按**匹配分**排序 ——
   同名多版本里挑中哪一版**纯属偶然**；
2. 名字索引是**中英双语**的：`search("Judgment")` 返回的条目本地化名是「审判 / 公平审判 /
   旅行者的审判5…」——只比 `displayProperties.name` 会让"精确同名"全部落空，于是退回全部结果，
   "最新"落到**另一把枪**（公平审判 index 35552）上。

判据用 **`index`**（Manifest 定义的自增序号，越大越新）：实测鹰月 23927 vs 35200、
Judgment 六版 35186 / 33474 / 33466 / 32887 / 28330 / 23837，与赛季顺序一致；
`versionNumber` 字段**不存在**（先猜过，已否）。

## Decision

1. **默认取最新**：`find_weapon()` 按 `index` 降序取第一版 —— 一处修，`perk_pool` / `info` /
   `stats` / `god_roll` 全部受益。
2. **`weapon_versions()` 是版本列表的唯一出处**：收 `search` 的全部条目，按 `index` 降序，
   带 `item_hash` / `index` / `name` / `definition`。
3. **精确同名要认中英两种名字**：`displayProperties.name` 或 `get_english_name(hash)` 任一命中
   才算同名；精确同名一条都没有时才退回搜索顺序（半截名字仍可用）。
4. 显示名走**本地化表**（中文），取不到就退回原名，**不编**。

被否掉的方案：

- **保持取第一条**：44% 的名字会随机给到某一版，其中多数是刷不到的旧版。
- **A2：不猜，列出版本要求指定 hash**：最诚实，但对 44% 的常见查询平白多一个来回；
  用户 2026-10-10 明确选了 A1（默认最新 + 回执列版本）。
- **用 hash 大小当"新"**：Destiny 的 hash 是随机值，与赛季无关，实测反例（Judgment 六版
  hash 正负交错）已排除。
- **新建扫描/sqlite 查询路由**：不需要 —— `search` **本来就返回全部版本**（名字索引是
  `名字 → 多条`），只是旧代码取了一条。

## Consequences

- 同一句「鹰月 perk 池」现在给新版：13 个插槽、特性 1/2 各 12 条（含强化版）、特性 3「宝藏现世」，
  社区建议 `萤火虫 / 巨脉蜻蜓 / 墓碑 / 结晶残花 / 混沌重塑` **5/5 命中**（改前 4 颗是
  `variant_pool_ambiguous`）。
- 版本数收敛并**与直读 sqlite 交叉核对一致**：Judgment 13 → **6**、A Sudden Death 8 → **6**、
  鹰月 2、Zephyr 6、遗产/玉兔 1。
- **未做**：`item_hash` 参数（想指定旧版/某一版时要能精确要）；着色器/模组/大师杰作三栏的
  "要全"出口（现在带 `options_truncated: true`，如实标但拿不全）。
- 守门：`tests/test_weapon_versions.py`（最新版优先 / 新→旧 + 精确同名 / 找不到报错），
  注入 67（把排序改回升序必红）。
