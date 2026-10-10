"""凡输出武器/装备（护甲）/perk 身份的出口，必须带 `icon_url`：跨切面静态守门。

**为什么有这个文件（这笔账要记清楚，不然下次还会有人问"为什么不能自己拼一个 URL"）：**

图标 URL 以前在 12 处各拼一遍（`f"https://www.bungie.net{icon}"`、`_cdn_url()`、
`_absolute_icon_url()`、`BUNGIE_BASE_URL + icon`…），同一个 `icon_url` 字段因此有四种边界行为；
而**出口之间对不齐**这件事不会报错，只会让前端"有的卡片有图、有的只能放色块"——
用户 2026-10-04 拿实拍截图报的正是这个（`inventory_assistant(intent="mods")` 的五件护甲
只有 slot/name/instance_id/hash/is_exotic/power/energy/mods；锻造图样、`find`/`recommend`
的候选行、账号配装的 `build_template` 同样没有图）。

口径照老 web（`~/项目/Destiny_MCP/webui/api/destiny_icons.py::normalize_destiny_icon_url`）：
键名一律 `icon_url`（**perk 与物品同名**，不另造 `perk_icon`/`image`/`icon`），
值一律是绝对的 Bungie CDN 地址。这里守两件事：

1. **单一出处**（`test_no_second_icon_url_constructor`）：除了
   `destiny_mcp/utils/icons.py`，任何模块都不许把 Bungie 源站字面量与别的表达式拼起来
   当图标值用。判据：找 `JoinedStr`（f-string）与字符串 `+` 拼接里含 `bungie.net`
   字面量的节点，再看它绑给了谁 —— 绑定名/字典键/关键字实参里带 `icon` 的判红。
   不看在哪儿出现、只看"结果是不是当图标在用"，所以 `bungie_client` 的
   `f"https://www.bungie.net{world_url}"`（Manifest 资源地址，不是图标）不在此列。
2. **身份行必须带 `icon_url`**（`test_identity_rows_carry_icon_url`）：**七种**形状
   （字典字面量 / 键清单常量 / `Model(...)` 构造调用 / **模型类的字段声明** /
   **逐次装配 `x.update(…)`+`x["k"]=v`** / **内联键清单** / **只有 `item_hash` 的字典字面量**）
   同时带**名字键**与**身份 hash 键**时（后三种只需带身份 hash 键）必须也有 `icon_url`，
   除非登记在下面的 `_EXEMPT` 台账里（每条写明理由）。后三种是 2026-10-05 补的，
   每一种都对应一次"守门全绿、出口却没图"的现场（见各自 docstring 与
   `docs/plans/ICON_URL_PLAN.md` §十四）。
3. **活动身份行另算一套**（`test_activity_rows_carry_icon_url`）：带 `activity_name`/`activity`
   的行（活动、副本、PvP 场次）也必须带 `icon_url`。**为什么单列**：那类图标不是物品图，
   走的是另一条道（`pgcrImage`），用物品道的判据扫不到、也守不住 —— 2026-10-04 的缺口
   就是"物品全对、活动全裂"（见 `docs/plans/ICON_URL_PLAN.md` §八）。
4. **活动道只能有一个出处**（`test_activity_images_come_from_the_activity_lane`）：
   全仓只有 `manifest_lookup` 读 `pgcrImage`。
5. **社区配装的图不许猜**（§⑥ 那五条）：社区模板只给**名字**，名字→物品那一跳必须可核 ——
   精确名解析、同名多版本**图一致**才出图、裸标签必须带类型过滤、套装没有图。
   每条断言都对着一个**实测反例**（「贪婪之握」是活动名、「棱镜」命中武器皮肤、
   「重型弹药搜寻者」3 个定义 2 种图）。判据在 `services/starside_build_icons.py`。
   为什么单列：它守的是**不可信参考**那条边界（社区资料不是账号事实），
   而"看出来像是同一件东西"正是这条边界上最容易破的一处。

**台账是冻结的复核清单**，不是"让自己变绿的开关"：新增的漏网出口直接判红；
台账里过期（代码已经补上图标或删掉）的条目也判红，逼着条目跟着代码走。
按 `tests/test_hash_domains.py` 的老规矩：确实存在、只是这一轮不修的，理由里写清是什么、
留多少体积，而不是含糊过去。

**已知漏检边界（别把它当成"图标全查过了"）：**

- 只认**字面量**形状的行：经过 `{key: item.get(key) for key in KEYS}` 投影出来的行，
  只有 `KEYS` 常量本身在扫描范围内（`_ROW_ITEM_FIELDS` 就是这么被抓到的）；
- **`dict.update(**kwargs)` 这种"逐次装配"的行**：2026-10-05 收进第 5 种形状
  （`_assembly_rows`）—— `loadout_service._build_template` 的
  `class_data.update(subclass_item_hash=…, icon_url=…)` 当初是靠人核发现的，不是守门抓的；
- 名字/hash 键表是**封闭词表**（见 `_NAME_KEYS` / `_HASH_KEYS`），换个键名就漏
  （2026-10-05 收进了 `subclass_name`/`subclass_hash` 与裸 `hash`；`record_hash`/`tier_hash`
  一类仍刻意在外）；
- 只扫 `destiny_mcp/**`：`legacy/`（历史存档，不进包）与 `tests/` 不在范围内；
- **跨函数/跨语句拼装**仍扫不到：一个函数返回名字、调用方再补 hash（或反过来），
  以及 `Model(**row)`、`{**base, …}` 这类运行期合并 —— 判据只在**同一个函数内**聚合。
"""

from __future__ import annotations

import ast
from pathlib import Path

from destiny_mcp.manifest_lookup import DefinitionLookupMixin
from destiny_mcp.utils.icons import (
    BUNGIE_ORIGIN,
    is_bungie_activity_image,
    is_bungie_icon,
)

SOURCE_ROOT = Path(__file__).resolve().parents[1] / "destiny_mcp"

#: 唯一允许构造图标 URL 的模块（`BUNGIE_ORIGIN` / `icon_url()` 的出处）
ICON_SOURCE = "destiny_mcp/utils/icons.py"

#: 名字键：一行"这是什么"靠它
_NAME_KEYS = {"name", "plug_name", "item_name", "set_name", "perk_name", "subclass_name"}
#: 身份 hash 键：一行"是哪一件"靠它。
#:
#: 2026-10-05 扩表（用户实拍的三处缺口之一）：原来只有 `item_hash`/`plug_hash`/`set_hash`
#: 那一族，**不含** `subclass_name`/`subclass_hash`，也**不含裸 `hash`**。实测后果：
#: `subclass_assistant(intent="get")` 的 `data.subclass`（`subclass_name` + `subclass_hash`）
#: 明明是一行的身份，却在扫描面之外 —— `plugs[]`/`available[]` 每颗都有图，唯独
#: "这是哪个子职业"那一行没有（模型于是在回复里写"本次响应未带图标字段，按文本行渲染"）。
#: 这条盲区**不是裸 `hash`**：`subclass_hash` 根本不是 `hash`。
#:
#: 裸 `hash` 当时被排除的理由是"套装层级/记录/档位表到处都是，会把台账冲成噪声"。
#: 2026-10-05 把这个理由**实测**了一遍（`_HASH_KEYS | {"hash"}` 跑一次扫描）：全仓只多出
#: **21** 行，**没有一行**是套装层级/记录/档位表，全是 `{hash, name}` 形状的物品身份行。
#: 所以假设不成立，裸 `hash` 一并收进来 —— 收进来之后那 21 行要么补图、要么进台账写清理由。
#:
#: 同日第二次扩表（**"逐次装配"那条判据的注入验证逼出来的**）：收进"物品/组件身份"那一族 ——
#: `subclass_item_hash` / `plug_item_hash` / `new_plug_hash` / `enhanced_plug_hash` /
#: `perk_hash` / `super_hash` / `grenade_hash` / `melee_hash` / `class_ability_hash` /
#: `movement_hash`。**根因**：`loadout_service._build_template` 的
#: `class_data.update(subclass_item_hash=…)` 写了 `icon_url` 却没人管 —— 不是因为形状漏了
#: （第 5 种形状正好抓它），而是因为 `subclass_item_hash` **不在词表里**，判据根本认不出那是个
#: 身份行。**先量后收**：这一族加进来全仓只多 **0** 行（都是已经带图的 build/subclass 行）。
#: 同族里**故意不收**的（`name_hash`/`icon_hash`/`color_hash` 是配装外观标识、`vendor_hash`/
#: `progression_hash`/`milestone_hash`/`metric_hash`/`node_hash`/`stat_hash`/`record_hash`/
#: `tier_hash`/`with_hash` 不是物品）：实测收进来会多 **20** 行，其中 20 行全要写台账理由
#: —— 那是把台账冲成噪声，不是覆盖（判据的价值在于"点名的每一行都能被核对"）。
_HASH_KEYS = {"item_hash", "plug_hash", "itemHash", "plugItemHash", "set_hash",
              "subclass_hash", "hash", "subclass_item_hash", "plug_item_hash", "new_plug_hash",
              "enhanced_plug_hash", "perk_hash", "super_hash", "grenade_hash", "melee_hash",
              "class_ability_hash", "movement_hash",
              # 2026-10-05 第三次扩表（**为了让商人那一行落在覆盖面里**）：`vendor_hash`
              # 当初被归进"不是物品"那一族一起排除，理由是那**一族**会多 20 行噪声。
              # 单收它一个实测只多 **4** 行：两行是商人身份出口（`VendorInfo` 类体 +
              # `get_vendor_inventory` 的构造调用 —— 本轮补 `icon_url` 的对象，收进来才有守门）、
              # 两行是 `vendor_menu.VendorIdentity`（匹配/排序的中间体，已登记台账）。
              # 拿"一族"的量去否掉"一个键"是不成立的（同 §14.3 裸 `hash` 那次）。
              "vendor_hash"}
