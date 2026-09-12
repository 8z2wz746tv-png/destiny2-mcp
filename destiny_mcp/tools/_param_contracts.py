"""参数与 intent 的对应关系：没人认的参数直接拒绝，而不是静默忽略。

八个 assistant 各自只有一个宽签名，任何 intent 都能收到全部参数。结果是
`inventory_assistant(intent="get", item_instance_id="123")` 会安静地丢掉
item_instance_id，然后像没事一样返回整包清单 —— 信封完整、内容答非所问，
调用方没有任何线索发现自己问错了入口。

这张表登记**每个参数由哪些 intent 真正读取**。没认领的 intent 收到它，
就在调用服务层之前返回 `ignored_parameter`，消息里直接列出认领者，
能给出替代入口的还会带上 `next_actions`。

表的判据是「行为上读没读」而不是「签名里有没有」：一个参数如果传了不改变
任何结果，就算代码里被引用过（比如预分发里算了一下 `resolved` 却没人用），
也不算认领。宁可多拦一次，也不要留一个安静的错答案。

有意不登记的三个（不是遗漏，是决定）：

- `confirmed`：写入确认门槛，由 `WRITE_INTENTS` 和确认响应共同保证。
  读 intent 收到它被忽略不会改变任何结果；登记反而会误伤那些每次都把
  `confirmed=true` 一起发过来的客户端。
- `community_build_id`：传给非 community intent 时，代码里已经有一段
  更贴切的 `community_template_not_executable` 说明，不需要这里抢答。
- `ctx`：运行时注入，不是调用方参数。

参数说明（进 schema 的那句话）在 `_param_docs.py`；两者分工是「说明是建议、
拦截是保证」。本模块的 `render_parameter_table()` 生成 skill 文档里的参数表，
由 `tests/test_skill_contracts.py` 保证文档与这里逐字一致。
"""

from __future__ import annotations

import sys
from collections.abc import Callable, Mapping
from functools import wraps
from inspect import Parameter, signature
from typing import Any, Literal, NamedTuple, get_args, get_origin

from ._requests import (
    ActivityIntent,
    BuildIntent,
    InventoryIntent,
    LoadoutIntent,
    PlayerIntent,
    SubclassIntent,
    WeaponIntent,
    WorldIntent,
)
from ._responses import error_response

_MISSING = object()


class ParameterContract(NamedTuple):
    """一个参数的归属：谁真正读它，被拒时怎么改。"""

    intents: frozenset[str]
    hint: str = ""  # 可选的额外说明；不写就只报认领者名单
    suggestion: tuple[str, str] | None = None  # 可选：(工具, intent) 替代调用


def _all(intent_type: object) -> frozenset[str]:
    """某个工具的全部 intent（含中文别名）。"""
    return frozenset(str(value) for value in get_args(intent_type))


def _only(*intents: str) -> frozenset[str]:
    return frozenset(intents)


def _contract(
    intents: frozenset[str],
    *,
    hint: str = "",
    suggestion: tuple[str, str] | None = None,
) -> ParameterContract:
    return ParameterContract(intents, hint, suggestion)


# ── inventory_assistant 的重复意图名，写一次 ────────────────────────────────
_INV_DUPLICATES = ("duplicates", "duplicate_weapons", "find_duplicates", "重复武器")
_INV_SUMMARY = ("summary", "summarize", "概况")
_INV_GET = ("get", "inventory", "list")
_INV_TYPE = ("type", "search_type")
# ── weapon_assistant ────────────────────────────────────────────────────────
_W_CATALOG = ("catalog", "search_catalog", "all_weapons", "global", "search_all")
_W_ROLL_FILTERS = ("filter_rolls", *_W_CATALOG)
_W_POPULARITY = (
    "popularity", "selection_rates", "perk_selection", "selection", "usage_rates",
)
# ── build_assistant ─────────────────────────────────────────────────────────
_B_CRAFT = ("recommend", "find", "analyze", "farm_target")
_B_COMMUNITY = ("community", "community_build", "starside")
# ── activity_assistant ──────────────────────────────────────────────────────
_A_WEAPON_HISTORY = ("weapon_history", "weapons", "weapon_usage", "weapon_leaderboard")
_A_AGGREGATE = ("aggregate", "activity_aggregate", "activity_stats")
_A_LEADERBOARD = ("leaderboards", "leaderboard")


