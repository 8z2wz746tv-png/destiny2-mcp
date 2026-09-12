# 武器格式统一 · 开发计划

状态：**待确认**（确认后按第 3 节分阶段实施）
范围：`weapon_assistant` 的全部只读 intent + 依赖的 6 个服务
不动：写入链路、`build_assistant`、商人、求解器

---

## 1. 现状盘点（都是实测/读码证据）

### 1.1 一把武器，十处形状

| # | 位置 | 产出 | 关键字段 | 实测大小 |
| --- | --- | --- | --- | --- |
| 1 | `manifest_query_service.get_weapon_full_info` | `data.weapon`（info / analyze） | `name`/`nameEn`/`weaponType`/`tier`/`damageType`/`ammoType`/`intrinsicPerks`/`stats`/`catalysts` | 6.6 KB |
| 2 | `manifest_query_service.get_weapon_stats` | `data.stats`（stats） | 只有 `name`/`nameEn`/`weaponType`/`stats`/`icon_url` —— **没有稀有度、没有 item_hash** | 0.4 KB |
| 3 | `perk_service.get_weapon_perks` | `WeaponPerkPool`（perk_pool / analyze） | `weapon_name`/`weapon_type`/`item_hash`/`slots[].slot_name`（英文：barrel/sight/…）+ `plugs[]` | 8–32 KB |
| 4 | `perk_service.get_god_roll` | `data.god_roll`：**一整段文字** | 无结构；`analyze` 里复用同一段文字 | 0.2–0.9 KB |
| 5 | `weapon_roll_filter_service._compact_catalog_weapon` | `catalog.matched[]` | `name`/`nameEn`/`item_hash`/`weapon_type`/`tier`/`damage_type`/`ammo_type`/`matched_perks`/`owned=false` | 每项 ~0.5 KB |
| 6 | `weapon_roll_filter_service._compact_weapon` | `filter_rolls.matched[]` | `name`/`instance_id`/`item_hash`/`weapon_type`/`location`/`power`/`perks[]`（**字符串数组**）/`reason` —— 没有稀有度、没有 damage/ammo | 每项 ~0.2 KB |
| 7 | `weapon_detail_service` → `WeaponDetail`（type） | `weapons.weapons[]` | **实例级**：`instance_id`/`power`/`location`/`sockets[].slot_label`（中文：框架/枪管/…）+ `stats`/`perks_complete` | 3.6 KB/把 |
| 8 | `weapon_compare_service` → `WeaponComparisonInstance` | `comparison.instances[]` | `instance_id`/`location`/`power`/`perks[]`/`god_roll_score` | — |
| 9 | `weapon_popularity_service._weapon_identity` | `popularity.weapon` | `name`/`name_en`/`item_hash`/`icon_url`/`weapon_type`/`version_label`/`status`/`release_label`/`episode` | — |
| 10 | `inventory_service.search_items_by_type` → `InventoryItem`（inventory 的 type） | `result.items[]` | `name`/`power`/`bucket_type`/`location`/`stats` —— **没有稀有度、没有 perk、没有 roll 信息** | 每项 ~0.3 KB |

外加 `manifest_search` 的索引条目（`itemTypeNameDisplay`/`tier` 整数）是它们的共同上游。

### 1.2 三个结构性问题

**① 稀有度表抄了三份，其中三份不全**

```python
weapon_roll_filter_service.py:285   {5:"传说", 6:"异域"}    # 蓝绿白 → ""
weapon_detail_service.py:276        {5:"传说", 6:"异域"}    # 同上
vendor_service.py:432               {5:"传说", 6:"异域"}    # 同上
manifest_query_service.py:35        {2:普通,3:罕见,4:稀有,5:传说,6:异域}   # 只有这份全
```
Manifest 全量 2208 把武器：普通 18、罕见 37、稀有 94、传说 1913、异域 146 —— **蓝绿白一共 149 把**，现在它们在 `type`/`catalog`/`filter_rolls` 里稀有度是空字符串。

**② "固定 perk / 随机 roll" 这个维度在源头就丢了**

`perk_service` 取池时写的是 `socket.get("randomizedPlugSetHash") or socket.get("reusablePlugSetHash")` —— 两种池合并处理，后面的代码再也分不出"这条是固定 perk 还是随机池"。