#: **光有 hash 也算身份行**的那一个键（2026-10-05 新增的判据）。
#:
#: 上面那套要求"名字键**和**身份 hash 键同时在"，因为只有 hash 的行未必是给人看的身份行
#: （`{"plugItemHash": …}` 这种合成查询体到处都是）。但形状反过来漏掉了一整类真实出口：
#: **副本行视图**每行只有"这把枪的实例 + 它属于哪个 hash"，名字在卡头说一次就够
#: （`weapon_analysis_projection.compare_rows`）。真机实测：8 把同一个 hash 的行视图里
#: `icon_url` 出现 **0** 次，而静态守门全绿 —— 因为它既没有名字键、hash 键也没配名字键。
#: 只收 `item_hash`（"这是哪件物品"最强的那一个），不收 `plug_hash`/裸 `hash`：
#: 后两者在全仓有 17 处是**取数中间体**（合成查询体、求解器入参），收进来只是台账噪声。
_ITEM_IDENTITY_KEYS = {"item_hash"}
#: 活动/副本身份行的键（与物品那套**分开**：它们的图标走另一条道，见模块开头第 3 条）
_ACTIVITY_NAME_KEYS = {"activity_name", "activity"}
#: **只有活动 hash 也算活动身份行**（2026-10-05 新增的判据，与 `_ITEM_IDENTITY_KEYS` 同一个道理）。
#:
#: 上面那对名字键漏掉了一整类真实出口：**活动行只有 hash + 名字在别处**——`rotations` 的特色
#: 突袭/地牢行头（`name` 是里程碑名、身份是那组 `activityHash`）、`nightfall` 行、
#: `rotation_service._activity_row`、`pvp_match_tally.activity_rows`（`{activity_hash, name, …}`）。
#: 实测：这几行**一条都没被扫到**（`test_activity_rows_carry_icon_url` 的 docstring 里
#: 却写着"覆盖 rotations / pvp_weapons" —— 那是句假话，这一轮一并改成真的）。
#:
#: 两条边界（都按实测取的最窄口径，不是想当然）：
#: - 只扫**字典字面量**：`get_icon_url(activity_hash=…)` 这种**调用**不是行；
#: - **还要有名字键**：否则 `pvp_weapon_service` 逐场扫描的暂存行
#:   （`candidates[instance_id] = {instance_id, class, period, activity_hash, …}`，
#:   没有名字键）会被卷进来 —— 它不是给人看的行，也不该为它开一条活动行台账
#:   （活动行的口径是"**没有**豁免台账"，见下面的测试）。
_ACTIVITY_IDENTITY_KEYS = {"activity_hash"}
_ICON_KEYS = {"icon_url", "iconUrl"}