PARAMETER_OWNERS: dict[tuple[str, str], ParameterContract] = {
    # ══ player_assistant ═══════════════════════════════════════════════════
    ("player_assistant", "player_name"): _contract(
        _only("profile", "get_profile", "角色", "档案", "search", "search_player"),
        hint='名字记不全时用 intent="find" 并把片段传给 name_prefix，而不是 player_name。',
    ),
    ("player_assistant", "name_prefix"): _contract(
        _only("find", "find_players", "fuzzy"),
        hint=(
            '名字记不全时用 intent="find" 传 name_prefix（返回候选，含 名字#数字 与 has_more）；'
            '精确查人用 intent="search" 传完整 player_name。'
        ),
        suggestion=("player_assistant", "find"),
    ),
    # ══ inventory_assistant ════════════════════════════════════════════════
    ("inventory_assistant", "player_name"): _contract(_all(InventoryIntent)),
    ("inventory_assistant", "location"): _contract(
        _only(*_INV_SUMMARY, *_INV_GET, "search", "find_item", *_INV_TYPE)
    ),
    ("inventory_assistant", "item_name"): _contract(
        _only("search", "find_item", *_INV_DUPLICATES, "move"),
        hint='按名字找东西用 intent="search"；查重复武器用 intent="duplicates"；移动物品用 intent="move"。',
        suggestion=("inventory_assistant", "search"),
    ),
    ("inventory_assistant", "item_type"): _contract(
        _only(*_INV_SUMMARY, *_INV_GET, *_INV_TYPE),
        hint='按类型查询用 intent="type"；按名字查用 intent="search"；duplicates 用的是 type_name。',
        suggestion=("inventory_assistant", "type"),
    ),
    ("inventory_assistant", "armor_slot"): _contract(
        _only(*_INV_GET), hint="部位过滤只作用于护甲，且只有列出清单的 intent 读它。"
    ),
    ("inventory_assistant", "rarity"): _contract(
        _only(*_INV_GET), hint="稀有度过滤只有列出清单的 intent 读它。"
    ),
    ("inventory_assistant", "type_name"): _contract(
        _only(*_INV_DUPLICATES, *_INV_TYPE),
        hint='按类型查询用 intent="type"；查某类型的重复武器用 intent="duplicates"。',
        suggestion=("inventory_assistant", "type"),
    ),
    ("inventory_assistant", "item_instance_id"): _contract(
        _only(
            "move", "transfer", "equip", "pull_postmaster",
            "lock", "track_quest", "quest_tracking",
        ),
        hint=(
            '读某个副本当前 Perk 用 weapon_assistant(intent="compare", weapon_name=..., '
            'item_instance_id=...)；移动/装备/锁定/取回分别用 intent="move"/"equip"/"lock"/"pull_postmaster"。'
        ),
        suggestion=("weapon_assistant", "compare"),
    ),
    ("inventory_assistant", "item_instance_ids"): _contract(
        _only("equip_many", "equip_items"),
        hint='只有批量装备 intent="equip_many" 使用这个参数；单个副本用 item_instance_id。',
        suggestion=("inventory_assistant", "equip_many"),
    ),
    ("inventory_assistant", "destination"): _contract(
        _only("move"), hint='移动物品用 intent="move"；转移给别的角色用 intent="transfer"。',
        suggestion=("inventory_assistant", "move"),
    ),
    ("inventory_assistant", "equip"): _contract(
        _only("move"),
        hint='"移动完顺便装上"用 intent="move" + equip=true；只装备不移动用 intent="equip"。',
        suggestion=("inventory_assistant", "move"),
    ),
    ("inventory_assistant", "from_character"): _contract(
        _only("move", "transfer"), hint="只有 move 和 transfer 关心物品现在在哪个角色身上。"
    ),
    ("inventory_assistant", "to_character"): _contract(
        _only("transfer"),
        hint='按实例 ID 换角色用 intent="transfer"；按名字移动用 intent="move"。',
        suggestion=("inventory_assistant", "transfer"),
    ),
    ("inventory_assistant", "character"): _contract(
        _only(
            "equip", "equip_many", "equip_items", "pull_postmaster",
            "lock", "track_quest", "quest_tracking",
        ),
        hint="只有装备、批量装备、取回、锁定、任务追踪这几个写入 intent 需要指定角色。",
    ),
    ("inventory_assistant", "locked"): _contract(
        _only("lock"), hint='锁定/解锁用 intent="lock"，locked=true 锁、false 解锁。',
        suggestion=("inventory_assistant", "lock"),
    ),
    ("inventory_assistant", "tracked"): _contract(
        _only("track_quest", "quest_tracking"),
        hint='追踪/取消追踪用 intent="track_quest"，tracked=false 表示取消。',
        suggestion=("inventory_assistant", "track_quest"),
    ),
    ("inventory_assistant", "limit"): _contract(
        _only(*_INV_SUMMARY, *_INV_DUPLICATES, *_INV_GET),
        hint=(
            'list（get/inventory）默认 100 件、可传 limit 调整；要数量概况用 intent="summary"。'
            '被截断时响应带 total_items/returned_items/truncated/next_offset。'
        ),
        suggestion=("inventory_assistant", "summary"),
    ),
    ("inventory_assistant", "offset"): _contract(
        _only(*_INV_DUPLICATES, *_INV_GET),
        hint='list 与 duplicates 都支持翻页：用响应里的 next_offset 继续读。',
        suggestion=("inventory_assistant", "get"),
    ),
    # ══ weapon_assistant ═══════════════════════════════════════════════════
    ("weapon_assistant", "player_name"): _contract(
        _only("analyze", "compare", "compare_duplicates", "type", "filter_rolls"),
        hint="只有读账号的武器 intent 需要玩家名；纯定义查询（catalog/info/stats/perk_pool 等）与账号无关。",
    ),
    ("weapon_assistant", "weapon_name"): _contract(
        _only(*(i for i in _all(WeaponIntent) if i not in {"type", "perk_description"})),
        hint='按武器类型查要传 weapon_type（intent="type"，不是 weapon_name）；查单个 Perk 用 perk_name（intent="perk_description"）。',
    ),
    ("weapon_assistant", "weapon_type"): _contract(
        _only(*_W_ROLL_FILTERS, "type"),
        hint='按类型列举用 intent="type"；按类型在全量定义里筛用 intent="catalog"。',
        suggestion=("weapon_assistant", "catalog"),
    ),
    ("weapon_assistant", "perk_name"): _contract(
        _only(*_W_ROLL_FILTERS, "perk_description", "community"),
        hint='问 Perk 效果用 intent="perk_description"；找能滚出它的枪用 catalog（全量）或 filter_rolls（账号）。',
        suggestion=("weapon_assistant", "catalog"),
    ),
    ("weapon_assistant", "required_perks"): _contract(
        _only(*_W_ROLL_FILTERS), hint="只有 catalog 和 filter_rolls 支持按 Perk 筛选。",
        suggestion=("weapon_assistant", "filter_rolls"),
    ),
    ("weapon_assistant", "any_perks"): _contract(
        _only(*_W_ROLL_FILTERS), hint="只有 catalog 和 filter_rolls 支持按 Perk 筛选。",
        suggestion=("weapon_assistant", "filter_rolls"),
    ),
    ("weapon_assistant", "excluded_perks"): _contract(
        _only(*_W_ROLL_FILTERS), hint="只有 catalog 和 filter_rolls 支持按 Perk 排除。",
        suggestion=("weapon_assistant", "filter_rolls"),
    ),
    ("weapon_assistant", "location"): _contract(
        _only("filter_rolls"), hint='按位置筛持有副本只有 intent="filter_rolls" 支持。',
        suggestion=("weapon_assistant", "filter_rolls"),
    ),
    ("weapon_assistant", "include_inventory"): _contract(
        _only("analyze", "filter_rolls"),
        hint='filter_rolls + include_inventory=false 才是"只查全量定义"；analyze 用它决定要不要读账号。',
        suggestion=("weapon_assistant", "filter_rolls"),
    ),
    ("weapon_assistant", "item_instance_id"): _contract(
        _only("compare", "compare_duplicates"),
        hint='对比同名副本用 intent="compare"；只看武器本体用 intent="analyze"（不接受副本 ID）。',
        suggestion=("weapon_assistant", "compare"),
    ),
    ("weapon_assistant", "limit"): _contract(
        _only(*_W_ROLL_FILTERS, "type", "community"),
        hint="catalog、filter_rolls、type 和 community 支持限量；type 会另给 total_weapons/truncated。",
        suggestion=("weapon_assistant", "filter_rolls"),
    ),
    ("weapon_assistant", "knowledge_id"): _contract(
        _only("community"), hint='读本地资料详情用 intent="community" 并给 knowledge_id。',
        suggestion=("weapon_assistant", "community"),
    ),
    ("weapon_assistant", "community_section"): _contract(
        _only("community"), hint='只有 intent="community" 读它（text/tables/links）。',
        suggestion=("weapon_assistant", "community"),
    ),
    ("weapon_assistant", "offset"): _contract(
        _only("community"), hint='只有 intent="community" 支持翻页。',
        suggestion=("weapon_assistant", "community"),
    ),
    # ══ build_assistant ════════════════════════════════════════════════════
    ("build_assistant", "player_name"): _contract(
        _only(*_B_CRAFT, *_B_COMMUNITY, "equip_build"),
        hint="护甲模组、异域护甲列表、套装效果都是定义查询，不需要玩家名。",
    ),
    ("build_assistant", "character"): _contract(
        _only(*_B_CRAFT, *_B_COMMUNITY, "equip_build", "exotic_armor"),
        hint="求解、社区配装、装备候选、异域护甲列表都按角色；模组和套装效果是全职业定义。",
    ),
    ("build_assistant", "exotic_name"): _contract(
        _only(*_B_CRAFT, "exotic_armor"),
        hint='指定金装的求解用 intent="recommend"/"find"/"analyze"/"farm_target"；查金装定义用 intent="exotic_armor"。',
        suggestion=("build_assistant", "exotic_armor"),
    ),
    ("build_assistant", "confirmed_exotic_hash"): _contract(
        _only(*_B_CRAFT), hint="金装确认只在求解类 intent 上用；第一次查询不要填。"
    ),
    ("build_assistant", "exotic_confirmation_token"): _contract(
        _only(*_B_CRAFT), hint="金装确认凭据只在求解类 intent 上用；第一次查询不要填。"
    ),
    ("build_assistant", "weapons_target"): _contract(
        _only(*_B_CRAFT), hint='属性目标是求解类 intent 的硬约束；社区配装不受它影响。',
        suggestion=("build_assistant", "recommend"),
    ),
    ("build_assistant", "health_target"): _contract(
        _only(*_B_CRAFT), hint='属性目标是求解类 intent 的硬约束；社区配装不受它影响。',
        suggestion=("build_assistant", "recommend"),
    ),
    ("build_assistant", "class_target"): _contract(
        _only(*_B_CRAFT), hint='属性目标是求解类 intent 的硬约束；社区配装不受它影响。',
        suggestion=("build_assistant", "recommend"),
    ),
    ("build_assistant", "grenade_target"): _contract(
        _only(*_B_CRAFT), hint='属性目标是求解类 intent 的硬约束；社区配装不受它影响。',
        suggestion=("build_assistant", "recommend"),
    ),
    ("build_assistant", "melee_target"): _contract(
        _only(*_B_CRAFT), hint='属性目标是求解类 intent 的硬约束（"力量"要传给 melee_target）。',
        suggestion=("build_assistant", "recommend"),
    ),
    ("build_assistant", "super_target"): _contract(
        _only(*_B_CRAFT), hint='属性目标是求解类 intent 的硬约束；社区配装不受它影响。',
        suggestion=("build_assistant", "recommend"),
    ),
    ("build_assistant", "fragment_names"): _contract(
        _only(*_B_CRAFT), hint='碎片计入配装只在求解类 intent 上生效。',
        suggestion=("build_assistant", "recommend"),
    ),
    ("build_assistant", "include_subclass_fragment"): _contract(
        _only(*_B_CRAFT), hint='是否计入当前子职业属性只在求解类 intent 上生效。',
        suggestion=("build_assistant", "recommend"),
    ),
    ("build_assistant", "priority_stats"): _contract(
        _only(*_B_CRAFT), hint='优先级排序只在求解类 intent 上生效。',
        suggestion=("build_assistant", "recommend"),
    ),
    ("build_assistant", "priority_stat"): _contract(
        _only(*_B_CRAFT, "armor_mods"),
        hint='筛模组用 intent="armor_mods"；求解里的优先级用 priority_stats。',
    ),
    ("build_assistant", "set_bonus_name"): _contract(
        _only(*_B_CRAFT, "set_bonus"),
        hint='查套装效果用 intent="set_bonus"；把套装当求解约束用 intent="farm_target"/"recommend"。',
        suggestion=("build_assistant", "set_bonus"),
    ),
    ("build_assistant", "set_bonus_count"): _contract(
        _only(*_B_CRAFT), hint='套装件数是求解约束，只在求解类 intent 上生效。',
        suggestion=("build_assistant", "recommend"),
    ),
    ("build_assistant", "replacement_slot"): _contract(
        _only("farm_target"), hint='反推单件用 intent="farm_target" + replacement_slot。',
        suggestion=("build_assistant", "farm_target"),
    ),
    ("build_assistant", "baseline"): _contract(
        _only("farm_target"), hint='基线只被 intent="farm_target" 读（baseline="equipped" 是默认值）。',
        suggestion=("build_assistant", "farm_target"),
    ),
    ("build_assistant", "max_replacements"): _contract(
        _only("farm_target"), hint='允许替换几件只被 intent="farm_target" 读。',
        suggestion=("build_assistant", "farm_target"),
    ),
    ("build_assistant", "canonical_build"): _contract(
        _only("equip_build"),
        hint='canonical_build 只能回传给 intent="equip_build"，而且必须是服务端签发的候选。',
        suggestion=("build_assistant", "equip_build"),
    ),
    ("build_assistant", "top_n"): _contract(
        _only(*_B_CRAFT, *_B_COMMUNITY), hint="求解返回几套、社区搜索返回几条。"
    ),
    ("build_assistant", "scenario"): _contract(
        _only(*_B_COMMUNITY), hint='场景筛选只在社区配装搜索 intent="community" 上生效。',
        suggestion=("build_assistant", "community"),
    ),
    ("build_assistant", "category"): _contract(
        _only(*_B_COMMUNITY), hint='分类筛选只在社区配装搜索 intent="community" 上生效。',
        suggestion=("build_assistant", "community"),
    ),
    ("build_assistant", "query"): _contract(
        _only(*_B_COMMUNITY), hint='关键词搜索只在社区配装 intent="community" 上用。',
        suggestion=("build_assistant", "community"),
    ),
    ("build_assistant", "include_inventory"): _contract(
        _only(*_B_COMMUNITY),
        hint="只有社区配装 intent 需要它决定要不要读账号；求解类 intent 一定读账号。",
        suggestion=("build_assistant", "community"),
    ),
    ("build_assistant", "offset"): _contract(
        _only(*_B_COMMUNITY), hint='只有社区配装 intent="community" 支持翻页。',
        suggestion=("build_assistant", "community"),
    ),
    # ══ loadout_assistant ══════════════════════════════════════════════════
    ("loadout_assistant", "player_name"): _contract(
        _only(
            "list", "get", "save", "equip_loadout",
            "snapshot_official", "update_official_identifiers", "clear_official",
        ),
        hint="删配装、装备配装、搜官方槽位标识不需要玩家名（前者按 ID，后者是本地搜索）。",
    ),
    ("loadout_assistant", "limit"): _contract(
        _only("list", "get"),
        hint=(
            "配装（list/get）默认 5 套、可用 limit 调整；被截断时响应带 "
            "total_loadouts/returned_loadouts/truncated/next_offset。"
        ),
        suggestion=("loadout_assistant", "list"),
    ),
    ("loadout_assistant", "offset"): _contract(
        _only("list", "get"),
        hint="配装翻页：用响应里的 next_offset 继续读。",
        suggestion=("loadout_assistant", "list"),
    ),
    ("loadout_assistant", "character"): _contract(
        _only(
            "list", "get", "save",
            "snapshot_official", "update_official_identifiers", "clear_official",
        ),
        hint="按 ID 删配装/装备配装，以及搜标识，都不按角色过滤。",
    ),
    ("loadout_assistant", "loadout_id"): _contract(
        _only("delete", "equip_loadout"),
        hint=(
            'list/get 只按 character 过滤并返回全部配装，不接受 loadout_id；'
            "要哪一套请从返回结果里按 ID 或名字挑出来。"
        ),
    ),
    ("loadout_assistant", "name"): _contract(
        _only("save"), hint='保存配装用 intent="save"，名字传给 name。',
        suggestion=("loadout_assistant", "save"),
    ),
    ("loadout_assistant", "notes"): _contract(
        _only("save"), hint='备注只在保存配装 intent="save" 时写入。',
        suggestion=("loadout_assistant", "save"),
    ),
    ("loadout_assistant", "slot_number"): _contract(
        _only("snapshot_official", "update_official_identifiers", "clear_official"),
        hint=(
            'list/get 返回全部配装（含官方槽位），不接受 slot_number；'
            "要指定槽位请从结果里挑，只有快照/改名/清空这三个写入 intent 读槽位号。"
        ),
    ),
    ("loadout_assistant", "name_hash"): _contract(
        _only("snapshot_official", "update_official_identifiers"),
        hint="名称 hash 只在存快照或改标识时使用；先 search_identifiers 拿 hash。",
    ),
    ("loadout_assistant", "icon_hash"): _contract(
        _only("snapshot_official", "update_official_identifiers"),
        hint="图标 hash 只在存快照或改标识时使用；先 search_identifiers 拿 hash。",
    ),
    ("loadout_assistant", "color_hash"): _contract(
        _only("snapshot_official", "update_official_identifiers"),
        hint="颜色 hash 只在存快照或改标识时使用；先 search_identifiers 拿 hash。",
    ),
    ("loadout_assistant", "kind"): _contract(
        _only("search_identifiers"),
        hint='只搜官方槽位标识用 intent="search_identifiers"；列配装用 intent="list"。',
        suggestion=("loadout_assistant", "search_identifiers"),
    ),
    ("loadout_assistant", "query"): _contract(
        _only("search_identifiers"),
        hint='按名字搜官方槽位标识用 intent="search_identifiers"。',
        suggestion=("loadout_assistant", "search_identifiers"),
    ),
    # ══ subclass_assistant ═════════════════════════════════════════════════
    ("subclass_assistant", "player_name"): _contract(
        _only("get", "subclass", "modify", "equip_artifact_mod"),
        hint="看当前配置、改技能、装神器模组才需要玩家名；选项/碎片/神器定义都是全量数据。",
    ),
    ("subclass_assistant", "character"): _contract(
        _only("get", "subclass", "modify", "options", "equip_artifact_mod", "community"),
        hint="技能与碎片的定义列表按角色过滤；modify/equip_artifact_mod 需要它作为写入目标。",
    ),
    ("subclass_assistant", "element"): _contract(
        _only("options", "fragments", "community"),
        hint='按元素列可选项用 intent="options"，列碎片用 intent="fragments"。',
        suggestion=("subclass_assistant", "options"),
    ),
    ("subclass_assistant", "component"): _contract(
        _only("options"), hint='技能类别过滤（超能/手雷/近战等）只在 intent="options" 上生效。',
        suggestion=("subclass_assistant", "options"),
    ),
    ("subclass_assistant", "fragment_name"): _contract(
        _only("fragment_details", "community"),
        hint='查某个碎片的效果用 intent="fragment_details"。',
        suggestion=("subclass_assistant", "fragment_details"),
    ),
    ("subclass_assistant", "artifact_name"): _contract(
        _only("artifact", "community"),
        hint='查赛季神器用 intent="artifact"；查模组要传 artifact_mod_hash。',
        suggestion=("subclass_assistant", "artifact"),
    ),
    ("subclass_assistant", "artifact_mod_hash"): _contract(
        _only("artifact_mod", "equip_artifact_mod"),
        hint='查模组详情用 intent="artifact_mod"；装备模组用 intent="equip_artifact_mod"。',
        suggestion=("subclass_assistant", "artifact_mod"),
    ),
    ("subclass_assistant", "changes"): _contract(
        _only("modify"), hint='改技能用 intent="modify"，改动放进 changes。',
        suggestion=("subclass_assistant", "modify"),
    ),
    ("subclass_assistant", "query"): _contract(
        _only("community"), hint='关键词搜索只在社区职业资料 intent="community" 上用。',
        suggestion=("subclass_assistant", "community"),
    ),
    ("subclass_assistant", "limit"): _contract(
        _only("community"), hint='只有社区职业资料 intent="community" 支持限量。',
        suggestion=("subclass_assistant", "community"),
    ),
    ("subclass_assistant", "knowledge_id"): _contract(
        _only("community"), hint='读资料详情用 intent="community" 并给 knowledge_id。',
        suggestion=("subclass_assistant", "community"),
    ),
    ("subclass_assistant", "community_section"): _contract(
        _only("community"), hint='只有 intent="community" 读它（text/tables/links）。',
        suggestion=("subclass_assistant", "community"),
    ),
    ("subclass_assistant", "offset"): _contract(
        _only("community"), hint='只有 intent="community" 支持翻页。',
        suggestion=("subclass_assistant", "community"),
    ),
    # ══ activity_assistant ═════════════════════════════════════════════════
    ("activity_assistant", "player_name"): _contract(
        _only(*(i for i in _all(ActivityIntent) if i not in {"pgcr", "clan_leaderboards", "community"})),
        hint="单场结算只看活动 ID，公会榜只看 group_id，社区资料与账号无关。",
    ),
    ("activity_assistant", "character"): _contract(
        _only(*(i for i in _all(ActivityIntent) if i not in {"pgcr", "clan_leaderboards"})),
        hint="单场结算和公会榜不按角色过滤。",
    ),
    ("activity_assistant", "mode"): _contract(
        _only("history", *_A_LEADERBOARD, "clan_leaderboards", "community"),
        hint="模式过滤被 history、排行榜和社区资料读；生涯统计和武器历史不分模式。",
        suggestion=("activity_assistant", "history"),
    ),
    ("activity_assistant", "activity_id"): _contract(
        _only("pgcr"),
        hint='看单场结算用 intent="pgcr" + activity_id；看最近几场用 intent="history"。',
        suggestion=("activity_assistant", "pgcr"),
    ),
    ("activity_assistant", "group_id"): _contract(
        _only("clan_leaderboards"),
        hint='公会排行榜用 intent="clan_leaderboards" 并必须给 group_id；个人榜用 intent="leaderboards"。',
        suggestion=("activity_assistant", "clan_leaderboards"),
    ),
    ("activity_assistant", "statid"): _contract(
        _only(*_A_LEADERBOARD, "clan_leaderboards"),
        hint='榜单指标只被排行榜类 intent 读；生涯统计用 intent="stats"。',
        suggestion=("activity_assistant", "leaderboards"),
    ),
    ("activity_assistant", "maxtop"): _contract(
        _only(*_A_LEADERBOARD, "clan_leaderboards"),
        hint="榜单取前几名只被排行榜类 intent 读。",
        suggestion=("activity_assistant", "leaderboards"),
    ),
    ("activity_assistant", "count"): _contract(
        _only("history", *_A_WEAPON_HISTORY, *_A_AGGREGATE, "community"),
        hint='要几场/几条只被 history、武器历史、聚合统计和社区资料读；生涯统计不分条数。',
        suggestion=("activity_assistant", "history"),
    ),
    ("activity_assistant", "query"): _contract(
        _only("community"), hint='关键词搜索只在社区活动资料 intent="community" 上用。',
        suggestion=("activity_assistant", "community"),
    ),
    ("activity_assistant", "knowledge_id"): _contract(
        _only("community"), hint='读资料详情用 intent="community" 并给 knowledge_id。',
        suggestion=("activity_assistant", "community"),
    ),
    ("activity_assistant", "community_section"): _contract(
        _only("community"), hint='只有 intent="community" 读它（text/tables/links）。',
        suggestion=("activity_assistant", "community"),
    ),
    ("activity_assistant", "offset"): _contract(
        _only("community"), hint='只有 intent="community" 支持翻页。',
        suggestion=("activity_assistant", "community"),
    ),
    # ══ world_assistant ════════════════════════════════════════════════════
    ("world_assistant", "player_name"): _contract(
        _only("vendor", "collectible_node", "collectible_item"),
        hint="周常、搜节点、社区资料与账号无关；商人库存和收藏品状态才需要玩家名。",
    ),
    ("world_assistant", "character"): _contract(
        _only("vendor", "collectible_node", "collectible_item", "community"),
        hint="周常和搜节点不按角色过滤。",
    ),
    ("world_assistant", "item_name"): _contract(
        _only("community", "collectible_item"),
        hint=(
            '查收藏品状态用 intent="collectible_item"；查自己有没有这件东西用 '
            'inventory_assistant(intent="search")；intent="vendor" 返回整个货架，不按物品过滤。'
        ),
        suggestion=("inventory_assistant", "search"),
    ),
    ("world_assistant", "vendor_name"): _contract(
        _only("vendor", "community"),
        hint='看某个商人用 intent="vendor"；社区资料搜索也读它。',
        suggestion=("world_assistant", "vendor"),
    ),
    ("world_assistant", "query"): _contract(
        _only("search_collectible_nodes", "community"),
        hint='搜收藏品节点用 intent="search_collectible_nodes"；搜社区资料用 intent="community"。',
        suggestion=("world_assistant", "search_collectible_nodes"),
    ),
    ("world_assistant", "collectible_node_hash"): _contract(
        _only("collectible_node"),
        hint='查节点解锁状态用 intent="collectible_node" + collectible_node_hash；'
             '只有节点名字时先用 intent="search_collectible_nodes" 找 hash。',
        suggestion=("world_assistant", "collectible_node"),
    ),
    ("world_assistant", "include_invisible"): _contract(
        _only("collectible_node"), hint='是否包含隐藏节点只在 intent="collectible_node" 上生效。',
        suggestion=("world_assistant", "collectible_node"),
    ),
    ("world_assistant", "limit"): _contract(
        _only("weekly", "vendor", "search_collectible_nodes", "collectible_node", "collectible_item", "community"),
        hint="完整周常（weekly_full）不读 limit；vendor 用它限制商人数（菜单）或每个商人的商品数（详情）。",
    ),
    ("world_assistant", "community_category"): _contract(
        _only("community"), hint='只有 intent="community" 能跨分类搜索。',
        suggestion=("world_assistant", "community"),
    ),
    ("world_assistant", "knowledge_id"): _contract(
        _only("community"), hint='读资料详情用 intent="community" 并给 knowledge_id。',
        suggestion=("world_assistant", "community"),
    ),
    ("world_assistant", "community_section"): _contract(
        _only("community"), hint='只有 intent="community" 读它（text/tables/links）。',
        suggestion=("world_assistant", "community"),
    ),
    ("world_assistant", "offset"): _contract(
        _only("community"), hint='只有 intent="community" 支持翻页。',
        suggestion=("world_assistant", "community"),
    ),
}


