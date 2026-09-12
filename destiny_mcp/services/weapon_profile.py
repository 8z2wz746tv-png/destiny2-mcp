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

import json
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable, Mapping

from ..manifest_names import RPM_STAT_HASH

if TYPE_CHECKING:  # pragma: no cover
    from .manifest import ManifestManager

RARITY: dict[int, str] = {6: "异域", 5: "传说", 4: "稀有", 3: "罕见", 2: "普通"}

# 生成物：基础 perk ↔ 强化 perk。由 scripts/generate_weapon_metadata.py 产出，
# 里面记着生成时的 Manifest 指纹（见 manifest_fingerprint）。
_REPO_ROOT = Path(__file__).resolve().parents[2]
ENHANCED_PAIRS_PATH = _REPO_ROOT / "data" / "weapon_enhanced_pairs.json"

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

# roll 相关栏位给全量选项；其余（着色器/装饰/追踪器/大师杰作…）只给计数 + 少量样本。
# 实测：遗产的着色器插槽有 694 个选项、大师杰作 167 个 —— 全量展开会把响应撑到上百 KB。
ROLL_KINDS: frozenset[str] = frozenset({"intrinsic", "barrel", "magazine", "trait", "scope", "grip"})
# 只有 roll 相关栏位给全量。模组以前也在里面，实测一件武器 54 个模组选项 = 19.6 KB
# （perk_pool 里最大的一块），而"能滚到什么"跟模组无关 —— 模组改为计数 + 3 个样本。
FULL_OPTION_KINDS: frozenset[str] = ROLL_KINDS | {"catalyst"}
NON_DETAIL_OPTION_SAMPLE = 3

# 实例级（副本级）只对 roll 栏给全量：模组/大师杰作/纪念物这些"每个副本都差不多"的栏，
# 一件武器能列 17 个模组 + 11 个大师杰作，列表里 5 把武器就撑到 100 KB 以上（实测）。
# 计数 + 少量样本 + options_truncated 已经能回答"这栏能不能换"，要全量就单把武器再问。
INSTANCE_FULL_OPTION_KINDS: frozenset[str] = ROLL_KINDS

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


def find_weapon(manifest: "ManifestManager", weapon_name: str) -> tuple[int, dict]:
    """按名字找武器定义：返回 (item_hash, definition)。

    只取 `itemType == 3` 的条目 —— 存在与武器同名的非武器条目（例如「遗产」），
    按精确名取会拿到那一条，然后误报「不是武器」。
    """
    from ..exceptions import ManifestError

    for item in manifest.search(weapon_name, limit=5):
        item_hash = item.get("itemHash") or item.get("hash") or 0
        if item.get("itemType") != 3:
            continue
        definition = manifest.get_item_definition(item_hash)
        if definition:
            return int(item_hash), definition
    raise ManifestError(f"找不到武器: {weapon_name}")


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


# ── 强化 perk 配对（生成物）──────────────────────────────────────────────


class EnhancedPairs:
    """基础 perk hash → 强化 perk hash 的查表。

    数据来自生成物，Manifest 里没有这个关系；`enhanced_plug_hash` 的语义是
    "这个 perk 的强化版 hash"（本身就是强化版时指向自己，没有强化版时为 0）。
    """

    def __init__(self, pairs: Mapping[str, Mapping[str, Any]] | None = None) -> None:
        self._pairs = dict(pairs or {})
        self._reverse: dict[int, int] = {}
        for key, entry in self._pairs.items():
            try:
                base = int(key)
                enhanced = int(entry.get("enhanced") or 0)
            except (TypeError, ValueError):
                continue
            if enhanced:
                self._reverse.setdefault(enhanced, base)

    @property
    def size(self) -> int:
        return len(self._pairs)

    def enhanced_of(self, plug_hash: int) -> int:
        """该 perk 的强化版 hash；没有就 0。本身就是强化版时返回自己。"""
        entry = self._pairs.get(str(plug_hash))
        if entry:
            return int(entry.get("enhanced") or 0)
        if plug_hash in self._reverse:
            return int(plug_hash)
        return 0

    def base_of(self, plug_hash: int) -> int:
        """强化版 perk 对应的基础版 hash；不是强化版就返回 0。"""
        return int(self._reverse.get(int(plug_hash), 0))

    def is_enhanced(self, plug_hash: int) -> bool:
        return int(plug_hash) in self._reverse