# ── 豁免台账 ──────────────────────────────────────────────────────────────
# 键 = `相对路径::函数名`（键清单常量用 `相对路径::常量名`）。每条都要写**为什么**：
# 理由必须能被核对 —— 写清"是什么东西、凭什么不给图、代价是多少"，不许写"忘了/以后再说"。
#
# ⚠️ **两类"不给图"不属于本台账**（它们形状上就扫不到，理由在这里写死）：
#
# ① **定义级 perk 池**（`weapon_profile` 的 socket options `scope="definition"`）：
#    带上 `icon_url`+`description` = **+17 KB/把（图标 13.2 + 描述 4.0，P6 实测）**
#    （`docs/plans/WEAPON_FORMAT_PLAN.md:360`）。它是"**可能滚到什么**"、不是"你有这件"；
#    要图/要效果走实例级 options 与 `perk_description`。正面钉在
#    `tests/test_perk_icon_output.py::test_perk_pool_includes_weapon_and_perk_icons_with_description_fallback`。
# ② **`duplicates` 的 perk 行**（`inventory_analysis_service._perk_row` → `{name, slot}`）：
#    真机基线每实例 1.81 KB，其中 **`perks` 1.64 KB = 91%**，而 `limit=5` 整包有 **20 KB 闸**
#    （`docs/adr/021-default-exit-gives-rows-and-references.md:15`、
#    `docs/plans/RESPONSE_PROJECTION_PLAN.md:12`）—— 补图就把闸顶破。
#    它压成 `{name, slot}` 是**取舍过的**（同一份 docstring 里写着为什么不能只留 id/光等）。
#    正面钉在 `test_duplicates_perk_rows_stay_lean_by_volume`。
#
# 台账里剩下的每一条都是"这一行**不是出口**（中间产物/回滚快照/求解器内部模型）"，
# 或者"它本来就不是物品"（套装行、收藏品）。
_EXEMPT: dict[str, str] = {
    # `weapon_versions` 造的是**查找行**（hash → index → 定义），不是给模型看的输出行。
    "destiny_mcp/services/weapon_profile.py::weapon_versions": "版本查找行，不是输出行",
    # 指纹载荷（不是给模型看的输出行）：那几行是 hash 的输入，加 icon_url 会改指纹、
    # 把已签发候选全部作废 —— 登记豁免。
    "destiny_mcp/build/snapshot_fingerprint.py::_rows": "指纹载荷：加 icon_url 会改指纹",
    # ── ① 定义级 perk 池：体积口径（见上面 ⚠️ ①，+17 KB/把）──────────────────
    # `scope="definition"` 的 perk 池默认不带 description/icon_url —— 池子回答"能滚到什么"，
    # 要看效果走 `perk_description`、要图走实例级 options。
    # 见 `services/weapon_payload.socket_list` 与 `tests/test_weapon_key*` 的
    # `DEFINITION_ONLY_ABSENT`；要翻这条得先回答 17 KB/把 的账（用户 2026-10-04 拍板：不带）。
    "destiny_mcp/services/weapon_profile.py::_plug_option": "定义级池子不带图标：带 = +17 KB/把（图标 13.2 + 描述 4.0，P6 实测，docs/plans/WEAPON_FORMAT_PLAN.md:360）",
    "destiny_mcp/services/weapon_profile.py::intrinsic_plug": "固有特性走 socket 行的同一套投影",
    "destiny_mcp/services/weapon_profile.py::with_equipped": "`equipped` 只报「装着哪个」，图在 options 里",
    "destiny_mcp/services/weapon_payload.py::_normalize_equipped": "同上：每槽一个 name/hash 对，图在 options 里",
    # 选取率快照（第三方统计）里的 perk 行：快照本身只有 name/selection_rate，图标是**定义级**的。
    # 同一个 `popularity` intent 的另一条路（`weapon_popularity_service._enrich_entry`）本来
    # **带**图标 —— 2026-10-04 已按同一条口径**统一成不带**（那条路不再有 icon_url），
    # 两条路的一致性由 `test_popularity_paths_agree_on_perk_rows` 钉住。
    "destiny_mcp/services/weapon_local_data.py::_popularity_summary": "定义级 perk 行不带图标（与同 intent 的选取率服务统一，体积口径）",
    "destiny_mcp/services/weapon_popularity_service.py::get_weapon_popularity": "同上：`popular_combinations[].perks[]` 与 `perk_columns` 是同一批定义级 perk 行",
    # ── ② 原始 Manifest 行 / 内部暂存：不是出口 ────────────────────────────
    "destiny_mcp/manifest_search.py::_build_name_index": "索引条目用原始键 `icon`（已是绝对地址），出口才改名 icon_url",
    "destiny_mcp/manifest_armor.py::_lookup_set_bonus": "套装（set_hash/set_name）不是物品，没有自己的图标",
    "destiny_mcp/services/set_bonus_service.py::lookup_armor_set": "套装行同上；`armor_pieces[]` 已带图标",
    "destiny_mcp/services/set_bonus_service.py::list_all_set_bonuses": "套装行同上（列表里没有具体某一件）",
    "destiny_mcp/services/starside_matching.py::_set_resolution": "社区模板匹配的**中间产物**，不进响应",
    "destiny_mcp/services/starside_matching.py::_exact_definitions": "社区模板匹配的**中间产物**，不进响应",
    "destiny_mcp/services/starside_matching.py::validate_build": "社区模板校验结果（不可信参考），不是账号物品",
    "destiny_mcp/services/weapon_detail_service.py::add_weapon": "收集阶段的暂存行，出口走 weapon_payload.list_row（有图）",
    "destiny_mcp/services/manifest_query_service.py::_find_catalysts": "催化剂候选暂存，出口 `catalyst` 行已带图标",
    "destiny_mcp/services/manifest_query_service.py::_extract_catalyst_from_sockets": "同上（另一条取候选的路）",
    "destiny_mcp/services/pattern_service.py::_catalog": "图样目录的中间行，出口 `_row` 已带 icon_url（这里存 icon_path）",
    "destiny_mcp/services/armor_mod_service.py::plan": "模组计划回执的用户输入回显，不是物品身份行",
    "destiny_mcp/services/loadout_recovery.py::_capture_recovery_state()→LoadoutItem": "回滚用的现场快照，不进响应（恢复时按实例 ID 逐件还原）",
    "destiny_mcp/services/vendor_service.py::_compact_response()→PerkInfo": "商人**菜单**的 perk 摘要：这里刻意只留名字 + [PvE]/[PvP] 标记（不是真 perk 身份，plug_hash 都被置 0）",
    "destiny_mcp/services/weapon_profile.py::socket_options()→_plug_option": "定义级池子不带图标（与 `_plug_option` 同一条，+17 KB/把）",
    # ── ④ 求解器内部模型：不是出口（`build_projection` 就是把这些投影掉的）──
    # `TuningChoice`/`PieceTuning` 是护甲求解器内部的行；默认出口走候选行投影，
    # 要执行走 `execution_id` → `canonical_build`。它们不该各自长成一个图标字段。
    "destiny_mcp/build/tuning.py::_catalog()→TuningChoice": "求解器内部模型（`build_projection` 会投影掉）",
    "destiny_mcp/build/tuning.py::piece_tuning()→PieceTuning": "求解器内部模型（同上）",
    # ── ③ 收藏品：不在"武器/装备/perk"三类的口径里 ─────────────────────────
    "destiny_mcp/services/collection_service.py::get_collectible_item_status": "收藏品（徽章/机灵外壳等）不属于武器/护甲/perk",
    "destiny_mcp/services/collection_service.py::get_collectible_node_status": "同上（节点视图）",
    # ── ⑤ 2026-10-05 收进裸 `hash` 之后新进扫描面的 10 行：都不是出口 ──────────
    # 这一批是"把裸 `hash` 收进 `_HASH_KEYS`"的直接产物。每一条都当场核过：
    # 要么是**求解器/取数的中间模型**（根本进不了响应），要么那一行**本来就不是物品**。
    "destiny_mcp/build/process_types.py::armor_to_process_item()→ProcessItem": "求解器内部模型（护甲进程项的入参形状），不进响应",
    "destiny_mcp/build/tuning.py::as_dict": "求解器内部模型（`TuningChoice.as_dict` 与 `PieceTuning.as_dict` 的 from/to 三处字典字面量都归这一个键）：投影成候选行的那一层有图",
    "destiny_mcp/manifest_item_queries.py::find_items_by_plug_category": "取数中间行（原样带 `displayProperties`，出口自己 `_icon_url()`；消费方是 `fragment_service`，它的出口行有图）",
    "destiny_mcp/services/build_service.py::_get_fragment_stats_by_names": "求解器输入回执（碎片名 → 六维向量），不是给人看的身份行",
    "destiny_mcp/services/collection_service.py::_declared_children": "`DestinyPresentationNodeDefinition` 展示节点（收藏品的文件夹），不是物品：Manifest 里这类节点没有物品图标语义",
    "destiny_mcp/tools/_armor_branches.py::armor_item": "护甲**套装**行（`equipableItemSetHash`）：套装不是物品 —— 实测 `DestinyEquipableItemSetDefinition` 741162535 写着 `hasIcon: false`，给不出图也不能拿别的顶（同 `_lookup_set_bonus`）",
    "destiny_mcp/tools/_armor_branches.py::equip_mod": "`plan[\"from\"]` 的**空槽**占位（`hash`/`name` 都是 `None`），不是物品",
    "destiny_mcp/tools/_perk_branches.py::perk_description_payload": "`{**(perk or {}), …}` 的**合并**：`icon_url` 运行时从上游 `get_perk_description()` 的载荷继承（那里 `_icon_url(...)` 已有），静态扫描看不见 spread",
    # ── ⑥ 模型类（第四种身份行形状，2026-10-05 注入验证补上的判据）─────────────
    # 这四行都是**求解器内部模型**：它们的实例从不进响应 —— `process_types.ProcessItem`
    # 与 `build/tuning.py` 的 `TuningChoice`/`PieceTuning` 由 `build_projection` 投影成
    # 候选行（那一层有图），`armor_rules.ArmorArchetype` 只在求解器里当键用。
    # 同一批文件里的**字典**形状早就各自登记过了（`as_dict`、`armor_to_process_item`），
    # 这里补的是"类体声明"这个形状 —— 判据本身是因为 `SubclassConfig` 那次漏网才加的。
    "destiny_mcp/build/armor_rules.py::ArmorArchetype": "求解器内部模型（词条原型在求解器里的键），不进响应",
    "destiny_mcp/build/process_types.py::ProcessItem": "求解器内部模型（同上，字典形状那条也登记着）",
    "destiny_mcp/build/tuning.py::TuningChoice": "求解器内部模型（`build_projection` 投影成候选行，那一层有图）",
    "destiny_mcp/build/tuning.py::PieceTuning": "求解器内部模型（同上）",
    # ── ⑦ 2026-10-05 收进"只有 `item_hash` 的字典字面量"之后新进扫描面的 5 行 ──────
    # 这条判据是为了**副本行视图**（每行只有"是哪一件"、名字在卡头说一次）才加的：
    # 真机实测 8 行副本里 `icon_url` 出现 0 次而守门全绿。下面是它顺带扫出来的全部行，
    # 逐条核过 —— 都是**取数/指纹的中间产物**，没有任何一行进响应。
    "destiny_mcp/services/pvp_weapon_service.py::get_pvp_weapon_board": "逐场累计的暂存表 `totals`；出口行在同一函数下面另建（`name`/`icon_url` 都在）",
    "destiny_mcp/services/weapon_compare_service.py::compare_weapon_instances": "`weapon_instances` 的收集行（146–185 两处），出口行在 `weapon_analysis_projection.compare_rows`（2026-10-05 起每行带图）",
    "destiny_mcp/services/weapon_compare_service.py::_check_name_match": "同上：按名字兜底匹配时的收集行（`weapon_instances.append`），不进响应",
    # ── ⑧ 同上，但形状是"逐次装配"（`x["k"] = v` 聚合成一行）───────────────────
    # 这两行的**共同点**：`icon_url` 由**另一个具名工厂**放进对象，装配语句只补别的键 ——
    # 静态扫描看不见"那个工厂给了什么"，所以只能逐条登记（两处都在真机响应里核过）。
    "destiny_mcp/services/weapon_popularity_service.py::_weapon_identity()::block": "身份块由 `weapon_payload.lean_identity` 造（`LEAN_IDENTITY_KEYS` 含 `icon_url`，真机 `data.weapon.icon_url` 非空）；这里只覆盖 `item_hash` 与版本标签",
    "destiny_mcp/services/weapon_popularity_service.py::_enrich_entry()::result": "定义级 perk 行，**按口径不带图**（§十 第 4 项；两条路一致由 `test_popularity_paths_agree_on_perk_rows` 钉住）：`plug_hash` 在这里补，图故意不给",
    # ── ⑨ 2026-10-05 第六轮：收进 `vendor_hash`、活动 hash 后新进扫描面的 3 行 ───────────
    # 前两条是**同一个中间体**的两半（模型类体 + 构造调用）。它**不进响应**：出口行是
    # `VendorInfo`（`get_vendor_inventory` 里现造，两条形状判据都已覆盖，见
    # `test_icon_url_output` 的 `VendorInfo` 类体与 `vendor_service.py:668` 的构造调用）。
    # 这个 `VendorIdentity` 只用来做名字匹配、别名解析与排序（`services/vendor_menu.py`）。
    "destiny_mcp/services/vendor_menu.py::VendorIdentity": "商人身份的**匹配/排序中间体**（不是出口行）：出口是 `VendorInfo`（本轮补了 `icon_url`，类体与构造调用两条判据都咬得住）",
    "destiny_mcp/services/vendor_menu.py::vendor_identities()→VendorIdentity": "同上：`vendor_identities()` 造的就是上一条那个中间体，不进响应",
}