def _is_supplied(value: Any, default: Any = _MISSING) -> bool:
    """这个参数算不算「调用方真的传了」。

    空值（None/""/0/False）不算传：客户端把 schema 默认值一起发过来（confirmed=false、
    item_name=""、offset=0）不应该被拒。

    因此签名默认值只能是空值 —— 有含义的默认值（limit 的 12、locked 的 true、
    slot_number 的 1）必须改成 None 哨兵、把默认值搬进函数体，否则显式传默认值
    会被当成"没传"而静默吞掉。规则与回归见 tests/test_parameter_sentinels 一节
    （在 tests/test_ignored_parameters.py 末尾）。
    """
    if default is not _MISSING and value == default:
        return False
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, (list, tuple, set, dict)):
        return bool(value)
    return True


def intent_accepts_parameter(tool: str, intent: str, parameter: str) -> bool:
    """这个 intent 是否收得下这个参数。没登记的参数一律放行。"""
    contract = PARAMETER_OWNERS.get((tool, parameter))
    return contract is None or intent in contract.intents


def _describe_owners(tool: str, parameter: str, contract: ParameterContract) -> str:
    """一句话说清谁读它。认领者很多时反过来说例外，否则消息会长到没人读。"""
    declared = TOOL_INTENTS.get(tool, frozenset())
    owners = contract.intents
    if not owners:
        detail = "没有任何 intent 读它"
    elif owners == declared:
        detail = f"{tool} 的全部 intent 都读它"
    elif len(declared - owners) <= 3 and len(owners) > 6:
        excluded = "、".join(sorted(declared - owners))
        detail = f"{tool} 里除 {excluded} 之外的全部 intent 都读它"
    else:
        detail = f"{tool} 里只有 {'、'.join(sorted(owners))} 读它"
    return f"{detail}。{contract.hint}" if contract.hint else f"{detail}。"