_PAIRS_CACHE: dict[str, tuple[float, EnhancedPairs]] = {}


def load_enhanced_pairs(path: Path | None = None) -> EnhancedPairs:
    """读生成物（按文件 mtime 缓存）。文件缺失时返回空表，不抛异常。"""
    target = Path(path) if path is not None else ENHANCED_PAIRS_PATH
    try:
        mtime = target.stat().st_mtime
    except OSError:
        return EnhancedPairs()
    cached = _PAIRS_CACHE.get(str(target))
    if cached and cached[0] == mtime:
        return cached[1]
    try:
        document = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return EnhancedPairs()
    pairs = EnhancedPairs(document.get("pairs") or {})
    _PAIRS_CACHE[str(target)] = (mtime, pairs)
    return pairs


# ── 定义级插槽 ───────────────────────────────────────────────────────────


def _plug_definition(manifest: "ManifestManager", plug_hash: int) -> dict:
    getter = getattr(manifest, "get_item_definition", None)
    if not callable(getter):
        return {}
    return getter(plug_hash) or {}


def _stat_effects(manifest: "ManifestManager", names: Any, plug_hash: int) -> list[dict]:
    """plug 自带的效果：`investmentStats` 是增量（例如箭头制退器 后坐+30、操控+10）。"""
    definition = _plug_definition(manifest, plug_hash)
    effects: list[dict] = []
    for entry in definition.get("investmentStats") or []:
        if not isinstance(entry, dict):
            continue
        value = entry.get("value")
        if not value:
            continue
        effect = {
            "stat": names.stat(entry.get("statTypeHash", 0)) or f"#{entry.get('statTypeHash', 0)}",
            "value": value,
        }
        if entry.get("isConditionallyActive"):
            effect["conditional"] = True
        effects.append(effect)
    return effects


def _plug_option(
    manifest: "ManifestManager",
    *,
    plug_hash: int,
    name: str = "",
    category: str = "",
    can_roll: bool = True,
    names: Any,
    pairs: EnhancedPairs,
    include_descriptions: bool,
    include_icons: bool = True,
    god_roll_lookup: Callable[[int], tuple[bool, bool]] | None = None,
) -> dict:
    info = manifest.get_item_info(plug_hash) or {}
    option_name = str(name or info.get("name") or "").strip()
    plug_category = str(category or manifest.get_plug_category_identifier(plug_hash) or "")
    option: dict[str, Any] = {
        "plug_hash": int(plug_hash),
        "name": option_name,
        "plug_category": plug_category,
        "can_roll": bool(can_roll),
        # `enhanced` 布尔与 `enhanced_plug_hash` 表达同一件事（自己就是强化版时后者指向自己），
        # 保留一个即可；`recommended.wishlist` 也取代了原来的扁平 god_roll_pve/pvp。
        "enhanced_plug_hash": pairs.enhanced_of(plug_hash),
    }
    if include_descriptions:
        description = ""
        sandbox = manifest.get_sandbox_perk_description(plug_hash)
        if isinstance(sandbox, dict):
            description = str(sandbox.get("description") or "")
        if not description:
            description = str(info.get("description") or "")
        if not description:
            # 中文 Manifest 里 perk 的描述常常只落在物品定义的 displayProperties.description
            fallback = getattr(manifest, "get_item_description", None)
            if callable(fallback):
                item_description = fallback(plug_hash)
                if isinstance(item_description, str):
                    description = item_description
        option["description"] = description
    effects = _stat_effects(manifest, names, plug_hash)
    if effects:
        option["stat_effects"] = effects
    if god_roll_lookup is not None:
        pve, pvp = god_roll_lookup(plug_hash)
        if pve or pvp:
            option["recommended"] = {"wishlist": {"pve": bool(pve), "pvp": bool(pvp)}}
    if include_icons:
        option["icon_url"] = str(info.get("icon") or "")
    return option