def _modules() -> list[tuple[str, Path]]:
    return [
        (f"destiny_mcp/{path.relative_to(SOURCE_ROOT).as_posix()}", path)
        for path in sorted(SOURCE_ROOT.rglob("*.py"))
        if "__pycache__" not in path.parts
    ]


def _functions(tree: ast.AST) -> dict[int, str]:
    """节点 id → 最近的函数名（没有函数包裹就给 `?`）。"""
    owner: dict[int, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            for child in ast.walk(node):
                owner[id(child)] = node.name
    return owner


def _dict_keys(node: ast.Dict) -> set[str]:
    return {
        key.value
        for key in node.keys
        if isinstance(key, ast.Constant) and isinstance(key.value, str)
    }


def _names_in(node: ast.AST) -> set[str]:
    return {n.id for n in ast.walk(node) if isinstance(n, ast.Name)}


# ── ① 单一出处 ────────────────────────────────────────────────────────────


def _concats_bungie_origin(tree: ast.AST) -> list[ast.AST]:
    """所有"把 Bungie 源站字面量与非字面量拼起来"的节点。"""
    hits: list[ast.AST] = []
    for node in ast.walk(tree):
        pieces: list[ast.AST] = []
        if isinstance(node, ast.JoinedStr):
            pieces = list(node.values)
        elif isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
            pieces = [node.left, node.right]
        else:
            continue
        has_literal = any(
            isinstance(p, ast.Constant)
            and isinstance(p.value, str)
            and "bungie.net" in p.value
            for p in pieces
        )
        has_dynamic = any(not isinstance(p, ast.Constant) for p in pieces)
        if has_literal and has_dynamic:
            hits.append(node)
    return hits


def _bound_to_icon(module_tree: ast.AST, node: ast.AST) -> bool:
    """这个拼接表达式是不是"当图标在用"（赋给 icon* 名字 / 当 icon* 键或关键字实参）。"""
    for parent in ast.walk(module_tree):
        for field, value in ast.iter_fields(parent):
            children = value if isinstance(value, list) else [value]
            if node not in children:
                continue
            if isinstance(parent, ast.Dict):
                for key, item in zip(parent.keys, parent.values):
                    if item is node and isinstance(key, ast.Constant) and "icon" in str(key.value).lower():
                        return True
            if isinstance(parent, ast.keyword) and "icon" in (parent.arg or "").lower():
                return True
            if isinstance(parent, (ast.Assign, ast.AnnAssign)):
                targets = parent.targets if isinstance(parent, ast.Assign) else [parent.target]
                names = {t.id for t in targets if isinstance(t, ast.Name)}
                if any("icon" in name.lower() for name in names):
                    return True
            # 走到语句这一层就够了：再往上只会看到 If/FunctionDef
            del field
    return False


def test_no_second_icon_url_constructor() -> None:
    """图标 URL 只能由 `utils/icons.py` 构造（键名与归一都只有一份）。"""
    offenders: list[str] = []
    for rel, path in _modules():
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in _concats_bungie_origin(tree):
            if _bound_to_icon(tree, node):
                offenders.append(f"{rel}:{node.lineno}")

    assert not offenders, (
        "这些地方自己拼了 Bungie 图标 URL；改用 destiny_mcp/utils/icons.py 的 "
        f"`icon_url()`（唯一构造点）：{offenders}"
    )


def test_the_icon_source_is_the_only_place_with_the_origin_literal() -> None:
    """源站字面量本身也只有一份：`manifest_data` 只能再导出，不许再抄一遍。"""
    literal = f'"{BUNGIE_ORIGIN}"'
    holders = [
        rel
        for rel, path in _modules()
        if literal in path.read_text(encoding="utf-8")
    ]
    assert holders == [ICON_SOURCE], (
        f"`{BUNGIE_ORIGIN}` 的字面量只许出现在 {ICON_SOURCE}（manifest_data 走再导出）：{holders}"
    )


#: 活动道的字段名（`DestinyActivityDefinition.pgcrImage`）。全仓只有一处能读它。
ACTIVITY_IMAGE_FIELD = "pgcrImage"
#: 活动道的**唯一出处**
ACTIVITY_LANE = "destiny_mcp/manifest_lookup.py"


def _code_mentions(path: Path, field: str) -> bool:
    """文件里有没有把 `field` 当**字段名**用（字符串字面量），**不算文档字符串**。

    为什么要排掉 docstring：字段名写进注释/docstring 是"说明"，写进代码才是"读它"。
    这一条是给 `test_activity_images_come_from_the_activity_lane` 用的 —— 它要抓的是
    "第二个地方自己读了活动图字段"，不是"第二份文档提到了它"。
    """
    tree = ast.parse(path.read_text(encoding="utf-8"))
    docstrings = {
        id(node.body[0].value)
        for node in ast.walk(tree)
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef))
        and node.body
        and isinstance(node.body[0], ast.Expr)
        and isinstance(node.body[0].value, ast.Constant)
    }
    return any(
        isinstance(node, ast.Constant)
        and node.value == field
        and id(node) not in docstrings
        for node in ast.walk(tree)
    )


def test_activity_images_come_from_the_activity_lane() -> None:
    """活动/副本的图只能从活动道取：全仓只有 `manifest_lookup` 读 `pgcrImage`。

    为什么值得一条守卫：把活动 hash 塞进物品道（`get_item_info(activity_hash)`）**不会报错**，
    只会一路返回空串 —— 症状是"物品图全对、活动全裂"，而裂的那半边没有任何报错可查
    （`docs/plans/ICON_URL_PLAN.md` §八）。所以这里钉住"活动图字段只有一个读者"。
    """
    holders = [
        rel for rel, path in _modules() if _code_mentions(path, ACTIVITY_IMAGE_FIELD)
    ]
    assert holders == [ACTIVITY_LANE], (
        f"`{ACTIVITY_IMAGE_FIELD}` 只许在 {ACTIVITY_LANE} 里读（活动道的唯一出处）：{holders}"
    )