def check_parameter_ownership(
    tool: str,
    intent: str,
    supplied: Mapping[str, Any],
) -> dict[str, Any] | None:
    """有参数没人认就返回错误响应，否则返回 None。"""
    rejected: list[tuple[str, ParameterContract]] = [
        (name, contract)
        for name, value in supplied.items()
        if _is_supplied(value)
        and (contract := PARAMETER_OWNERS.get((tool, name))) is not None
        and intent not in contract.intents
    ]
    if not rejected:
        return None

    names = "、".join(name for name, _ in rejected)
    details = " ".join(
        f"{name}：{_describe_owners(tool, name, contract)}" for name, contract in rejected
    )
    suggestions: list[dict[str, Any] | str] = [
        {
            "label": f'改用 {contract.suggestion[0]} intent="{contract.suggestion[1]}"',
            "tool": contract.suggestion[0],
            "arguments": {"intent": contract.suggestion[1]},
        }
        for _, contract in rejected
        if contract.suggestion is not None
    ]
    return error_response(
        "ignored_parameter",
        f'{tool}(intent="{intent}") 不读 {names}，传进来的值会被忽略，不会被当成查询条件。{details}',
        next_actions=suggestions,
    )


def declared_intents(function: Callable) -> frozenset[str]:
    """从签名上的 Literal 注解读出这个工具声明支持的 intent。

    注解是字符串（模块有 `from __future__ import annotations`），所以要走
    eval_str；取不到就返回空集合，表示"不校验 intent 名单"。
    """
    try:
        annotation = signature(function, eval_str=True).parameters["intent"].annotation
    except Exception:  # 注解解析不了就不拦，交给原有的 unsupported_intent 分支
        return frozenset()
    if hasattr(annotation, "__metadata__"):  # Annotated[Literal[...], Field(...)]
        annotation = annotation.__origin__
    if get_origin(annotation) is Literal:
        return frozenset(str(item) for item in get_args(annotation))
    return frozenset()