def socket_columns(
    manifest: "ManifestManager",
    definition: Mapping[str, Any] | None,
) -> list[dict]:
    """只**数**每个插槽有几个可选项，不构造选项字典（所有栏，不只 roll 栏）。

    列表类（按类型列武器）只需要"有哪些栏、每栏几个、现在装的什么"，把整张池子
    塞进去会让响应从几十 KB 涨到几百 KB（实测 5 把武器 597 KB）。所以这里做轻量计数，
    完整池子由单把武器的 `info`/`perk_pool` 给。
    """
    columns: list[dict] = []
    used: dict[str, int] = {}
    for index, entry in enumerate(socket_entries(definition)):
        plug_set_hash = entry.get("randomizedPlugSetHash") or entry.get("reusablePlugSetHash")
        single_hash = entry.get("singleInitialItemHash") or 0
        if not plug_set_hash and not single_hash:
            continue
        if plug_set_hash:
            raw_plugs = manifest.get_plug_set_plugs(plug_set_hash) or []
        else:
            raw_plugs = [{"plugItemHash": single_hash}]
        kind = ""
        names: list[str] = []
        seen: set[int] = set()
        for raw in raw_plugs:
            plug_hash = int(raw.get("plugItemHash") or 0)
            if not plug_hash or plug_hash in seen:
                continue
            seen.add(plug_hash)
            category = str(raw.get("plugCategoryIdentifier") or "") or (
                manifest.get_plug_category_identifier(plug_hash) or ""
            )
            if not kind:
                kind = slot_kind(category)
            name = str(raw.get("name") or "")
            if not name:
                name = str((manifest.get_item_info(plug_hash) or {}).get("name") or "")
            if name:
                names.append(name)
        if not kind or not names:
            continue
        base = slot_label(kind)
        used[base] = used.get(base, 0) + 1
        columns.append({
            "slot": base,
            "kind": kind,
            "socket_index": index,
            "option_count": len(names),
        })
    for column in columns:
        if used.get(column["slot"], 0) > 1:
            same = [c for c in columns if c["slot"] == column["slot"]]
            column["slot"] = f"{column['slot']}{same.index(column) + 1}"
    return columns


def roll_summary_from_columns(columns: list[dict]) -> dict[str, Any]:
    """与 `roll_summary` 同一结构，但输入是轻量计数。"""
    random_columns = [
        column["slot"]
        for column in columns
        if column["kind"] in {"barrel", "magazine", "trait", "scope", "grip"}
        and column["option_count"] > 1
    ]
    return {
        "roll_kind": "random" if random_columns else "fixed",
        "random_columns": random_columns,
        "option_counts": {
            column["slot"]: column["option_count"]
            for column in columns
            if column["slot"] in random_columns
        },
        "scope": "definition",
    }