实测 socket 结构（决定语义）：

| 武器 | 稀有度 | 可选池 | 随机池 | 可锻造 | 正确语义 |
| --- | --- | --- | --- | --- | --- |
| 泰拉巴 | 异域 | 8 | 0 | 否 | **固定 perk**（部件可选），不存在"推荐 roll" |
| 牵引器火炮 | 异域 | 8 | 0 | 否 | 固定 perk |
| 枯骨鳞片 | 异域 | 6 | 4 | **是** | 随机 roll（和紫武器一样） |
| 遗产 | 传说 | 11 | 4 | 否 | 随机 roll |
| 刚愎自用 | 普通 | 2 | 0 | 否 | 固定 perk |
| Ψ卷云II | 稀有 | 5 + 固定件 1 | 0 | 否 | 固定 perk |

→ 判据不能只看稀有度：**有随机池 = 随机 roll；没有 = 固定 perk**。可锻造金武器有配方（type 30），属于随机 roll。

**③ 同一个概念两套字段名 / 两套命名风格**

- perk 栏位：manifest 侧叫 `slot_name`（英文 barrel/sight/magazine/perk_1），实例侧叫 `slot_label`（中文 框架/枪管/特性1）；
- 拼写：`manifest_query_service` 用 camelCase（`nameEn`/`weaponType`/`damageType`/`ammoType`），其余模型用 snake_case；
- `filter_rolls` 的 `perks` 是字符串数组，`compare` 的 `perks` 是 `PerkInfo` 数组，`type` 的 `sockets` 又是第三种。

### 1.3 由此产生的错误回答（已实测）

| 问法 | 现在 | 应该是 |
| --- | --- | --- |
| 泰拉巴的推荐 roll | 只有标题的空壳（刚修成"本地这条记录不完整"） | 固定 perk 武器 → 列 Manifest 里的固定 perk，说明没有"推荐 roll" |
| 泰拉巴的选取率 | 「暂无录入的选取率快照」 | 固定 roll 武器 → 选取率无意义 |
| 遗产的选取率 | 同上（遗产是真·随机武器，答案合理） | 保持 |
| 蓝武器的稀有度 | 空字符串 | 稀有 |
| 我的微冲（inventory type） | 没稀有度、没 perk | 身份块 + roll_kind |
| 问"速射框架的脉冲有哪些" / "这把枪什么框架" | `itemTypeDisplayName` 只有"脉冲步枪"，没有框架字段；`info.intrinsicPerks` 里混着框架与异域特性 | 身份块给 `frame`（异域为 null 时用 `intrinsic` 说明） |

---

### 1.4 对照 DIM 的做法（已读源码）与我们缺的东西

DIM（`src/app/inventory/store/sockets.ts`、`src/app/utils/perk-utils.ts`）的模型：

```ts
DimSocket { plug, reusablePlugs: DimPlugSet, ... }
DimPlugSet { plugs, plugHashesThatCanRoll, plugHashesThatCannotRoll, craftingData, ... }
DimPlug    { plugDef, cannotCurrentlyRoll, enabled, ... }
// 强化 perk：data/d2/trait-to-enhanced-trait.json（基础 hash → 强化 hash）+ 反转映射
isEnhancedPerkHash(h) / unenhancedVersion(h) / enhancedVersion(h)
```

四条可以直接照搬：

1. **一个 socket = 已装 plug + 这一件副本能换的 plug 集合**。DIM 读的是 `itemComponents.reusablePlugs.data[instanceId].plugs`（组件 **310**）—— **我们现在根本没请求 310**，所以只能拿定义级的池冒充"可能 perk"。
2. **`currentlyCanRoll`**：plug set 条目自带的"这个 perk 现在还能不能滚出"。我们现在把退役 perk 也当"可能 perk"列出来，直接影响"值不值得刷"的判断。
3. **强化 perk 是独立条目**，靠生成的配对表转换；实测我们 Manifest 里的判据是 **同 plug 类别 + 同名字 + `inventory.tierType` 2（基础）/ 3（强化）**，共 **272 组**配对，强化版描述都是"持续时间延长／效果增强／数量增多"。
4. **框架从固有 perk 取**；异域没有定义框架的固有 perk（社区与 DIM issue #4056 都确认），只能退回 RPM —— 与我们 2.2 定的规则一致。