def check_intent_parameters(function: Callable) -> Callable:
    """拦住「intent 不读这个参数」。装在每个 assistant 上。"""
    tool = function.__name__
    parameters = signature(function)
    intents = declared_intents(function)
    defaults = {
        name: parameter.default
        for name, parameter in parameters.parameters.items()
        if parameter.default is not Parameter.empty
    }

    @wraps(function)
    async def wrapped(*args: Any, **kwargs: Any) -> Any:
        try:
            bound = parameters.bind(*args, **kwargs)
        except TypeError:  # 参数本身就不对，让原函数照常报错
            return await function(*args, **kwargs)
        intent = bound.arguments.get("intent")
        if intent is None and "intent" in parameters.parameters:
            intent = parameters.parameters["intent"].default
        intent = str(intent or "").strip().lower()
        # intent 不在声明名单里时不插嘴：那种情况原有的 unsupported_intent 说得更准
        if intent and (not intents or intent in intents):
            supplied = {
                name: value
                for name, value in bound.arguments.items()
                if name != "intent" and _is_supplied(value, defaults.get(name, _MISSING))
            }
            rejected = check_parameter_ownership(tool, intent, supplied)
            if rejected is not None:
                return rejected
        return await function(*args, **kwargs)

    return wrapped


# ── skill 文档里的参数表由这里生成 ──────────────────────────────────────────