def socket_options(
    manifest: "ManifestManager",
    definition: Mapping[str, Any] | None,
    *,
    names: Any = None,
    pairs: EnhancedPairs | None = None,
    include_descriptions: bool = True,
    include_icons: bool = True,
    god_roll_lookup: Callable[[int], tuple[bool, bool]] | None = None,
) -> list[dict]:
    """**全部**插槽（定义级）：框架/枪管/弹匣/特性/模组/大师杰作/纪念物/追踪器/装饰…

    以前只取两类插槽类别 + 一张 plug 类别白名单，于是大师杰作、模组、纪念物全丢了；
    这里改成遍历所有 socket entry，按 plug 类别归 kind，认不出来归 `other`（不丢）。
    """
    if names is None:
        from ..manifest_names import names_for

        names = names_for(manifest)
    pairs = pairs if pairs is not None else load_enhanced_pairs()

    sockets: list[dict] = []
    used_labels: dict[str, int] = {}
    for index, entry in enumerate(socket_entries(definition)):
        plug_set_hash = entry.get("randomizedPlugSetHash") or entry.get("reusablePlugSetHash")
        single_hash = entry.get("singleInitialItemHash") or 0
        if not plug_set_hash and not single_hash:
            continue  # 这个插槽没有任何来源，跳过

        if plug_set_hash:
            raw_plugs = manifest.get_plug_set_plugs(plug_set_hash) or []
        else:
            raw_plugs = [{"plugItemHash": single_hash}]

        options: list[dict] = []
        seen: set[int] = set()
        for raw in raw_plugs:
            plug_hash = int(raw.get("plugItemHash") or 0)
            if not plug_hash or plug_hash in seen:
                continue
            seen.add(plug_hash)
            option = _plug_option(
                manifest,
                plug_hash=plug_hash,
                name=str(raw.get("name") or ""),
                category=str(raw.get("plugCategoryIdentifier") or ""),
                can_roll=bool(raw.get("currentlyCanRoll", True)),
                names=names,
                pairs=pairs,
                include_descriptions=include_descriptions,
                include_icons=include_icons,
                god_roll_lookup=god_roll_lookup,
            )
            if not option["name"]:
                continue  # 无名字的占位 plug（Bungie 用它占位），不是可选内容
            options.append(option)
        if not options:
            continue

        kind = slot_kind(str(options[0].get("plug_category") or ""))
        # 同一栏里类别一致时，逐选项重复 plug_category 纯属浪费（实测 2.7 KB/把）；
        # 只在这个选项的类别与栏位主类别不同时才保留。
        for option in options:
            if option.get("plug_category") == options[0].get("plug_category"):
                option.pop("plug_category", None)

        socket: dict[str, Any] = {
            "slot": "",
            "kind": kind,
            "scope": "definition",
            "socket_index": index,
            "option_count": len(options),
        }
        if kind in FULL_OPTION_KINDS or len(options) <= NON_DETAIL_OPTION_SAMPLE:
            socket["options"] = options
        else:
            # 装饰/大师杰作这类插槽动辄上百个选项（着色器 694、大师杰作 167），
            # 全量展开没有意义；给计数 + 少量样本，并明说被裁过。
            socket["options"] = options[:NON_DETAIL_OPTION_SAMPLE]
            socket["options_truncated"] = True
        sockets.append(socket)
        used_labels[slot_label(kind)] = used_labels.get(slot_label(kind), 0) + 1

    # 同名栏位统一编号：有两栏「特性」就叫「特性1」「特性2」（只编重复的，不编唯一的）
    for socket in sockets:
        base = slot_label(str(socket.get("kind") or ""))
        if used_labels.get(base, 0) > 1:
            socket["slot"] = f"{base}{_ordinal_of(sockets, socket)}"
        else:
            socket["slot"] = base
    return sockets


def _ordinal_of(sockets: list[dict], target: dict) -> int:
    """同一 kind 里这是第几栏（从 1 开始）—— 用于「特性1/特性2」。"""
    base = slot_label(str(target.get("kind") or ""))
    order = 0
    for socket in sockets:
        if slot_label(str(socket.get("kind") or "")) != base:
            continue
        order += 1
        if socket is target:
            return order
    return order


# 物品 state 位（组件 102/201/205 的 item.state）。来源：Bungie 官方 OpenAPI 生成的
# bungie-api-ts `ItemState`（destiny2/interfaces.js）：
#   None=0, Locked=1, Tracked=2, Masterwork=4, Crafted=8, HighlightedObjective=16
# 注意这是**现版本**的位序；老资料里 Crafted=4 / Masterworked=32 已经不对了，别照抄。
# 真机交叉核对（1873 件）：Locked=250 件；Tracked=0 件（该账号没追踪任务）；
# Crafted 置位的 204 件里 170 件定义可锻造 —— 与语义一致。
ITEM_STATE_LOCKED = 1
ITEM_STATE_TRACKED = 2
ITEM_STATE_MASTERWORK = 4
ITEM_STATE_CRAFTED = 8