### 1.5 定义级 vs 实例级（关键：现在混在一起了）

| 层级 | 数据来源 | 例子 |
| --- | --- | --- |
| **定义级** | Manifest `socketEntries` + `DestinyPlugSetDefinition` | 遗产的特性栏定义池 **19 个** perk；哪些退役（`currentlyCanRoll=false`）；强化配对 |
| **实例级** | 账号组件 305（已装）+ **310（能换）** + 300（`gearTier`） | 这一件 T5 遗产的特性栏**实际只有 3 个**；已装的是哪个 |

实测 `gearTier → 特性栏可滚数量`（账号里 1425 件有 reusablePlugs 的武器）：

| gearTier | 实例数 | 最常见的「随机栏可选数」组合 | 结论 |
| --- | --- | --- | --- |
| T5 | 343 | (3,3,2,2) × 274 | **两个特性栏各 3 个** |
| T4 | 17 | (2,2,2,2) × 10 | 各 2 个 |
| T3 | 10 | (2,2,2,2) × 6 | 各 2 个 |
| T2 | 8 | (1,1,1,1) × 5 | 各 1 个 |
| T0（老武器，无级） | 521 | (2,2,1,1) × 215、(1,1,1,1) × 123 | 无统一规律，**必须按实例报** |

同一把武器确实会以不同 T 级存在（742 个武器 hash 里有 **28 个**同时有多个 T 级的副本），所以"这把枪能滚几个"不能只看定义。

## 2. 设计

### 2.1 两个共用块

**身份块（所有 intent 都用同一份）**

```jsonc
"weapon": {
  "item_hash": 0,
  "name": "遗产",
  "name_en": "Heritage",
  "rarity": "传说",            // 统一稀有度表，5 档齐全
  "rarity_tier": 5,            // 6 异域 / 5 传说 / 4 稀有 / 3 罕见 / 2 普通
  "weapon_type": "霰弹枪",
  "frame": "精确重击框架",      // 框架（archetype）；异域常为 null，见 2.2
  "intrinsic": "精确重击框架",  // 固有槽原文：传说=框架名，异域=专属特性名（如「贪食野兽」）
  "rpm": 30,                   // 射速，与 stats.rpm 同源（一处计算、两处投影）
  "ammo_type": "威能",
  "damage_type": "动能",
  "is_craftable": false,       // 有 type 30 配方
  "roll_kind": "random",       // random | fixed
  "socket_kinds": {"fixed": 1, "option": 11, "random": 4},
  "gear_tier": 5,              // T 级：只在实例上（300 组件）；未读账号时 null 并说明
  "description": "…",
  "icon_url": "…",
  "owned": {"status": "checked", "count": 2,
            "instances": [{"instance_id": "…", "location": "仓库", "power": 1900, "is_equipped": false}]}
            // 没读账号时：{"status": "not_checked"}
}
```

**perk 块（统一栏位命名与结构）**

```jsonc
"perks": {
  "source": "manifest+wishlist",      // manifest | manifest+wishlist
  "slots": [
    {"slot": "枪管", "kind": "random", "option_count": 3, "plugs": [
        {"plug_hash": 1, "name": "箭头制退器", "description": "…",
         "enhanced": false, "enhanced_plug_hash": 0, "can_roll": true,
         "god_roll_pve": false, "god_roll_pvp": true, "icon_url": "…"}]},
    {"slot": "框架", "kind": "fixed",   "plugs": [{...}]},
    {"slot": "特性1", "kind": "option", "plugs": [{...}]}
  ]
}
```

- `kind`：`fixed` = socket 只有 `singleInitialItemHash`；`option` = `reusablePlugSetHash`（固定但可换）；`random` = `randomizedPlugSetHash`。
- `plugs` 的口径由 `perks.scope` 标明：`"definition"`（Manifest 完整池，含退役条目与强化配对）或 `"instance"`（这一件副本在组件 310 里能换的，`option_count` 就是 T 级决定的那个数）。
- `can_roll` 来自 plug set 条目的 `currentlyCanRoll`；`enhanced` / `enhanced_plug_hash` 来自 tierType 2/3 配对表。
- `slot` 统一用中文标签（和实例侧一致），英文原类别放 `plug_category` 字段备查。