TOOL_INTENTS: dict[str, frozenset[str]] = {
    "player_assistant": _all(PlayerIntent),
    "inventory_assistant": _all(InventoryIntent),
    "weapon_assistant": _all(WeaponIntent),
    "build_assistant": _all(BuildIntent),
    "loadout_assistant": _all(LoadoutIntent),
    "subclass_assistant": _all(SubclassIntent),
    "activity_assistant": _all(ActivityIntent),
    "world_assistant": _all(WorldIntent),
}

BLOCK_START = "<!-- 参数归属表开始：由 _param_contracts.render_parameter_table() 生成，不要手改 -->"
BLOCK_END = "<!-- 参数归属表结束 -->"


def _render_owner_cell(tool: str, contract: ParameterContract) -> str:
    declared = TOOL_INTENTS[tool]
    owners = contract.intents
    if not owners:
        return "没有任何 intent 读它"
    if owners == declared:
        return "全部 intent"
    excluded = declared - owners
    if len(excluded) <= 3 and len(owners) > 6:
        return "除 " + "、".join(f"`{name}`" for name in sorted(excluded)) + " 外全部"
    return "、".join(f"`{name}`" for name in sorted(owners))


def render_parameter_table() -> str:
    """生成 skill 文档里的参数归属表（Markdown 表格，不含首尾标记）。"""
    by_parameter: dict[str, list[str]] = {}
    for (tool, parameter), contract in PARAMETER_OWNERS.items():
        by_parameter.setdefault(parameter, []).append(
            f"`{tool}`：{_render_owner_cell(tool, contract)}"
        )

    lines = ["| 参数 | 谁读它 |", "| --- | --- |"]
    for parameter in sorted(by_parameter):
        cells = "；".join(sorted(by_parameter[parameter]))
        lines.append(f"| `{parameter}` | {cells} |")
    return "\n".join(lines)


def main(argv: list[str]) -> int:
    """`python -m destiny_mcp.tools._param_contracts --write-doc` 更新 skill 文档。"""
    from pathlib import Path

    if "--print" in argv:
        print(render_parameter_table())
        return 0
    if "--write-doc" not in argv:
        print(__doc__)
        print("用法：--print 打印表格；--write-doc 写进 skills/destiny2-mcp/references/routing.md")
        return 1

    doc = Path(__file__).resolve().parents[2] / "skills/destiny2-mcp/references/routing.md"
    text = doc.read_text(encoding="utf-8")
    if BLOCK_START not in text or BLOCK_END not in text:
        print(f"✗ 文档里找不到标记：{doc}")
        return 1
    head, rest = text.split(BLOCK_START, 1)
    _, tail = rest.split(BLOCK_END, 1)
    doc.write_text(
        f"{head}{BLOCK_START}\n{render_parameter_table()}\n{BLOCK_END}{tail}",
        encoding="utf-8",
    )
    print(f"✔ 已更新 {doc}")
    return 0


if __name__ == "__main__":  # pragma: no cover - 手工运行
    raise SystemExit(main(sys.argv[1:]))
