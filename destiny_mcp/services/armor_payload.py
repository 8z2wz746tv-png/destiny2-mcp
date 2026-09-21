"""护甲载荷工厂：把 Bungie 的三份组件数据收成一个形状。

设计依据（全部来自实机勘测，见 docs/plans/ARMOR_FORMAT_PLAN.md §1）：

- **两族**：`armor_3`（有 T 级，12 槽）与 `legacy`（无 T 级，15 槽）。legacy 没有的概念
  （词条原型、调谐、词条反推）**不出现**，而不是给一排 `null`。
- **三层属性**：`roll`（三条词条本体，T5 恒为 30/25/20）、`base`（去掉可换部件）、
  `final`（组件 304，游戏里看到的那个数）。反推不出来就整层给 `null` + `notes`，不猜。
- **槽位类别用短键**：原始 `enhancements.v2_general` 这类长字符串占掉整份载荷的大头，
  短键（`general`/`legs`/`archetype`/`roll`/`masterwork`/`tuning`/`shader`/`skin`/`raid`）
  能省一半以上体积。
- **词条本体槽标 `editable: false`**：那三个 `armor_stats` 槽是掉落时定死的，不是给玩家换的。

这一层只读不写，也不碰求解器内部模型（`build/models.Armor`）：它只是同一批数据的视图。
"""

from __future__ import annotations

from typing import Any, Callable, Literal

from ..utils.hash_utils import to_unsigned
from ..vocabulary import CLASS_LABELS_ZH as CLASS_DISPLAY
from ..vocabulary import class_key

ARMOR_SCHEMA_VERSION = 1

DefinitionLookup = Callable[[int], dict[str, Any] | None]

# 组件 304 的属性 hash → 载荷键名（与求解器 / 工具参数同一套）
STAT_HASH_TO_KEY: dict[int, str] = {
    2996146975: "weapons",
    392767087: "health",
    1943323491: "class_stat",
    1735777505: "grenade",
    144602215: "super_stat",
    4244567218: "melee",
}
STAT_KEYS: tuple[str, ...] = tuple(STAT_HASH_TO_KEY.values())

# 求解器内部的槽位桶名（复数）→ 工具面统一用的键（单数）
_SOLVER_SLOT_TO_KEY: dict[str, str] = {
    "helmets": "helmet",
    "gauntlets": "gauntlets",
    "chests": "chest",
    "legs": "legs",
    "class_items": "class_item",
}

# bucket 显示名 → 统一键（列表路径手里只有 bucket_type 字符串）
_BUCKET_TO_KEY: dict[str, str] = {
    "Helmet": "helmet",
    "Gauntlets": "gauntlets",
    "Chest Armor": "chest",
    "Leg Armor": "legs",
    "Class Armor": "class_item",
}

SLOT_DISPLAY: dict[str, str] = {
    "helmet": "头盔",
    "gauntlets": "臂铠",
    "chest": "胸部护甲",
    "legs": "腿部护甲",
    "class_item": "职业护甲",
}

# 反向：定义级查询手里只有 `itemTypeDisplayName`（"腿部护甲"），没有 bucket 字符串
_DISPLAY_TO_SLOT: dict[str, str] = {value: key for key, value in SLOT_DISPLAY.items()}

# ── 装备槽：武器与护甲同一套键（ADR-011）────────────────────────────────
# 正主是物品定义的 `equippingBlock.equipmentSlotTypeHash`（2026-09-21 实测：护甲给护甲桶 hash、
# 武器给武器桶 hash）。以前这套代码里**武器没有槽位**概念（`InventoryItem.slot` 是护甲专用的），
# 装备编排因此看不见武器上的异域；这里补上武器那一半，护甲那半复用 `SLOT_DISPLAY`，
# 保证"槽位键"只有一个出处。
_ARMOR_SLOT_HASHES: dict[str, int] = {
    "helmet": 3448274439,
    "gauntlets": 3551918588,
    "chest": 14239492,
    "legs": 20886954,
    "class_item": 1585787867,
}
_WEAPON_SLOT_LABELS: dict[int, tuple[str, str]] = {
    1498876634: ("kinetic", "动能武器"),
    2465295065: ("energy", "能量武器"),
    953998645: ("power", "威能武器"),
}
EQUIP_SLOT_LABELS: dict[int, tuple[str, str]] = {
    **{slot_hash: (key, SLOT_DISPLAY[key]) for key, slot_hash in _ARMOR_SLOT_HASHES.items()},
    **_WEAPON_SLOT_LABELS,
}