### 2.2 稀有度、框架与 roll_kind 规则（单一实现）

**框架（frame / archetype）**：玩家描述武器时最先说的就是它（"速射框架的脉冲"），但 Manifest 里没有独立字段：

- `itemTypeDisplayName` **不含**框架（遗产 = "霰弹枪"、泰拉巴 = "微型冲锋枪"）；
- 框架在**固有槽**（`socketCategoryHash = SOCKET_CAT_INTRINSIC`）里，plug 类别 `intrinsics`；
- 传说武器：固有槽 = 框架名（精确重击框架 / 攻击型框架 / 速射框架）；实测抽样 400 把中 218 把如此；
- 异域武器：固有槽放的是**专属特性**（贪食野兽 / 尖啸群击 / 冲击波），抽样 146 把里 145 把不是框架名；
- 另有 36 把传说武器的固有槽也不是框架名（老武器，如 M-17"快嘴" 的"平衡热量武器"、"散射"）。

→ 规则：`frame` = 固有槽 plug 名，**当它以「框架」结尾时**取用，否则 `null`；`intrinsic` 永远给原文，所以信息不丢。异域武器的回答话术用 `intrinsic`（"异域专属特性：贪食野兽"），不要硬套"框架"。

**角色数值**：`rpm` 取自 `investmentStats`（statTypeHash 4284893193），与 `stats.rpm` 同源；个别武器为 0（如可锻造异域按配方给数值），此时留 0 并照实说。

```python
RARITY = {6: "异域", 5: "传说", 4: "稀有", 3: "罕见", 2: "普通"}     # 唯一一份
def rarity_of(tier) -> str                                          # 未知档给 "未知(N)"
def frame_of(intrinsic_name) -> str | None                          # 以「框架」结尾才取
def roll_kind(socket_entries) -> str                                # 有任何 randomizedPlugSetHash → random
def socket_kinds(socket_entries) -> dict                            # {fixed, option, random}
def is_craftable(manifest, item_hash) -> bool                       # find_items_by_type(30, ...) 命中
```

`vendor_service`、`weapon_detail_service`、`weapon_roll_filter_service` 三处旧表全部改为引用这一份。

### 2.3 各 intent 的映射

| intent | 改成 | 删掉 |
| --- | --- | --- |
| `info` | `weapon`（完整身份块）+ `perks.slots`（固定/可选）+ `catalysts`（仅异域）+ community/farming | `intrinsicPerks`（并入 perks 的"框架"槽）、camelCase 键 |
| `stats` | `weapon`（精简身份块）+ `stats` | 现在缺 identity 的形状 |
| `perk_pool` | `weapon` + `perks.slots`（带 `kind`） | `weapon_name`/`weapon_type`/`item_hash` 顶层平铺（并入 identity） |
| `analyze` | `weapon` + `perks` + `god_roll`（**结构化**）+ `inventory` + community/farming | `god_roll` 字符串；`perk_pool` 顶层键 |
| `god_roll` | `weapon` + `god_roll: {kind: "fixed"|"recommended", source, pve[], pvp[], note}` | 纯文字 |
| `popularity` | `weapon` + `popularity`（原样）+ 固定武器时 `note` 说明无意义 | — |
| `catalog` | `matched[]` 每项用**精简身份块**（含 rarity/roll_kind） | 顶层 `tier`/`damage_type`/`ammo_type` 散字段 |
| `filter_rolls` | `matched[]` 每项 = 精简身份块 + `instance_id`/`location`/`power`/`perks[]`（PerkInfo 数组，不再是字符串数组） | 字符串 perks |
| `type` | `weapons[]` 每项 = 精简身份块 + 实例字段（instance_id/power/location/sockets/stats） | `tier` 半张表 |
| `compare` | 实例项并入身份块；`differences` 维持上一轮修好的实例定位 | — |
| `catalyst` | 维持上一轮的结构，`unlock_state` 暂为 `not_checked` | — |