def item_state_flags(state: Any) -> dict[str, bool | None]:
    """把 item.state 位掩码翻成字段；读不到就给 None（不猜 False）。"""
    if not isinstance(state, int) or isinstance(state, bool):
        return {"locked": None, "tracked": None}
    return {
        "locked": bool(state & ITEM_STATE_LOCKED),
        "tracked": bool(state & ITEM_STATE_TRACKED),
    }


def instance_fields(instance: Mapping[str, Any] | None) -> dict[str, Any]:
    """组件 300（itemInstances）里的副本级字段 + 缺失清单。

    `gear_tier` 是装备分级 T1–T5；组件里的 **0 表示"不在分级体系内"**（旧装备，或本来就
    不属于分级的武器），不是 T0 —— 所以统一输出 `null`，并在 `legacy_tier` 里标出来，
    由展示层补一句说明。`item_level`/`quality` 由 Bungie 给。
    这些字段**只在账号数据里**，Manifest 里查不到；没取到就留 None 并记进 `missing`，
    避免调用方把"没读到"当成"没有"。
    """
    data: Mapping[str, Any] = instance if isinstance(instance, Mapping) else {}
    fields: dict[str, Any] = {}
    missing: list[str] = []
    legacy_tier = False
    for key, source in (("gear_tier", "gearTier"), ("item_level", "itemLevel"), ("quality", "quality")):
        value = data.get(source)
        if key == "gear_tier" and value == 0:
            # 0 = 无分级：给 null（不是 0），并标记出来让上层写说明
            fields[key] = None
            legacy_tier = True
            continue
        if isinstance(value, int) and not isinstance(value, bool):
            fields[key] = value
        else:
            fields[key] = None
            missing.append(key)
    fields["missing"] = missing
    fields["legacy_tier"] = legacy_tier
    return fields


def _instance_plug_hashes(raw_plugs: Any) -> list[int]:
    """310 里某一栏的可插 plug：`[{"plugItemHash":…, "canInsert":…}, …]`。"""
    hashes: list[int] = []
    seen: set[int] = set()
    for item in raw_plugs or []:
        if not isinstance(item, Mapping):
            continue
        if item.get("canInsert") is False:
            continue  # 这个副本插不进去（该栏已被锁死/等级不够）
        plug_hash = int(item.get("plugItemHash") or 0)
        if not plug_hash or plug_hash in seen:
            continue
        seen.add(plug_hash)
        hashes.append(plug_hash)
    return hashes


