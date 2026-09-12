"""武器画像：稀有度、框架、射速、能不能滚、破盾……这些"这把枪是什么"的判定。

放在这里的原因：同一个判定以前散在多个服务里各写一遍（稀有度表 3 份、属性名 3 套、
框架从固有槽现取），改一处漏一处。这个模块只做**纯判定**，不碰网络、不碰账号：
输入是 Manifest 的定义字典，输出是稳定的字段值，方便单测。

数据来源的三条规矩（都是从真实数据里核对出来的）：

1. **稀有度**看 `inventory.tierType`：6 异域 / 5 传说 / 4 稀有 / 3 罕见 / 2 普通。
2. **框架**在固有槽里，且**只有以「框架」结尾才算框架**；异域武器的固有槽放的是专属特性
   （泰拉巴=贪食野兽），此时 `frame` 为 None，但 `intrinsic` 一定有值。
3. **属性值**优先取 `definition.stats.stats[hash].value`（Bungie 算好的显示值），
   取不到才回退 `investmentStats`。两者会不一样：遗产的每分钟发射数
   investmentStats=30、显示值=65（框架加成已算进显示值）。
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Mapping

from ..manifest_names import RPM_STAT_HASH

if TYPE_CHECKING:  # pragma: no cover
    from .manifest import ManifestManager

RARITY: dict[int, str] = {6: "异域", 5: "传说", 4: "稀有", 3: "罕见", 2: "普通"}

# 插槽类别：固有特性（框架）与武器特性（枪管/弹匣/特性）
SOCKET_CATEGORY_INTRINSIC = 3956125808
SOCKET_CATEGORY_WEAPON_PERKS = 4241085061

FRAME_SUFFIX = "框架"

# plug 类别 → 稳定的 kind（P2 起 sockets 用它；先在这里定一份，避免各处自造）
_SLOT_KINDS: tuple[tuple[str, str], ...] = (
    ("intrinsic", "intrinsic"),
    ("barrel", "barrel"),
    ("scope", "scope"),
    ("sight", "scope"),
    ("magazine", "magazine"),
    ("battery", "magazine"),
    ("grip", "grip"),
    ("stock", "grip"),
    ("frame", "trait"),
    ("trait", "trait"),
    ("perk", "trait"),
    ("origin", "trait"),
    ("mod", "mod"),
    # tracker 必须排在 masterwork 前面：类别串是 v400.plugs.weapons.masterworks.trackers
    ("tracker", "tracker"),
    ("masterwork", "masterwork"),
    ("memento", "memento"),
    ("shader", "shader"),
    ("ornament", "ornament"),
    ("skin", "ornament"),
    ("catalyst", "catalyst"),
)

SLOT_LABELS: dict[str, str] = {
    "intrinsic": "框架",
    "barrel": "枪管",
    "scope": "瞄具",
    "magazine": "弹匣",
    "grip": "握把",
    "trait": "特性",
    "mod": "模组",
    "masterwork": "大师杰作",
    "memento": "纪念物",
    "tracker": "追踪器",
    "shader": "着色器",
    "ornament": "装饰",
    "catalyst": "催化剂",
    "other": "插槽",
}


def rarity_of(tier: Any) -> str:
    """稀有度中文名；未知档位照实说，不猜。"""
    if isinstance(tier, int) and tier in RARITY:
        return RARITY[tier]
    return f"未知({tier})" if tier not in (None, 0) else ""


def socket_entries(definition: Mapping[str, Any] | None) -> list[dict]:
    sockets = (definition or {}).get("sockets") or {}
    entries = sockets.get("socketEntries") or []
    return [entry for entry in entries if isinstance(entry, dict)]


def socket_category_indexes(
    definition: Mapping[str, Any] | None, category_hash: int
) -> set[int]:
    sockets = (definition or {}).get("sockets") or {}
    indexes: set[int] = set()
    for category in sockets.get("socketCategories") or []:
        if isinstance(category, dict) and category.get("socketCategoryHash") == category_hash:
            indexes.update(
                index for index in category.get("socketIndexes") or [] if isinstance(index, int)
            )
    return indexes


def socket_kinds(definition: Mapping[str, Any] | None) -> dict[str, int]:
    """插槽种类计数：固定件 / 可选池 / 随机池。

    这是"能不能滚"的唯一判据 —— 有随机池就是随机 roll 武器，与稀有度无关
    （可锻造的异域武器也有随机池）。
    """
    counts = {"fixed": 0, "option": 0, "random": 0}
    for entry in socket_entries(definition):
        if entry.get("randomizedPlugSetHash"):
            counts["random"] += 1
        elif entry.get("reusablePlugSetHash"):
            counts["option"] += 1
        elif entry.get("singleInitialItemHash"):
            counts["fixed"] += 1
    return counts


def roll_kind(definition: Mapping[str, Any] | None) -> str:
    """`random`（有随机池）或 `fixed`（只有固定件/可选池）。"""
    return "random" if socket_kinds(definition)["random"] > 0 else "fixed"


def intrinsic_plug(manifest: "ManifestManager", definition: Mapping[str, Any] | None) -> dict:
    """固有槽的 plug 信息：{plug_hash, name, category}；没有就返回空字典。"""
    entries = socket_entries(definition)
    for index in sorted(socket_category_indexes(definition, SOCKET_CATEGORY_INTRINSIC)):
        if index >= len(entries):
            continue
        entry = entries[index]
        plug_hash = entry.get("singleInitialItemHash")
        if not plug_hash:
            plugs = manifest.get_plug_set_plugs(entry.get("reusablePlugSetHash", 0)) or []
            plug_hash = plugs[0].get("plugItemHash") if plugs else 0
        if not plug_hash:
            continue
        info = manifest.get_item_info(plug_hash) or {}
        return {
            "plug_hash": int(plug_hash),
            "name": str(info.get("name") or ""),
            "category": manifest.get_plug_category_identifier(plug_hash) or "",
        }
    return {}


def frame_of(intrinsic_name: str) -> str | None:
    """框架名：只有以「框架」结尾才认（异域专属特性不算框架）。"""
    name = str(intrinsic_name or "").strip()
    return name if name.endswith(FRAME_SUFFIX) else None


def stat_value(definition: Mapping[str, Any] | None, stat_hash: int) -> int | None:
    """属性值：优先显示值（`stats.stats`），回退投资值（`investmentStats`）。"""
    stats = (definition or {}).get("stats") or {}
    display = stats.get("stats") or {}
    for key in (str(stat_hash), str(stat_hash + 4294967296 if stat_hash < 0 else stat_hash)):
        entry = display.get(key)
        if isinstance(entry, dict) and isinstance(entry.get("value"), int):
            return int(entry["value"])
    for entry in (definition or {}).get("investmentStats") or []:
        if isinstance(entry, dict) and entry.get("statTypeHash") == stat_hash:
            value = entry.get("value")
            if isinstance(value, int):
                return value
    return None


def rpm_of(definition: Mapping[str, Any] | None) -> int | None:
    """每分钟发射数（显示值优先）。"""
    return stat_value(definition, RPM_STAT_HASH)


def is_craftable(definition: Mapping[str, Any] | None) -> bool:
    """能不能锻造：看 `inventory.recipeItemHash`，不要再按 type 30 反查（会漏）。"""
    inventory = (definition or {}).get("inventory") or {}
    return bool(inventory.get("recipeItemHash"))


def trait_ids(definition: Mapping[str, Any] | None) -> list[str]:
    """武器族 / 稀有度 / 版本标签（Bungie 的 traitIds）。"""
    raw = (definition or {}).get("traitIds") or []
    return [str(item) for item in raw if isinstance(item, str)]


def slot_kind(plug_category: str) -> str:
    """plug 类别 → 稳定 kind；认不出来归 `other`（不再静默丢掉这个插槽）。"""
    lowered = str(plug_category or "").casefold()
    for marker, kind in _SLOT_KINDS:
        if marker in lowered:
            return kind
    return "other"


def slot_label(kind: str) -> str:
    return SLOT_LABELS.get(kind, SLOT_LABELS["other"])


def ammo_enum(definition: Mapping[str, Any] | None) -> int | None:
    equipping = (definition or {}).get("equippingBlock") or {}
    value = equipping.get("ammoType")
    return int(value) if isinstance(value, int) and value > 0 else None


def damage_value(definition: Mapping[str, Any] | None) -> int | None:
    """伤害类型：优先 hash（`defaultDamageTypeHash`），回退枚举（`defaultDamageType`）。"""
    for key in ("defaultDamageTypeHash", "defaultDamageType"):
        value = (definition or {}).get(key)
        if isinstance(value, int) and value > 0:
            return int(value)
    hashes = (definition or {}).get("damageTypeHashes") or []
    for value in hashes:
        if isinstance(value, int) and value > 0:
            return int(value)
    return None


def weapon_type_of(definition: Mapping[str, Any] | None) -> str:
    return str((definition or {}).get("itemTypeDisplayName") or "")


def display_name_of(manifest: "ManifestManager", definition: Mapping[str, Any] | None, fallback: str = "") -> str:
    display = (definition or {}).get("displayProperties") or {}
    name = str(display.get("name") or "").strip()
    if name:
        return name
    item_hash = (definition or {}).get("hash")
    if isinstance(item_hash, int):
        info = manifest.get_item_info(item_hash) or {}
        name = str(info.get("name") or "").strip()
    return name or fallback


def identity_fields(
    manifest: "ManifestManager",
    definition: Mapping[str, Any] | None,
    *,
    names: Any = None,
    fallback_name: str = "",
) -> dict[str, Any]:
    """身份块里**只依赖定义**的那些字段。

    `roll_summary` / `has_enhanced` / `gear_tier` / `owned` 分别在 P2、P3 补齐；
    这里先把"这把枪是什么"给全，避免每处再各写一套。
    """
    if names is None:
        from ..manifest_names import names_for

        names = names_for(manifest)
    definition = definition or {}
    intrinsic = intrinsic_plug(manifest, definition)
    inventory = definition.get("inventory") or {}
    display = definition.get("displayProperties") or {}
    icon = str(display.get("icon") or "")
    return {
        "item_hash": definition.get("hash") or 0,
        "name": display_name_of(manifest, definition, fallback_name),
        "name_en": _english_name(manifest, definition),
        "rarity": rarity_of(inventory.get("tierType")),
        "rarity_tier": int(inventory.get("tierType") or 0),
        "weapon_type": weapon_type_of(definition),
        "frame": frame_of(str(intrinsic.get("name") or "")),
        "intrinsic": str(intrinsic.get("name") or ""),
        "rpm": rpm_of(definition),
        "ammo_type": names.ammo_type(ammo_enum(definition)),
        "damage_type": names.damage_type(damage_value(definition)),
        "breaker_type": names.breaker_type(definition.get("breakerType")),
        "is_craftable": is_craftable(definition),
        "trait_ids": trait_ids(definition),
        "watermark": str(definition.get("iconWatermark") or ""),
        "description": str(display.get("description") or definition.get("flavorText") or ""),
        "icon_url": f"https://www.bungie.net{icon}" if icon.startswith("/") else icon,
    }


def _english_name(manifest: "ManifestManager", definition: Mapping[str, Any]) -> str:
    item_hash = definition.get("hash")
    if not isinstance(item_hash, int):
        return ""
    getter = getattr(manifest, "get_english_name", None)
    if callable(getter):
        try:
            return str(getter(item_hash) or "")
        except Exception:  # noqa: BLE001 - 英文名缺失不该影响画像
            return ""
    return ""