**精简身份块**（列表类用）：`item_hash/name/name_en/rarity/rarity_tier/weapon_type/frame/intrinsic/rpm/roll_kind/is_craftable/icon_url` + 列表特有字段（`owned`/`location`/`instance_id`/`matched_perks`）。

### 2.4 固定 vs 随机的语义分支

| intent | `roll_kind="fixed"` | `roll_kind="random"` |
| --- | --- | --- |
| `god_roll` | 列 Manifest 固定 perk；`note` 说明"固定 roll 武器没有推荐 roll"；愿单有内容则标为 `source: manifest+wishlist` 补充 | 现有愿单推荐 |
| `popularity` | `note`："固定 roll 武器，选取率没有意义" | 现有逻辑 |
| `perk_pool` | `note`："固定 perk（仅部件可选），不是随机池" | 现有逻辑 |
| `filter_rolls` | 按 perk 筛仍可用（固定 perk 也算命中），但 `note` 说明"没有 roll 可变" | 现有逻辑 |

### 2.5 兼容策略（**需要你拍板**）

推荐：**直接换成新形状 + 同步语料/skill**，不留旧键。

- 旧键的消费方只有：本仓库语料、测试、skill 的 `routing.md`，没有外部依赖；
- 新旧并存会让"乱"继续存在，且每个 intent 都要维护两套映射；
- 代价：这一轮语料里涉及武器的行要改（约 30 行），skill 的武器章节要重写一遍。

---

## 3. 分阶段任务

### P1 · 新建 `destiny_mcp/services/weapon_profile.py`（纯函数，可单测）
- 唯一稀有度表 + `rarity_of`；**`frame_of`/`intrinsic_of`**（固有槽 → 框架/专属特性）；`rpm_of`；`roll_kind`；`socket_kinds`；`is_craftable`；`build_identity(manifest, item_hash, owned=None)`。
- 验收：单测覆盖 6 把真实形态（泰拉巴/枯骨鳞片/遗产/牵引器火炮/刚愎自用/Ψ卷云II 的结构样本）+ 稀有度 5 档 + 未知 tier 兜底 + **框架三种情形**（传说有框架 / 异域只有专属特性 → frame=null 但 intrinsic 有值 / 老武器的非框架固有 → frame=null）。
- 文件：新增 1 个模块 + 1 个测试文件。

### P2 · perk 块（保留 socket 种类 + 强化配对 + 能否滚出）
- `perk_service.get_weapon_perks` 改为按 socket 逐条产出 `{slot, kind, option_count, plugs}`，槽位中文标签与实例侧统一；`plug_category` 保留英文类别。
- 新增 `weapon_profile.enhanced_pairs(manifest)`：按「同 plug 类别 + 同名字 + tierType 2/3」生成配对表（实测 272 组），供 `enhanced`/`enhanced_plug_hash` 使用；生成结果可缓存。
- plug 条目带 `can_roll`（plug set 的 `currentlyCanRoll`），退役 perk 不再冒充"可能滚到"。
- `god_roll` 从字符串改成结构化结果（`get_god_roll` 返回 dict）。
- 验收：单测断言 `kind` 分布（遗产 4 个 random、泰拉巴 0 个）、强化配对数量与抽查 3 组文案、`can_roll=false` 的条目被标出；`analyze`/`perk_pool`/`god_roll` 三个入口拿到同一个 perk 块。

### P3 · 接入身份块 + 补实例级数据（10 处形状收敛到 2 个块）
- **`weapon_detail_service` 的组件请求加 310（ItemReusablePlugs）**，并把 305（已装）+ 310（能换）+ 300（`gearTier`）合成实例级 perk 信息；没有实例数据时退回定义级并标明 `scope`。
- 改：`manifest_query_service`（info/stats/catalyst 键名 snake_case + `frame`/`intrinsic`/`rpm`）、`weapon_detail_service`、`weapon_roll_filter_service`（catalog/filter 两处条目）、`weapon_compare_service`、`weapon_popularity_service`（复用身份块）、`inventory_service` 的 type 查询（补精简身份块）、工具层 `assistants.py` 的武器分支。
- 验收：工具级测试逐 intent 断言 `data.weapon`/`data.perks` 的键集合；live 冒烟四把武器 × 全部 intent 的形状一致。