def equip_slot_of(definition: dict | None) -> tuple[str, str]:
    """物品定义 → (装备槽键, 中文名)；武器与护甲同一套键（ADR-011）。

    判不了给 `("", "")` —— 不猜，也不把"没查到"说成某个槽。
    """
    block = (definition or {}).get("equippingBlock")
    if not isinstance(block, dict):
        return "", ""
    slot_hash = block.get("equipmentSlotTypeHash")
    if not isinstance(slot_hash, int):
        return "", ""
    return EQUIP_SLOT_LABELS.get(slot_hash & 0xFFFFFFFF, ("", ""))

# 定义级没有实例，就不给 T 级 / 分族结论（族是按实例结构判的）
DEFINITION_TIER_NOTE = (
    "这是定义级查询，没有具体副本：T 级与护甲分族要看实例，"
    '用 inventory_assistant(intent="item", item_instance_id=…) 读某一件。'
)

# 职业标签的单一出处：vocabulary.CLASS_LABELS_ZH


def class_key_of(class_type: int | None) -> str:
    """组件里的 classType → 工具面统一的键（hunter/warlock/titan）。未登记给空串。"""
    return class_key(class_type)

# 插槽类别 → 短键 + 是否玩家可改
_SOCKET_KINDS: dict[str, tuple[str, bool]] = {
    "enhancements.v2_general": ("general", True),
    "enhancements.v2_head": ("helmet", True),
    "enhancements.v2_arms": ("gauntlets", True),
    "enhancements.v2_chest": ("chest", True),
    "enhancements.v2_legs": ("legs", True),
    "enhancements.v2_class_item": ("class_item", True),
    "armor_archetypes": ("archetype", False),
    "armor_stats": ("roll", False),
    "v460.plugs.armor.masterworks": ("masterwork", False),
    "core.gear_systems.armor_tiering.plugs.tuning.mods": ("tuning", True),
    "shader": ("shader", True),
    # 异域护甲的固定属性分布藏在这里（实测：相对主义 的超能30/近战25 来自
    # `至纯光能之灵`，不是 armor_stats 槽）—— 它和词条一样是"定死的"，算进 roll。
    "intrinsics": ("intrinsic", False),
}

# 大师升级 plug 里的"能量容量"类属性：它不是六维之一，单列进 energy
_NON_STAT_KEYS = {"16120457", "3578062600"}

GEAR_TIER_NOTE = "这件不在分级体系内（组件里的 gearTier=0 或缺失），不是 T0。"


def slot_key_from_bucket(bucket: str) -> str:
    """`"Leg Armor"` → `"legs"`；认不出来就原样返回。"""
    return _BUCKET_TO_KEY.get(bucket, bucket)


def slot_key_from_solver(slot: str) -> str:
    """求解器的复数槽位名 → 统一键（`chests` → `chest`）。"""
    return _SOLVER_SLOT_TO_KEY.get(slot, slot)


def slot_key_from_display(item_type_display: str) -> str:
    """`"腿部护甲"` → `"legs"`；认不出来给空串（定义级只有显示名可用）。"""
    return _DISPLAY_TO_SLOT.get(item_type_display, "")


def socket_kind(category: str) -> tuple[str, bool]:
    """插槽类别 → (短键, 是否可改)。没登记的类别给一个稳的兜底。"""
    if category in _SOCKET_KINDS:
        return _SOCKET_KINDS[category]
    if category.startswith("armor_skins"):
        return "skin", True
    if category.startswith("enhancements.raid") or category == "enhancements.rivens_curse":
        return "raid", True
    if category.startswith("enhancements.artifice"):
        return "artifice", True
    if category.startswith("enhancements."):
        return "mod", True
    if category.startswith("v460.plugs.armor.masterworks"):
        return "masterwork", False
    return category or "other", False


def _stat_key(stat_hash: int | str) -> str | None:
    try:
        return STAT_HASH_TO_KEY.get(int(stat_hash))
    except (TypeError, ValueError):
        return None