# ── ② 身份行必须带 icon_url ───────────────────────────────────────────────


def _keyword_identity_rows(tree: ast.AST) -> list[tuple[str, int, set[str]]]:
    """`Model(item_hash=…, name=…)` 这种**构造调用**：关键字实参里同时有名字键与身份 hash 键。

    为什么单列一类：`LoadoutItem` / `PlugOption` / `VendorCost` 这些行不是字典字面量拼的，
    只扫 `ast.Dict` 会整片漏掉（2026-10-04 注入验证时发现的：`build_results` 里的
    `LoadoutItem(icon_url=armor.icon_url)` 拿掉后守门没红）。
    """
    owner = _functions(tree)
    rows: list[tuple[str, int, set[str]]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        kwargs = {kw.arg for kw in node.keywords if kw.arg}
        if not ((kwargs & _NAME_KEYS) and (kwargs & _HASH_KEYS)):
            continue
        callee = (
            node.func.id
            if isinstance(node.func, ast.Name)
            else node.func.attr
            if isinstance(node.func, ast.Attribute)
            else "?"
        )
        rows.append((f"{owner.get(id(node), '?')}()→{callee}", node.lineno, kwargs))
    return rows


def _model_identity_rows(tree: ast.AST) -> list[tuple[str, int, set[str]]]:
    """**模型类**的身份行：类体里同时声明了名字键字段与身份 hash 键字段。

    为什么单列这一类（2026-10-05 注入验证抓出来的）：前三种形状都扫不到"pydantic 模型少了
    一个字段"。`SubclassConfig` 当时只声明了 `subclass_name` + `subclass_hash`，
    **把新补的 `icon_url` 删掉之后守门仍然是绿的** —— 类体既不是字典字面量、也不是构造调用。
    而这正是用户实拍场景 3 的形状（"这个响应就是没有图那个字段"），所以必须能咬住。
    """
    rows: list[tuple[str, int, set[str]]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.ClassDef):
            continue
        fields = {
            statement.target.id
            for statement in node.body
            if isinstance(statement, ast.AnnAssign) and isinstance(statement.target, ast.Name)
        }
        if (fields & _NAME_KEYS) and (fields & _HASH_KEYS):
            rows.append((node.name, node.lineno, fields))
    return rows


def _update_call_rows(tree: ast.AST) -> list[tuple[str, int, set[str]]]:
    """**逐次装配**的第一半：`x.update(name=…, item_hash=…)`（关键字或字典实参）。

    第 5 种形状，2026-10-05 收进来（用户点名的那个盲区）：`loadout_service._build_template`
    的 `class_data.update(subclass_item_hash=…, icon_url=…)` 是靠**人核**发现的 ——
    它既不是字典字面量、也不是构造调用，前四种形状一个都扫不到。

    **按"每一次 update 调用"判，不按目标对象聚合**。理由是这个文件里真实的形状：
    `class_data` 有两条**互斥分支**各 update 一次（组件 310 那条、`subclass_config` 那条），
    聚合的话"其中一条漏了图"会被另一条盖住 —— 注入验证会假绿，而运行期那一支真的没有图。
    代价：如果图是在**另一条语句**里补的（`x["icon_url"] = …` 之后
    `x.update(name=…, item_hash=…)`），这一条会误报，登记台账或把图并进同一次调用即可。
    """
    owner = _functions(tree)
    rows: list[tuple[str, int, set[str]]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if not (isinstance(func, ast.Attribute) and func.attr == "update"):
            continue
        keys = {kw.arg for kw in node.keywords if kw.arg}
        for arg in node.args:
            if isinstance(arg, ast.Dict):
                keys |= _dict_keys(arg)
        if (keys & _HASH_KEYS) and not (keys & _ICON_KEYS):
            rows.append((f"{owner.get(id(node), '?')}()::{_target_name(func.value)}.update",
                         node.lineno, keys))
    return rows


def _target_name(node: ast.AST) -> str:
    """`x` / `self.row` 这种被装配的对象的可读名字（认不出给 `?`，不猜）。"""
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return f"{_target_name(node.value)}.{node.attr}"
    return "?"


def _subscript_rows(tree: ast.AST) -> list[tuple[str, int, set[str]]]:
    """**逐次装配**的第二半：`x["name"] = …` / `x["item_hash"] = …`。

    这一半必须按 **(函数, 目标对象) 聚合**：单看一条语句永远只有一个键，不聚合就一条都判不了。
    动态键（`class_data[key] = …`，`key` 是循环变量）认不出，跳过 —— 不猜。

    聚合的代价写在模块开头的边界里：同一目标上"图由另一条分支补"会被盖住。
    """
    owner = _functions(tree)
    groups: dict[tuple[str, str], set[str]] = {}
    lines: dict[tuple[str, str], int] = {}
    for node in ast.walk(tree):
        if not isinstance(node, (ast.Assign, ast.AnnAssign)):
            continue
        targets = node.targets if isinstance(node, ast.Assign) else [node.target]
        for target in targets:
            if not isinstance(target, ast.Subscript):
                continue
            key = target.slice
            if not (isinstance(key, ast.Constant) and isinstance(key.value, str)):
                continue
            ident = (owner.get(id(node), "?"), _target_name(target.value))
            groups.setdefault(ident, set()).add(key.value)
            lines.setdefault(ident, node.lineno)
    return [
        (f"{func}()::{target}", lines[(func, target)], keys)
        for (func, target), keys in groups.items()
        if (keys & _HASH_KEYS) and not (keys & _ICON_KEYS)
    ]


def _assembly_rows(tree: ast.AST) -> list[tuple[str, int, set[str]]]:
    """第 5 种形状的两半（`update` 调用 + 下标装配），键表与前四种一致。"""
    return [*_update_call_rows(tree), *_subscript_rows(tree)]


def _inline_key_list_rows(tree: ast.AST) -> list[tuple[str, int, set[str]]]:
    """**内联**的键清单：`{k: row.get(k) for k in ("name", "item_hash")}`。

    第 6 种形状。原来只认**命名**的键清单常量（`_ROW_ITEM_FIELDS = (…)`），
    键清单写在字典推导的 `for … in (…)` 里就整片漏掉。2026-10-05 拿真实响应逆推时
    抓到的第一个实例：`pattern_service` 的歧义候选行（`patterns` 出口的
    `data.candidates[]`）投影时把 `icon_url` 丢了，而那个键清单正是内联元组。
    """
    owner = _functions(tree)
    rows: list[tuple[str, int, set[str]]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.DictComp):
            continue
        for generator in node.generators:
            source = generator.iter
            if not isinstance(source, (ast.Tuple, ast.List, ast.Set)):
                continue
            keys = {
                element.value
                for element in source.elts
                if isinstance(element, ast.Constant) and isinstance(element.value, str)
            }
            if (keys & _HASH_KEYS) and not (keys & _ICON_KEYS):
                rows.append((owner.get(id(node), "?"), node.lineno, keys))
    return rows


def _item_hash_only_rows(tree: ast.AST) -> list[tuple[str, int, set[str]]]:
    """**只有 `item_hash`** 的字典字面量（没有名字键）—— 第 7 种形状。

    为什么单列：前面几条都要求"名字键**和**身份 hash 键同时在"，于是漏掉了
    "名字在卡头说一次、每行只报是哪一件"这种**行视图**。真机实测就是用户实拍那一张：
    `weapon_assistant(intent="compare")` 的 8 行副本里 `icon_url` 出现 **0** 次，
    而守门全绿（那几行没有名字键，hash 键也没配名字键）。
    只收 `item_hash`：`plug_hash`/裸 `hash` 有十几处是取数中间体（合成查询体、求解器入参），
    收进来是台账噪声，不是覆盖（见 `_ITEM_IDENTITY_KEYS` 的注释）。
    只扫**字典字面量**：`get_icon_url(item_hash=…)` 这种**调用**不是行。
    """
    owner = _functions(tree)
    rows: list[tuple[str, int, set[str]]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Dict):
            continue
        keys = _dict_keys(node)
        # 带名字键的行归前四种形状管（否则同一行会被报两遍）
        if not (keys & _NAME_KEYS) and (keys & _ITEM_IDENTITY_KEYS):
            rows.append((owner.get(id(node), "?"), node.lineno, keys))
    return rows


def _identity_rows(tree: ast.AST) -> list[tuple[str, int, set[str]]]:
    """(kind, lineno, keys)：**七种**身份行形状。

    字典字面量 / 键清单常量 / 构造调用 / 模型类 / 逐次装配 / 内联键清单 / 只有 `item_hash`。
    后三种是 2026-10-05 补的，各有一次"守门全绿但出口缺图"的现场（见各自 docstring）。
    """
    owner = _functions(tree)
    rows: list[tuple[str, int, set[str]]] = list(_keyword_identity_rows(tree))
    rows.extend(_model_identity_rows(tree))
    rows.extend(_assembly_rows(tree))
    rows.extend(_inline_key_list_rows(tree))
    rows.extend(_item_hash_only_rows(tree))
    for node in ast.walk(tree):
        if isinstance(node, ast.Dict):
            keys = _dict_keys(node)
            if (keys & _NAME_KEYS) and (keys & _HASH_KEYS):
                rows.append((owner.get(id(node), "?"), node.lineno, keys))
        elif isinstance(node, (ast.Assign, ast.AnnAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            value = node.value
            if not isinstance(value, (ast.Tuple, ast.List, ast.Set)):
                continue
            for target in targets:
                if not isinstance(target, ast.Name):
                    continue
                keys = {
                    e.value
                    for e in value.elts
                    if isinstance(e, ast.Constant) and isinstance(e.value, str)
                }
                if (keys & _NAME_KEYS) and (keys & _HASH_KEYS):
                    rows.append((target.id, node.lineno, keys))
    return rows


def _scan_identity_rows() -> tuple[list[str], list[str]]:
    """返回 (缺失清单, 过期台账条目)。"""
    missing: list[str] = []
    seen: set[str] = set()
    for rel, path in _modules():
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for kind, lineno, keys in _identity_rows(tree):
            if keys & _ICON_KEYS:
                continue
            key = f"{rel}::{kind}"
            seen.add(key)
            if key in _EXEMPT:
                continue
            missing.append(f"{rel}:{lineno} {kind} {sorted(keys & (_NAME_KEYS | _HASH_KEYS))}")
    stale = [key for key in _EXEMPT if key not in seen]
    return missing, stale


def test_identity_rows_carry_icon_url() -> None:
    """武器/装备/perk 的身份行必须带 `icon_url`，否则卡片只能放色块。"""
    missing, _ = _scan_identity_rows()
    assert not missing, (
        "这些身份行没有 `icon_url`；补上（值走 utils/icons.py 的 icon_url()），"
        "确实不该给的登记进本文件的 _EXEMPT 并写明理由：\n  " + "\n  ".join(missing)
    )


# ── ②b 活动/副本身份行（另一条道，另一套键）───────────────────────────────


def _activity_rows(tree: ast.AST) -> list[tuple[str, int, set[str]]]:
    """带活动名字键、或**只有活动 hash** 的行：字典字面量、`Model(activity_name=…)` 构造调用。

    判据刻意**宽**：只要有 `activity_name`/`activity` 就算活动身份行，不要求它同时带 hash ——
    `history` 的行以前就只有名字没有 hash，正是这样漏掉的（补 hash 是这一步的副产品）。
    **只有 `activity_hash` 的字典字面量**也算（2026-10-05 补，见 `_ACTIVITY_IDENTITY_KEYS`）：
    那类行的名字在别处（行头的里程碑名、或者干脆只有 hash），以前一条都扫不到。
    """
    owner = _functions(tree)
    rows: list[tuple[str, int, set[str]]] = []
    for node in ast.walk(tree):
        keys: set[str] | None = None
        if isinstance(node, ast.Dict):
            keys = _dict_keys(node)
        elif isinstance(node, ast.Call):
            keys = {kw.arg for kw in node.keywords if kw.arg}
        if keys and (keys & _ACTIVITY_NAME_KEYS):
            rows.append((owner.get(id(node), "?"), node.lineno, keys))
        elif (
            isinstance(node, ast.Dict)
            and (keys & _ACTIVITY_IDENTITY_KEYS)
            and (keys & _NAME_KEYS)
        ):
            rows.append((owner.get(id(node), "?"), node.lineno, keys))
    return rows


def _scan_activity_rows() -> list[str]:
    """缺 `icon_url` 的活动身份行（没有台账：活动行一律要有图，不存在"该不给"的）。"""
    missing: list[str] = []
    for rel, path in _modules():
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for kind, lineno, keys in _activity_rows(tree):
            if keys & _ICON_KEYS:
                continue
            missing.append(
                f"{rel}:{lineno} {kind} "
                f"{sorted(keys & (_ACTIVITY_NAME_KEYS | _ACTIVITY_IDENTITY_KEYS))}"
            )
    return missing


def test_activity_rows_carry_icon_url() -> None:
    """活动/副本身份行必须带 `icon_url`（`docs/plans/ICON_URL_PLAN.md` §八 的缺口）。

    覆盖的出口：`history` / `pgcr` / `aggregate`（`activity_service`）、`raid_report`
    （`raid_report_service` 的行）、`rotations` 的两个活动行（`rotation_service`）、
    `pvp_weapons`（`pvp_match_tally.activity_rows`）。**没有豁免台账**：
    每一行都是一个真实的副本/活动，给不出图就是漏了。
    """
    missing = _scan_activity_rows()
    assert not missing, (
        "这些活动身份行没有 `icon_url`；走活动道补上"
        "（`manifest.get_icon_url(activity_hash=…)`，别再拼 URL、也别用物品道）：\n  "
        + "\n  ".join(missing)
    )


def test_icon_exemption_ledger_is_not_stale() -> None:
    """台账里过期（代码已经补上图标或删掉）的条目要跟着删，否则它会掩盖新问题。"""
    _, stale = _scan_identity_rows()
    assert not stale, (
        "这些豁免条目在代码里已经不存在或已经带上 icon_url 了，从 _EXEMPT 里删掉："
        f"{stale}"
    )


# ── ③ 真实基线里的图标形状（数据面，不是代码面）────────────────────────────


def _baseline_files() -> list[Path]:
    root = Path(__file__).resolve().parent / "baselines"
    return sorted((root / "weapon_responses").glob("*.json")) + sorted(
        (root / "armor_responses").glob("*.json")
    )


def test_recorded_baselines_use_openable_bungie_icon_urls() -> None:
    """真机录下来的基线里，每个非空 `icon_url` 都要是**打得开**的 Bungie 图标地址。

    这是"形状对不对"的第二条证据：代码面（前面两条）证明每个出口都在构造图标，
    数据面证明构造出来的确实是 `https://www.bungie.net/common/destiny2_content/icons/…`。
    """
    import json

    files = _baseline_files()
    assert files, "基线夹具不见了；这张网就白织了"

    bad: list[str] = []
    checked = 0

    def walk(node: object, where: str) -> None:
        nonlocal checked
        if isinstance(node, dict):
            for key, value in node.items():
                if key == "icon_url":
                    checked += 1
                    if value and not is_bungie_icon(value):
                        bad.append(f"{where}: {value!r}")
                    continue
                walk(value, f"{where}.{key}")
        elif isinstance(node, list):
            for index, item in enumerate(node):
                walk(item, f"{where}[{index}]")

    for path in files:
        walk(json.loads(path.read_text(encoding="utf-8")), path.name)

    assert checked > 0, "基线里一个 icon_url 都没有？夹具或扫描坏了"
    assert not bad, f"这些 icon_url 打不开（形状/前缀不对）：{bad[:10]}（共 {len(bad)}）"


# ── ④ 出口函数内部的两条道（行为面）───────────────────────────────────────


class _LaneProbe(DefinitionLookupMixin):
    """只实现两条道各自要用的查表：**走错道就一定露馅**（另一张表根本没实现）。"""

    def __init__(self, activity: dict | None, item: dict | None) -> None:
        self.activity = activity
        self.item = item
        self.tables: list[str] = []

    def get_definition(self, table: str, hash_id: int) -> dict | None:
        assert table == "DestinyActivityDefinition", f"活动 hash 走进了 {table}（不是活动道）"
        self.tables.append(table)
        return self.activity

    def get_item_info(self, item_hash: int) -> dict | None:
        self.tables.append("item")
        return self.item


def test_the_two_lanes_are_dispatched_by_definition_table() -> None:
    """`get_icon_url` 内部两条道：`item_hash=` 查物品表、`activity_hash=` 查活动表。

    这是"共用一个出口函数、内部两条道"的行为面证据 —— 静态守门只能证明"没人绕过出口"，
    证明不了"出口自己分得对"。三者一起才是完整的：

    1. `test_no_second_icon_url_constructor`：没人自己拼 URL；
    2. `test_activity_images_come_from_the_activity_lane`：活动字段只有一个读者；
    3. 本条：出口按**表**分道（活动 hash 去物品表 → `_LaneProbe` 直接断言失败）。
    """
    probe = _LaneProbe(
        activity={"pgcrImage": "/img/destiny_content/pgcr/raid_kings_fall.jpg",
                  "displayProperties": {"icon": "/common/destiny2_content/icons/raid.png"}},
        item={"icon": f"{BUNGIE_ORIGIN}/common/destiny2_content/icons/weapon.jpg"},
    )

    # 活动道：优先 `pgcrImage`（副本卡片要的是那张横幅，不是通用图标）
    icon = probe.get_icon_url(activity_hash=123)
    assert icon == f"{BUNGIE_ORIGIN}/img/destiny_content/pgcr/raid_kings_fall.jpg"
    assert is_bungie_activity_image(icon), "活动图的形状不对（前缀/源站）"
    assert probe.tables == ["DestinyActivityDefinition"]

    # 物品道：查物品表，**一次活动表都不查**
    probe.tables.clear()
    assert probe.get_icon_url(item_hash=456) == f"{BUNGIE_ORIGIN}/common/destiny2_content/icons/weapon.jpg"
    assert probe.tables == ["item"]

    # 占位横幅不算图：往下退到 displayProperties.icon（PvP 活动全是占位，见 manifest_lookup）
    placeholder = _LaneProbe(
        activity={"pgcrImage": "/img/theme/destiny/bgs/pgcrs/placeholder.jpg",
                  "displayProperties": {"icon": "/common/destiny2_content/icons/crucible.png"}},
        item=None,
    )
    assert placeholder.get_icon_url(activity_hash=1).endswith("/common/destiny2_content/icons/crucible.png")

    # 查不到定义 / 两张图都是哨兵 → 空串（缺值不编地址）
    assert _LaneProbe(activity=None, item=None).get_icon_url(activity_hash=1) == ""
    sentinels = _LaneProbe(
        activity={"pgcrImage": "/img/theme/destiny/bgs/pgcrs/placeholder.jpg",
                  "displayProperties": {"icon": "/img/misc/missing_icon_d2.png"}},
        item=None,
    )
    assert sentinels.get_icon_url(activity_hash=1) == ""
    assert _LaneProbe(activity=None, item=None).get_icon_url() == ""


# ── ⑤ "同一 intent 两条路一致"（体积口径，不是忘了）────────────────────────


def test_popularity_paths_agree_on_perk_rows() -> None:
    """同一个 `popularity` intent 的两条路，perk 行必须是**同一套键**。

    两条路：`weapon_popularity_service._enrich_entry`（第三方选取率快照，`data.popularity`）
    与 `weapon_local_data._popularity_summary`（本地归档摘要，挂在 `weapon.sources.popularity`）。
    2026-10-04 之前一条带 `icon_url`、一条不带 —— 同一句话问两次拿到两种行形状，
    这是**真不一致**（不是有意为之）。口径统一成"定义级 perk 行不带图"，
    这条守门就是钉住"下次别再漂"。
    """
    from destiny_mcp.services import weapon_local_data
    from destiny_mcp.services.weapon_popularity_service import WeaponPopularityService

    warnings: list[str] = []
    enriched = WeaponPopularityService._enrich_entry(
        "狂暴",
        [{"itemHash": 300, "name": "狂暴",
          "icon": f"{BUNGIE_ORIGIN}/common/destiny2_content/icons/perk.png"}],
        warnings,
        selection_rate=12.5,
    )
    summary = weapon_local_data._popularity_summary(
        {
            "perk_columns": [
                {"label": "特性 1",
                 "items": [{"name": "狂暴", "plug_hash": 300, "selection_rate": 12.5}]}
            ],
            "popular_combinations": [{"perks": [{"name": "狂暴", "plug_hash": 300}],
                                      "selection_rate": 30.0}],
            "source": {"kind": "fixture"},
        },
        {300: {"name": "狂暴"}},
    )
    row = summary["columns"][0]["items"][0]

    assert set(row) == set(enriched) == {"name", "plug_hash", "selection_rate"}, (
        "同一个 popularity intent 的两条路 perk 行形状不一致："
        f"本地摘要={sorted(row)}、选取率快照={sorted(enriched)}"
    )
    assert not warnings, f"唯一命中不该有 warning（别为了少个字段顺手加话术）：{warnings}"


def test_duplicates_perk_rows_stay_lean_by_volume() -> None:
    """`duplicates` 的 perk 行保持 `{name, slot}`：**体积口径，不是忘了**。

    真机基线每实例 1.81 KB，其中 `perks` **1.64 KB（91%）**；`limit=5` 的整包有 20 KB 闸
    （`docs/adr/021-default-exit-gives-rows-and-references.md:15`、
    `docs/plans/RESPONSE_PROJECTION_PLAN.md:12`）。补 `icon_url` 就是把闸顶破，
    所以这里正面钉住"它就是两个键" —— 要加图先回答体积账。
    组级 `icon_url` **有**（每组一个 URL，5 组约 0.5 KB），不受这条约束。
    """
    from destiny_mcp.services import inventory_analysis_service as analysis

    row = analysis._perk_row({"name": "狂暴", "plug_category": "v400.plugs.weapons.rarity.tier1",
                              "plug_hash": 300, "icon_url": f"{BUNGIE_ORIGIN}/x.png"})
    assert set(row) == {"name", "slot"}, f"duplicates 的 perk 行变胖了：{sorted(row)}"

    groups = [{"item_hash": 1, "name": "测试武器", "icon_url": "/common/destiny2_content/icons/w.png",
               "instances": [{"instance_id": "9", "perks": [
                   {"name": "狂暴", "plug_category": "v400.plugs.weapons.rarity.tier1"}]}]}]
    group = analysis.duplicate_rows(groups)[0]
    assert set(group["instances"][0]["perks"][0]) == {"name", "slot"}
    assert group["icon_url"].startswith(BUNGIE_ORIGIN), "组级图标必须在（每组一个 URL，不占体积）"


# ── ⑥ 社区配装的图：宁可不出图，不可出错图 ────────────────────────────────
#
# 用户 2026-10-05 的价值判断（原话）：**玩家看图比看名快 —— 很多时候是看了图标才想起名字。**
# 所以错图比没图坏得多。社区模板是**不可信参考**，它给的是一段散文，唯一的输入是一串名字；
# 名字→物品这一跳必须可核。下面五条把这件事钉成可执行断言 —— 每一条都对应一个**实测反例**
# （2026-10-05 在本机 Manifest 上跑出来的，不是设想）：
#
# | 反例 | 实测结果 | 断言 |
# | --- | --- | --- |
# | 「贪婪之握」（其实是**活动**名） | 精确名 0 命中 | `test_community_build_icons_never_guess` |
# | 「棱镜」（模板里的**分支**短标签） | 命中一件**武器皮肤**（`3373357626`，itemType=19） | `test_subclass_icon_requires_the_subclass_item_type` |
# | 「重型弹药搜寻者」3 个 hash | **2 种图**（`2867719094` vs `644105`/`554409585`） | `test_same_name_must_agree_on_the_icon` |
# | 「埃希恩记忆」套装 | `DestinyEquipableItemSetDefinition` 写着 `hasIcon: false` | `test_armor_set_row_never_carries_an_icon` |


class _StubManifest:
    """只实现 `_exact_definitions` 与 `agreed_icon` 真正读的两个方法。

    为什么用替身而不是真 Manifest：这条守门钉的是**规则**（"图不一致就不出图"），
    规则不该依赖本机那份 38k 行的 sqlite —— 干净克隆里没有它，那条测试会被跳过，
    而"跳过的守门"等于没有（`docs/testing/TESTING_CORPUS.md` 的分层口径）。
    真实反例的证据写在上面那张表和 `starside_build_icons` 的模块 docstring 里。
    """

    def __init__(self, items: list[dict]) -> None:
        self._items = items

    def search(self, name: str, limit: int = 0) -> list[dict]:
        wanted = name.strip().casefold()
        return [
            item for item in self._items
            if wanted in {str(item.get("name", "")).casefold(), str(item.get("nameEn", "")).casefold()}
        ]

    def get_item_info(self, item_hash: int) -> dict | None:
        for item in self._items:
            if int(item.get("itemHash", 0)) == int(item_hash):
                return {"icon": item.get("icon", "")}
        return None


def _item(item_hash: int, name: str, icon: str, **extra) -> dict:
    return {"itemHash": item_hash, "name": name, "nameEn": extra.pop("nameEn", name),
            "icon": icon, "classType": 3, **extra}


def test_same_name_must_agree_on_the_icon() -> None:
    """同名多版本：**图一致才出图**（实测反例：「重型弹药搜寻者」3 个 hash / 2 种图）。"""
    from destiny_mcp.services.starside_build_icons import agreed_icon

    agree = _StubManifest([
        _item(1, "驱逐引擎", "/common/destiny2_content/icons/same.jpg"),
        _item(2, "驱逐引擎", "/common/destiny2_content/icons/same.jpg"),
    ])
    assert agreed_icon(agree, [1, 2]).endswith("/icons/same.jpg"), "图一致时必须给图"

    differ = _StubManifest([
        _item(2867719094, "重型弹药搜寻者", "/common/destiny2_content/icons/a.png"),
        _item(644105, "重型弹药搜寻者", "/common/destiny2_content/icons/b.png"),
        _item(554409585, "重型弹药搜寻者", "/common/destiny2_content/icons/b.png"),
    ])
    assert agreed_icon(differ, [2867719094, 644105, 554409585]) == "", (
        "同名定义指到两张不同的图时必须给空串 —— 挑一个就是猜，看图记名字的人会记错"
    )
    assert agreed_icon(differ, []) == "", "没有定义就是没有图"


def test_subclass_icon_requires_the_subclass_item_type() -> None:
    """裸分支标签**必须**带 `item_type=16` 才解析：实测「棱镜」命中的是一件**武器皮肤**。

    这条走的是**模块自己的 `_subclass_row`**（不是手工传 `item_type=`）—— 注入验证时发现：
    如果测试自己把过滤写进调用，那把模块里的过滤删掉它照样绿。替身刻意**只留**那件皮肤
    （没有名字能对上的子职业），于是"有过滤 → 空串、没过滤 → 皮肤图"这一对差别才咬得住。
    """
    from destiny_mcp.services.starside_build_icons import build_visuals

    manifest = _StubManifest([
        _item(3373357626, "棱镜", "/common/destiny2_content/icons/skin.jpg", itemType=19),
    ])
    build = {"class": {"name": "猎人", "id": "hunter"}, "subclass": "棱镜",
             "weapons": [], "armor": {}}
    visuals = build_visuals(manifest, build)

    assert visuals["subclass"] == {"name": "棱镜", "icon_url": ""}, (
        "裸标签只命中武器皮肤时必须空串 —— 去掉 item_type=16 就会画出那张皮肤图"
    )


def test_community_build_icons_never_guess() -> None:
    """`build_visuals` 的三条出口行为：解析不到→空串、套装→空串、子职业走类型过滤。"""
    from destiny_mcp.services.starside_build_icons import build_visuals

    manifest = _StubManifest([
        _item(2376481550, "混乱无序", "/common/destiny2_content/icons/anarchy.jpg", itemType=3, tier=6),
        _item(4282591831, "棱镜猎人", "/common/destiny2_content/icons/subclass.png", itemType=16, classType=1),
        _item(3373357626, "棱镜", "/common/destiny2_content/icons/skin.jpg", itemType=19),
    ])
    build = {
        "class": {"name": "猎人", "id": "hunter"},
        "subclass": "棱镜",
        "weapons": [
            {"name": "混乱无序", "tier": "exotic"},
            {"name": "斗牛士 64", "tier": "legendary"},      # 实测精确名 0 命中
        ],
        "armor": {"exotic": "", "set_requirements": [{"name": "贪婪之握", "count": 4}]},
    }
    visuals = build_visuals(manifest, build)

    assert visuals["weapons"][0]["icon_url"].endswith("/icons/anarchy.jpg")
    assert visuals["weapons"][1]["icon_url"] == "", "解析不到武器就必须空串（不出图，也不编）"
    assert visuals["armor"]["set"] == {"name": "贪婪之握", "icon_url": ""}, (
        "套装行：名字照给、图一律空串（Manifest 的套装定义 hasIcon:false）"
    )
    assert visuals["subclass"]["icon_url"].endswith("/icons/subclass.png"), (
        "子职业必须走「标签+职业名」+ item_type=16，不能拿裸标签命中的武器皮肤"
    )
    assert "exotic" not in visuals["armor"], "模板没写异域护甲就不该凭空造一行"


def test_armor_set_row_never_carries_an_icon() -> None:
    """套装行一律空串：`DestinyEquipableItemSetDefinition` 里有 `hasIcon: false`。

    实测 `741162535`（埃希恩记忆）的原始定义就是 `"iconHash":0,"hasIcon":false` ——
    这不是"还没做"，是**没有这张图**。**两条路都要钉**：`build_visuals` 的
    `armor.set`（列表/详情共用）与 `with_requirement_icons` 的 `armor_set` 行
    —— 只钉一条的话，注入另一条守门照样绿（第一版就是这样漏的）。
    """
    from destiny_mcp.services.starside_build_icons import (
        build_visuals,
        with_requirement_icons,
    )

    manifest = _StubManifest([_item(1, "某武器", "/common/destiny2_content/icons/w.jpg")])

    visuals = build_visuals(manifest, {
        "class": {"name": "猎人", "id": "hunter"}, "subclass": "",
        "weapons": [], "armor": {"set_requirements": [{"name": "埃希恩记忆", "count": 4}]},
    })
    assert visuals["armor"]["set"] == {"name": "埃希恩记忆", "icon_url": ""}, (
        "套装的图必须空串，不许拿任何一张图顶上"
    )

    validation = {
        "requirements": [
            {"kind": "armor_set", "name": "埃希恩记忆", "status": "resolved",
             "definitions": [{"set_hash": 741162535, "set_name": "埃希恩记忆"}]},
            {"kind": "weapon", "name": "某武器", "status": "resolved",
             "definitions": [{"item_hash": 1}],
             "perk_resolutions": [{"kind": "weapon_perk", "name": "某 perk",
                                   "definitions": [{"item_hash": 1}]}]},
        ]
    }
    with_requirement_icons(manifest, validation)
    assert validation["requirements"][0]["icon_url"] == "", "套装没有图，不许拿别的顶"
    assert validation["requirements"][1]["icon_url"].endswith("/icons/w.jpg")
    assert validation["requirements"][1]["perk_resolutions"][0]["icon_url"].endswith("/icons/w.jpg"), (
        "perk 行也要有图（详情那一档是「全部显示」）"
    )