### P4 · 语义分支（固定 vs 随机）
- `god_roll`/`popularity`/`perk_pool`/`filter_rolls` 按 `roll_kind` 给正确回答与 `note`。
- 验收：泰拉巴 → 固定 perk + 说明；遗产 → 随机推荐；枯骨鳞片（可锻造金）→ 走随机分支。

### P5 · 测试、语料、skill
- 新增/更新测试：`tests/test_weapon_profile.py`、`tests/test_weapon_format_contract.py`（键集合契约）、更新 `test_response_completeness_regressions.py` 里受影响的断言。
- 语料：第三章（Manifest/账号两侧）、第十二章 B（新增固定/随机两行）、第十三章回归点补本轮；skill `routing.md` 武器章节重写。
- 验收：`pytest` 全绿 + `scripts/verify_mcp.py` + 语料里武器相关行逐条实跑。

### 可选 P6 · perk 池瘦身
现状 `perk_pool` 对紫武器 32 KB（把每个 perk 的描述都带上）。可选做法：**只给愿单标中的 perk 带 `description`**，其余只给名字，预计 32 KB → 8 KB 量级。要不要做请一并拍板。

---

## 4. 风险与取舍

| 项 | 说明 |
| --- | --- |
| 破坏性 | 换新形状后旧键消失；除本仓库语料/测试/skill 外无外部依赖，风险可控 |
| 影响面 | 6 个服务 + 1 个工具分支；`analyze` 是最大聚合（同时含 weapon/perks/roll/inventory），要重点测 |
| 遗留工具 | `weapon_tools.py` 的老工具（`get_weapon_perks`/`get_god_roll`）默认屏蔽，跟着服务一起变；不额外适配，只在语料注明 |
| 账号侧解锁状态 | `catalyst.unlock_state`、锻造 enhanced perk 仍不做（需要账号记录组件），保持 `not_checked` 明说 |
| 不做 | 武器"菜单→详情"两态（武器已有 catalog/type 列表 + 单品详情，缺的是统一模板）；perk 图标/描述质量 |

---

## 5. 验收清单（实施完逐条核对）

- [ ] 稀有度表只有一份，蓝/绿/白不再显示空字符串
- [ ] 身份块带 `frame`/`intrinsic`/`rpm`：遗产 `frame="精确重击框架"`；泰拉巴 `frame=null` + `intrinsic="贪食野兽"`（话术用"异域专属特性"）；刚愎自用 `frame="攻击型框架"`
- [ ] 列表类（catalog/type/filter_rolls）每项都能看到 `frame`（用户最常按框架找枪）
- [ ] 单武器详情能区分**定义级**与**实例级**：定义级给完整池（遗产特性栏 19 个 + 退役标记 + 强化配对），实例级给这一件的 T 级与实际可换选项（T5 遗产特性栏 3 个）
- [ ] `gear_tier` 正确：T5/T4/T3/T2 各一例；老武器（T0）为 0/null 并说明"无 T 级"
- [ ] 强化 perk 成对出现：同一 perk 的 `enhanced=false/true` 两条，且 `enhanced_plug_hash` 指向对方
- [ ] `can_roll=false` 的退役 perk 被标出，不影响"能不能滚到"的结论
- [ ] 同一把武器在 info/stats/perk_pool/analyze 里拿到**同一个** `weapon` 块（哈希、稀有度、roll_kind 一致）
- [ ] `perks.slots[].kind` 与真实 socket 结构一致；`roll_kind` 由它推出
- [ ] 泰拉巴：`roll_kind=fixed`，god_roll 给固定 perk + 说明；popularity 说明无意义
- [ ] 枯骨鳞片：`roll_kind=random`、`is_craftable=true`，走随机分支
- [ ] `god_roll` 是结构而不是一段文字；`analyze.god_roll` 同一结构
- [ ] 列表类（catalog/type/filter_rolls）每项都有 rarity 与 roll_kind，且带 `total/returned/truncated`
- [ ] 路径：`pytest` 全绿、`PARAMETER_GUARD=ok`、8 个工具、语料武器章节逐条实跑