def _plug_stats(definition: dict[str, Any] | None) -> dict[str, int]:
    """plug 定义里的六维增量（忽略能量容量等非六维属性）。"""
    out: dict[str, int] = {}
    for entry in (definition or {}).get("investmentStats") or []:
        key = _stat_key(entry.get("statTypeHash", 0))
        value = entry.get("value", 0)
        if key and value:
            out[key] = out.get(key, 0) + int(value)
    return out


def stats_from_component(raw: dict[str, Any] | None) -> dict[str, int]:
    """组件 304 的 stats → 统一键名（缺的补 0，与求解器一致）。"""
    out = {key: 0 for key in STAT_KEYS}
    for stat_hash, entry in (raw or {}).items():
        key = _stat_key(stat_hash)
        if key and isinstance(entry, dict):
            out[key] = int(entry.get("value", 0) or 0)
    return out


def _energy(instance: dict[str, Any] | None) -> dict[str, int] | None:
    raw = (instance or {}).get("energy")
    if not isinstance(raw, dict) or not raw:
        return None
    capacity = int(raw.get("energyCapacity", 0) or 0)
    used = int(raw.get("energyUsed", 0) or 0)
    unused = int(raw.get("energyUnused", capacity - used) or 0)
    return {
        "capacity": capacity,
        "used": used,
        "unused": unused,
        "type_hash": raw.get("energyTypeHash"),
    }


def gear_tier_of(instance: dict[str, Any] | None) -> tuple[int | None, str | None]:
    """返回 (T 级, 说明)。0/缺失 → (None, 说明)，不把 0 当 T0。"""
    raw = (instance or {}).get("gearTier")
    if isinstance(raw, int) and not isinstance(raw, bool) and 1 <= raw <= 5:
        return raw, None
    return None, GEAR_TIER_NOTE


def armor_system_of(instance: dict[str, Any] | None) -> Literal["armor_3", "legacy"]:
    """按**结构**分族：有可用的 T 级（1–5）才是 Armor 3.0。

    注意别用"组件里有没有 gearTier 字段"来判：实测 54 件老护甲也带
    `gearTier: 0`，但它们是 15 槽布局、没有词条原型/调谐槽 —— 按字段判会把这
    54 件误判成 3.0，然后给它们套一套不存在的规则。
    """
    tier, _note = gear_tier_of(instance)
    return "armor_3" if tier is not None else "legacy"


def _sockets(
    socket_entries: list[dict[str, Any]],
    lookup: DefinitionLookup,
    *,
    include_icons: bool = False,
) -> list[dict[str, Any]]:
    """组件 305 → 插槽列表（短键、可改性、能量成本）。"""
    rows: list[dict[str, Any]] = []
    for index, entry in enumerate(socket_entries or []):
        plug_hash = int(entry.get("plugHash", 0) or 0)
        definition = lookup(plug_hash) if plug_hash else None
        category = ((definition or {}).get("plug") or {}).get("plugCategoryIdentifier", "")
        kind, editable = socket_kind(category)
        name = ((definition or {}).get("displayProperties") or {}).get("name", "")
        if not plug_hash and kind == "other":
            kind = "empty"
        cost = ((definition or {}).get("plug") or {}).get("energyCost") or {}
        rows.append(
            {
                "index": index,
                "kind": kind,
                "editable": editable,
                "plug_hash": plug_hash or None,
                "name": name or None,
                "energy_cost": int(cost.get("energyCost", 0) or 0),
                "empty": not plug_hash or "空" in name or not name,
            }
        )
    return rows