def instance_options(
    manifest: "ManifestManager",
    definition: Mapping[str, Any] | None,
    reusable_plugs: Mapping[str, Any] | None,
    *,
    equipped: list[int] | None = None,
    names: Any = None,
    pairs: EnhancedPairs | None = None,
    include_descriptions: bool = True,
    include_icons: bool = True,
    god_roll_lookup: Callable[[int], tuple[bool, bool]] | None = None,
) -> list[dict]:
    """**这一件副本**能换成什么（组件 310 itemReusablePlugs）。

    定义级的池子是"这把枪原则上能出什么"，实例级才是"我手上这把能换成什么"。
    真机核对了 745 件武器的特性栏（只统计 plug 类别为 frames 的栏，去掉 canInsert=false）：

    | 情形 | 每栏可插数 |
    | --- | --- |
    | T5（非锻造） | 大多 3（305 件里 286 件），少数 2 或 1 |
    | T4（非锻造） | 2（16/16） |
    | T3（非锻造） | 2（6/7） |
    | T2（非锻造） | 1（2/2） |
    | 锻造件（state 含 Crafted=8） | 通常只有 1 —— 310 只给当前选中的那个，不是全部可塑形项 |
    | 无 T 级的旧装备（T0） | 1–6 不等，没有规律 |

    所以**不能**拿 T 级去算可选项数量，也不能假定锻造件能列全：以 310 为准。
    插槽与标签沿用定义级，保证两边按 `socket_index`/`slot` 对得上。
    """
    if not isinstance(reusable_plugs, Mapping):
        return []
    per_socket = reusable_plugs.get("plugs")
    if not isinstance(per_socket, Mapping):
        return []

    if names is None:
        from ..manifest_names import names_for

        names = names_for(manifest)
    pairs = pairs if pairs is not None else load_enhanced_pairs()

    definition_sockets = socket_options(
        manifest,
        definition,
        names=names,
        pairs=pairs,
        include_descriptions=include_descriptions,
        god_roll_lookup=god_roll_lookup,
    )
    meta = {socket["socket_index"]: socket for socket in definition_sockets}

    options: list[dict] = []
    for key, entry in per_socket.items():
        try:
            index = int(key)
        except (TypeError, ValueError):
            continue
        socket = dict(meta.get(index) or {})
        plug_hashes = _instance_plug_hashes(entry)
        if not plug_hashes:
            continue  # 这一栏这个副本没有可换内容（真机上确实会出现 0/N）
        built: list[dict] = []
        for plug_hash in plug_hashes:
            option = _plug_option(
                manifest,
                plug_hash=plug_hash,
                names=names,
                pairs=pairs,
                include_descriptions=include_descriptions,
                include_icons=include_icons,
                god_roll_lookup=god_roll_lookup,
            )
            if not option["name"]:
                continue
            built.append(option)
        if not built:
            continue
        primary_category = str(built[0].get("plug_category") or "")
        kind = str(socket.get("kind") or slot_kind(primary_category))
        # 与定义级同一口径：同一栏里类别一致时不逐选项重复 plug_category
        for option in built:
            if option.get("plug_category") == primary_category:
                option.pop("plug_category", None)
        socket.update(
            {
                "slot": socket.get("slot") or slot_label(kind),
                "kind": kind,
                "scope": "instance",
                "socket_index": index,
                "option_count": len(built),
            }
        )
        if kind in INSTANCE_FULL_OPTION_KINDS or len(built) <= NON_DETAIL_OPTION_SAMPLE:
            socket["options"] = built
        else:
            socket["options"] = built[:NON_DETAIL_OPTION_SAMPLE]
            socket["options_truncated"] = True
        options.append(socket)
    options.sort(key=lambda socket: socket.get("socket_index", 0))

    # 现在装的是哪个（组件 305）：和可选项放一起，"换成什么"才有对照
    for socket in options:
        index = socket["socket_index"]
        current = 0
        if equipped and 0 <= index < len(equipped):
            current = int(equipped[index] or 0)
        socket["equipped_plug_hash"] = current
        socket["equipped_name"] = (
            str((manifest.get_item_info(current) or {}).get("name") or "") if current else ""
        )
    return options


def roll_summary(sockets: list[dict], *, scope: str = "definition") -> dict:
    """回答"能不能滚、有几栏"：随机栏清单 + 每栏可选数。"""
    random_columns = [
        socket["slot"] for socket in sockets if socket.get("kind") in {"barrel", "magazine", "trait", "scope", "grip"}
        and socket.get("option_count", 0) > 1
    ]
    return {
        "roll_kind": "random" if any(socket.get("kind") == "trait" and socket.get("option_count", 0) > 1 for socket in sockets) else "fixed",
        "random_columns": random_columns,
        "option_counts": {
            socket["slot"]: socket["option_count"]
            for socket in sockets
            if socket.get("slot") in random_columns
        },
        "scope": scope,
    }


def has_enhanced(sockets: list[dict]) -> bool:
    """这把武器的池子里有没有带强化版的 perk。"""
    return any(
        option.get("enhanced_plug_hash")
        for socket in sockets
        for option in socket.get("options") or []
    )


def fixed_roll_perks(sockets: list[dict]) -> list[dict]:
    """固定 roll 武器的"真正固定"内容：固有特性 + 只有一个选项的特性栏。

    固定武器（多数异域、蓝绿白）问"推荐 roll"是无意义的，该回答它到底装了什么；
    但**不能把可选的枪管/弹匣也算进去**，那些是可换部件。
    """
    perks: list[dict] = []
    for socket in sockets:
        if socket.get("kind") == "intrinsic":
            perks.extend(socket.get("options") or [])
            continue
        if socket.get("kind") == "trait" and socket.get("option_count", 0) == 1:
            perks.extend(socket.get("options") or [])
    return perks
