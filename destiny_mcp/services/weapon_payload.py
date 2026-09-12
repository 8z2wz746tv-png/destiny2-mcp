"""武器响应的唯一形状工厂：`weapon` / `sockets` / `stats`。

以前同一把武器在十处各长一个样（`nameEn`/`name_en`、`tier`/`rarity`、`slot_name`/`slot_label`、
`stats` 字典/`WeaponStats` 模型），调用方得为每个 intent 学一套。现在**只有这里造形状**：

- `weapon`：这把枪是什么（身份块 + `roll_kind`/`has_enhanced`/`roll_summary` + 可选实例字段 + `owned`）
- `sockets`：所有插槽（含非 perk），定义级给完整池、实例级给"这一件能换的"
- `stats`：按 `DestinyStatGroupDefinition` 的顺序输出，名字查 `DestinyStatDefinition`

三条规矩：
1. **不猜**：装备数据没读到就给 `null` + 说明，绝不把"没读"写成"没有"。
2. **列表类用精简身份块**（`lean_identity`），完整模板只给单把武器或对比。
3. 结构随 `WEAPON_SCHEMA_VERSION` 走；键集合有快照测试盯着（`tests/test_weapon_keys_snapshot.py`）。

历史键名对照（old → new）见 `TESTING_CORPUS.md` 武器章节附录。
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Callable, Mapping

from . import weapon_profile

if TYPE_CHECKING:  # pragma: no cover
    from .manifest import ManifestManager

# 结构版本：键名/语义变更时 +1，并同步快照测试与语料附录
WEAPON_SCHEMA_VERSION = 1

# 列表类精简身份块的固定字段（写死，避免"哪个 intent 有哪些键"再次发散）
LEAN_IDENTITY_KEYS: tuple[str, ...] = (
    "item_hash",
    "name",
    "name_en",
    "rarity",
    "rarity_tier",
    "weapon_type",
    "frame",
    "rpm",
    "roll_kind",
    "is_craftable",
    "icon_url",
)


def schema_block() -> dict[str, Any]:
    """响应顶层要带的结构版本（列表类也读得到）。"""
    return {"weapon_schema_version": WEAPON_SCHEMA_VERSION}


# ── stats ────────────────────────────────────────────────────────────────


def _stat_order(manifest: "ManifestManager", definition: Mapping[str, Any]) -> tuple[list[int], dict[int, bool]]:
    """属性顺序与"是否按数字展示"都来自 StatGroup；没有分组时退回定义顺序。"""
    core = definition.get("stats") or {}
    group_hash = core.get("statGroupHash")
    group = (
        manifest.get_definition("DestinyStatGroupDefinition", group_hash)
        if group_hash
        else None
    )
    scaled = (group or {}).get("scaledStats") or []
    if scaled:
        order = [int(entry.get("statHash") or 0) for entry in scaled if entry.get("statHash")]
        numeric = {
            int(entry.get("statHash") or 0): bool(entry.get("displayAsNumeric"))
            for entry in scaled
        }
        return order, numeric
    order = [
        int(entry.get("statTypeHash") or 0)
        for entry in definition.get("investmentStats") or []
        if entry.get("statTypeHash")
    ]
    return order, {}


def stat_list(
    manifest: "ManifestManager",
    definition: Mapping[str, Any] | None,
    instance_stats: Mapping[str, Any] | None = None,
    *,
    names: Any = None,
) -> list[dict[str, Any]]:
    """`stats` 块：`[{stat_hash, name, value, display, is_primary, display_as_numeric}]`。

    值优先取实例（组件 304 `itemComponents.stats`），否则取定义里的显示值
    （`definition.stats.stats[hash].value`，不是 `investmentStats` —— 后者漏掉框架加成，
    遗产的每分钟发射数会从 65 变成 30）。没名字的属性直接不输出，避免编一个名字糊弄。
    """
    if not definition:
        return []
    if names is None:
        from ..manifest_names import names_for

        names = names_for(manifest)
    core = definition.get("stats") or {}
    primary = core.get("primaryBaseStatHash")
    order, numeric = _stat_order(manifest, definition)
    instance = instance_stats if isinstance(instance_stats, Mapping) else {}

    stats: list[dict[str, Any]] = []
    seen: set[int] = set()
    for stat_hash in order:
        if not stat_hash or stat_hash in seen:
            continue
        seen.add(stat_hash)
        entry = instance.get(str(stat_hash)) or instance.get(stat_hash) or {}
        value = entry.get("value") if isinstance(entry, Mapping) else None
        if not isinstance(value, int) or isinstance(value, bool):
            value = weapon_profile.stat_value(definition, stat_hash)
        name = str(names.stat(stat_hash) or "")
        if value is None or not name:
            continue
        stats.append(
            {
                "stat_hash": stat_hash,
                "name": name,
                "value": value,
                "display": str(value),
                "is_primary": stat_hash == primary,
                "display_as_numeric": numeric.get(stat_hash),
            }
        )
    return stats


# ── sockets ──────────────────────────────────────────────────────────────


def _normalize_equipped(socket: dict[str, Any]) -> dict[str, Any]:
    """把扁平的 equipped_* 收成 `equipped: {plug_hash, name}`（没数据就是 null）。"""
    plug_hash = int(socket.pop("equipped_plug_hash", 0) or 0)
    name = str(socket.pop("equipped_name", "") or "")
    socket["equipped"] = {"plug_hash": plug_hash, "name": name} if plug_hash else None
    return socket


def column_list(
    manifest: "ManifestManager",
    definition: Mapping[str, Any] | None,
    *,
    equipped: list[int] | None = None,
) -> list[dict[str, Any]]:
    """插槽**列**清单：只有计数与"现在装的是哪个"，不展开池子。

    列表类用这个（列表回答"有哪些、能换什么"，不回答"这把枪能滚到什么"）。
    `options` 为空且 `options_available=false` 时，意思是"这次没展开"，
    不是"这栏没有可选项"——`option_count` 才是数量。
    """
    columns: list[dict[str, Any]] = []
    for column in weapon_profile.socket_columns(manifest, definition):
        item = dict(column)
        item["scope"] = "definition"
        item["options"] = []
        item["options_available"] = False
        columns.append(item)
    return with_equipped(columns, equipped, manifest)


def with_equipped(
    sockets: list[dict[str, Any]],
    equipped: list[int] | None,
    manifest: "ManifestManager" | None = None,
) -> list[dict[str, Any]]:
    """给**定义级** sockets 标上"这一件现在装的是哪个"（按 `socket_index` 对齐组件 305）。

    定义级看"能出什么"，`equipped` 看"我手上这件装的是什么" —— 两者放一起才回答得了
    "我这把要不要换"。没有实例数据时 `equipped` 为 null，不假装没装。
    """
    annotated: list[dict[str, Any]] = []
    for socket in sockets:
        item = dict(socket)
        item["equipped"] = None
        index = item.get("socket_index")
        plug_hash = 0
        if (
            equipped
            and isinstance(index, int)
            and 0 <= index < len(equipped)
        ):
            plug_hash = int(equipped[index] or 0)
        if plug_hash:
            name = ""
            if manifest is not None:
                name = str((manifest.get_item_info(plug_hash) or {}).get("name") or "")
            item["equipped"] = {"plug_hash": plug_hash, "name": name}
        annotated.append(item)
    return annotated


def socket_list(
    manifest: "ManifestManager",
    definition: Mapping[str, Any] | None,
    *,
    scope: str = "definition",
    reusable: Mapping[str, Any] | None = None,
    equipped: list[int] | None = None,
    names: Any = None,
    pairs: weapon_profile.EnhancedPairs | None = None,
    include_descriptions: bool = True,
    god_roll_lookup: Callable[[int], tuple[bool, bool]] | None = None,
) -> list[dict[str, Any]]:
    """`sockets` 块。`scope="definition"` = 完整池；`scope="instance"` = 这一件能换的（组件 310）。"""
    if scope == "instance":
        raw = weapon_profile.instance_options(
            manifest,
            definition,
            reusable,
            equipped=equipped,
            names=names,
            pairs=pairs,
            include_descriptions=include_descriptions,
            god_roll_lookup=god_roll_lookup,
        )
        return [
            _normalize_equipped(dict(socket)) | {"options_available": True}
            for socket in raw
        ]
    raw = weapon_profile.socket_options(
        manifest,
        definition,
        names=names,
        pairs=pairs,
        include_descriptions=include_descriptions,
        god_roll_lookup=god_roll_lookup,
    )
    expanded = [
        _normalize_equipped(dict(socket)) | {"options_available": True}
        for socket in raw
    ]
    return with_equipped(expanded, equipped, manifest)


def roll_summary(sockets: list[dict[str, Any]], *, scope: str = "definition") -> dict[str, Any]:
    return weapon_profile.roll_summary(sockets, scope=scope)


# ── weapon ───────────────────────────────────────────────────────────────


def instance_extras(instance: Mapping[str, Any] | None) -> dict[str, Any]:
    """组件 300 的副本字段（含缺失清单），供身份块与 `owned.instances[]` 共用。"""
    return weapon_profile.instance_fields(instance)


def weapon_block(
    manifest: "ManifestManager",
    definition: Mapping[str, Any] | None,
    *,
    sockets: list[dict[str, Any]] | None = None,
    roll_summary: dict[str, Any] | None = None,
    instance: Mapping[str, Any] | None = None,
    owned: dict[str, Any] | None = None,
    names: Any = None,
    pairs: weapon_profile.EnhancedPairs | None = None,
    fallback_name: str = "",
) -> dict[str, Any]:
    """完整身份块：定义字段 + 池子聚合 +（可选）副本字段 +（可选）持有摘要。

    `roll_summary` 可以外部传入（列表类用 `weapon_profile.roll_summary_from_columns`
    的轻量计数，避免为了一句"能滚几栏"把整张池子展开）。
    """
    block = weapon_profile.identity_fields(
        manifest, definition, names=names, fallback_name=fallback_name
    )
    if sockets is None and roll_summary is None:
        sockets = socket_list(manifest, definition, names=names, pairs=pairs)
    if roll_summary is None:
        roll_summary = weapon_profile.roll_summary(
            sockets or [], scope=str((sockets or [{}])[0].get("scope") or "definition")
        )
    block["has_enhanced"] = weapon_profile.has_enhanced(sockets or [])
    if sockets:
        block["has_enhanced"] = block["has_enhanced"] or any(
            option.get("enhanced_plug_hash")
            for socket in sockets
            for option in socket.get("options") or []
        )
    block["roll_kind"] = roll_summary["roll_kind"]
    block["roll_summary"] = roll_summary
    if instance is not None:
        fields = instance_extras(instance)
        block["gear_tier"] = fields["gear_tier"]
        block["item_level"] = fields["item_level"]
        block["quality"] = fields["quality"]
    if owned is not None:
        block["owned"] = owned
    return block


def lean_identity(
    manifest: "ManifestManager",
    definition: Mapping[str, Any] | None,
    *,
    names: Any = None,
    roll_kind: str = "",
    extra: Mapping[str, Any] | None = None,
    fallback_name: str = "",
) -> dict[str, Any]:
    """列表类专用精简身份块（字段写死，见 `LEAN_IDENTITY_KEYS`）。"""
    full = weapon_profile.identity_fields(
        manifest, definition, names=names, fallback_name=fallback_name
    )
    block: dict[str, Any] = {key: full.get(key) for key in LEAN_IDENTITY_KEYS}
    if roll_kind:
        block["roll_kind"] = roll_kind
    if extra:
        for key, value in extra.items():
            block[key] = value
    return block


def owned_block(
    instances: list[dict[str, Any]] | None,
    *,
    status: str | None = None,
) -> dict[str, Any]:
    """持有摘要（`analyze`/`type` 用）。没读账号就是 `{"status": "not_checked"}`，不装作没有。"""
    if instances is None:
        return {"status": status or "not_checked"}
    return {"status": status or "checked", "count": len(instances), "instances": instances}


def owned_instance(
    *,
    instance_id: str,
    location: str,
    power: int | None,
    is_equipped: bool,
    instance: Mapping[str, Any] | None,
    locked: bool | None,
    option_counts: Mapping[str, int] | None = None,
) -> dict[str, Any]:
    """`owned.instances[]` 的单条摘要：只给对比用得到的字段，不重复整个池子。"""
    fields = instance_extras(instance)
    entry: dict[str, Any] = {
        "instance_id": instance_id,
        "location": location,
        "power": power,
        "item_level": fields["item_level"],
        "gear_tier": fields["gear_tier"],
        "is_equipped": is_equipped,
        "locked": locked,
    }
    if option_counts:
        entry["option_counts"] = dict(option_counts)
    return entry