def armor_payload(
    *,
    item_hash: int,
    lookup: DefinitionLookup,
    instance: dict[str, Any] | None = None,
    sockets: list[dict[str, Any]] | None = None,
    stats: dict[str, Any] | None = None,
    item_instance_id: str = "",
    location: str = "",
    character_id: str = "",
    power: int | None = None,
    is_equipped: bool = False,
    quantity: int = 1,
    bucket: str = "",
    icon_url: str = "",
    set_info: dict[str, Any] | None = None,
    include_sockets: bool = True,
    include_stats_layers: bool = True,
    name: str = "",
    name_en: str = "",
    item_type_display: str = "",
) -> dict[str, Any]:
    """把一件护甲收成统一载荷。

    参数都是"已经取到的原始数据"，工厂不做任何网络请求；取不到的字段就是取不到，
    由调用方决定省略还是传 `None`。`base` 层算不出来时给 `None` 并在 `notes` 里说明。
    """
    definition = lookup(item_hash) or {}
    display = definition.get("displayProperties") or {}
    name = name or display.get("name", "")
    gear_tier, gear_tier_note = gear_tier_of(instance)
    system = armor_system_of(instance)
    slot = slot_key_from_bucket(bucket)

    entries = sockets or []
    socket_rows = _sockets(entries, lookup) if include_sockets else []

    # 词条本体 / 大师 / 调谐：都从装着的 plug 读，不推断
    roll: dict[str, int] = {}
    tuning_plug_stats: dict[str, int] | None = None
    masterwork_stats: dict[str, int] = {}
    tuning: dict[str, Any] | None = None
    tuning_delta: dict[str, int] = {}
    mod_stats: dict[str, int] = {}
    archetype: dict[str, Any] | None = None
    raid_family: str | None = None

    for row, entry in zip(socket_rows, entries):
        plug_hash = row["plug_hash"] or 0
        if not plug_hash:
            continue
        plug_def = lookup(int(plug_hash)) or {}
        category = (plug_def.get("plug") or {}).get("plugCategoryIdentifier", "")
        plug_stats = _plug_stats(plug_def)
        if category == "armor_archetypes":
            archetype = {
                "hash": int(plug_hash),
                "name": row["name"] or "",
            }
        elif category in ("armor_stats", "intrinsics"):
            # 两处都算"定死的属性分布"：armor_stats 是 3.0 的词条槽，
            # intrinsics 是异域护甲写死的分布。玩家都改不了。
            for key, value in plug_stats.items():
                roll[key] = roll.get(key, 0) + value
        elif category == "v460.plugs.armor.masterworks":
            # plug 的 investmentStats 里除了六维，还夹着"能量容量"（16120457=11），
            # 那不是等级；等级等算完实际生效量再定。
            masterwork_stats = plug_stats
        elif category == "core.gear_systems.armor_tiering.plugs.tuning.mods":
            tuning_plug_stats = plug_stats
            tuning = {
                "hash": int(plug_hash),
                "name": row["name"] or "",
                "declared_delta": plug_stats,
            }
        elif category.startswith("enhancements.raid"):
            raid_family = category.rsplit("_", 1)[-1]
        elif category.startswith("enhancements.") and plug_stats:
            for key, value in plug_stats.items():
                mod_stats[key] = mod_stats.get(key, 0) + value

    final = stats_from_component(stats) if stats is not None else None
    notes: list[str] = []
    base: dict[str, int] | None = None
    applied_masterwork: dict[str, int] | None = None

    # 两族的"基础属性"来源不同，不能混：
    # - armor_3：基础 = 三个词条槽的值本身（直接读，不推断）；大师的**实际生效**值
    #   用 final − roll − 调谐 − 模组反推（plug 定义里写着"六维各 +5"，但那不是实际生效量）。
    # - legacy：没有词条槽，基础 = final − 模组（− 老大师的抗性，不进六维）。
    roll_full = {key: roll.get(key, 0) for key in STAT_KEYS} if roll else None
    if include_stats_layers and final is not None:
        # 调谐的**实际生效**量：定向就是那两个 ±5；"平衡调整"只加在（满大师后）
        # 最低的三项上 —— plug 定义里写的是"六维各 +1"，照它算会多算。
        tuning_delta: dict[str, int] = {}
        if tuning is not None and not tuning_plug_stats:
            tuning["kind"] = "empty"
            tuning["installed"] = False
        elif tuning_plug_stats is not None and roll_full is not None:
            tuning["installed"] = True
            if len(tuning_plug_stats) >= len(STAT_KEYS):
                after = {
                    key: roll_full[key] + masterwork_stats.get(key, 0) for key in STAT_KEYS
                }
                lowest = sorted(STAT_KEYS, key=lambda key: (after[key], key))[:3]
                tuning_delta = {key: 1 for key in lowest}
                tuning["kind"] = "balanced"
            else:
                tuning_delta = dict(tuning_plug_stats)
                tuning["kind"] = "directional"
            tuning["delta"] = tuning_delta
        contributors = {
            key: mod_stats.get(key, 0) + tuning_delta.get(key, 0) for key in STAT_KEYS
        }
        if system == "armor_3" and roll_full is not None:
            base = dict(roll_full)
            observed = {
                key: final.get(key, 0) - base.get(key, 0) - contributors.get(key, 0)
                for key in STAT_KEYS
            }
            if all(value >= 0 for value in observed.values()):
                applied_masterwork = {key: value for key, value in observed.items() if value}
            else:
                notes.append(
                    "最终属性小于词条槽 + 已装部件之和，数据对不上，大师增量未反推（不猜）。"
                )
        else:
            candidate = {
                key: final.get(key, 0) - contributors.get(key, 0) - masterwork_stats.get(key, 0)
                for key in STAT_KEYS
            }
            if all(value >= 0 for value in candidate.values()):
                base = candidate
                notes.append("老护甲没有词条槽，基础属性按「最终 − 已装模组」给出，不是掉落时的原始值。")
            else:
                notes.append("这件护甲装着的部件与最终属性对不上，基础属性无法可靠反推，已留空。")

    payload: dict[str, Any] = {
        "identity": {
            "item_hash": item_hash,
            "name": name,
            "name_en": name_en,
            "slot": slot,
            "slot_display": SLOT_DISPLAY.get(slot, ""),
            "item_type_display": item_type_display,
            "gear_tier": gear_tier,
            "gear_tier_note": gear_tier_note,
            "armor_system": system,
            "icon_url": icon_url,
        },
        "instance": {
            "item_instance_id": item_instance_id,
            "location": location,
            "character_id": character_id,
            "power": power,
            "is_equipped": is_equipped,
            "quantity": quantity,
            "energy": _energy(instance),
        },
        "armor_schema_version": ARMOR_SCHEMA_VERSION,
    }
    identity = payload["identity"]
    if archetype is not None:
        identity["archetype"] = archetype
    if set_info is not None:
        identity["set"] = set_info
    if masterwork_stats or applied_masterwork:
        applied = applied_masterwork if applied_masterwork is not None else masterwork_stats
        block: dict[str, Any] = {
            "level": max(applied.values()) if applied else 0,
            "stat_bonus": applied,
        }
        if system == "armor_3" and not applied:
            block["note"] = "装着大师插件，但没读到它对六维的实际增量（未满级或数据缺失）。"
        payload["instance"]["masterwork"] = block

    if tuning is not None:
        payload["instance"]["tuning"] = tuning
    if include_stats_layers:
        payload["stats"] = {
            "roll": roll_full,
            "base": base,
            "final": final,
            "notes": notes,
        }
    if include_sockets:
        payload["sockets"] = socket_rows
        if raid_family:
            payload["instance"]["raid_family"] = raid_family
    return payload


def armor_definition_payload(
    *,
    item_hash: int,
    lookup: DefinitionLookup,
    name: str = "",
    name_en: str = "",
    item_type_display: str = "",
    icon_url: str = "",
    class_type: int | None = None,
    rarity: str = "异域",
    rarity_tier: int = 6,
    description: str = "",
    flavor_text: str = "",
    intrinsic_perks: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """一件护甲的**定义级**载荷（没有实例）：与 `armor_payload` 同一套身份块。

    `exotic_armor` 以前直接吐 Manifest 的原始键（`nameEn`/`flavorText`/`classType`/
    `tierType`/`intrinsicPerks`），和 `intent="item"` 是两套键集；语料实跑抓到后统一到这里。
    没有实例就**不编** T 级与分族：`gear_tier`/`armor_system` 给 `null` 并附说明。
    """
    definition = lookup(item_hash) or {}
    display = definition.get("displayProperties") or {}
    item_type_display = item_type_display or definition.get("itemTypeDisplayName", "")
    slot = slot_key_from_display(item_type_display)
    class_key = class_key_of(class_type)
    return {
        "identity": {
            "item_hash": to_unsigned(item_hash),
            "name": name or display.get("name", ""),
            "name_en": name_en,
            "slot": slot,
            "slot_display": SLOT_DISPLAY.get(slot, item_type_display),
            "item_type_display": item_type_display,
            "gear_tier": None,
            "gear_tier_note": DEFINITION_TIER_NOTE,
            "armor_system": None,
            "rarity": rarity,
            "rarity_tier": rarity_tier,
            "class_type": class_key,
            "class_display": CLASS_DISPLAY.get(class_key, ""),
            "icon_url": icon_url,
            "description": description or display.get("description", ""),
            "flavor_text": flavor_text or definition.get("flavorText", ""),
        },
        "intrinsic_perks": intrinsic_perks or [],
        "armor_schema_version": ARMOR_SCHEMA_VERSION,
    }
