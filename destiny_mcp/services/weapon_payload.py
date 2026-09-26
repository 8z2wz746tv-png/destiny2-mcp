"""武器响应的唯一形状工厂：`weapon` / `sockets` / 列表行。

以前同一把武器在十处各长一个样（`nameEn`/`name_en`、`tier`/`rarity`、`slot_name`/`slot_label`、
`stats` 字典/`WeaponStats` 模型），调用方得为每个 intent 学一套。现在**只有这里造形状**：

- `weapon`：这把枪是什么（身份块 + `roll_kind`/`has_enhanced`/`roll_summary` + 可选实例字段 + `owned`）
- `sockets`：所有插槽（含非 perk），定义级给完整池、实例级给"这一件能换的"
- `list_row`：列表行 = 精简身份块 + 副本字段 + 属性值，**不展开插槽池**
- `stats` 在 `weapon_stats_payload.py`（这个文件加了闸，只能减不能加）

三条规矩：
1. **不猜**：装备数据没读到就给 `null` + 说明，绝不把"没读"写成"没有"。
2. **列表类用精简身份块**（`lean_identity`）或列表行（`list_row`），完整模板只给单把武器或对比。
3. 结构随 `WEAPON_SCHEMA_VERSION` 走；键集合有快照测试盯着（`tests/test_weapon_keys_snapshot.py`）。

历史键名对照（old → new）见 `docs/testing/TESTING_CORPUS.md` 武器章节附录。
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
    return weapon_profile.with_equipped(columns, equipped, manifest)


def socket_list(
    manifest: "ManifestManager",
    definition: Mapping[str, Any] | None,
    *,
    scope: str = "definition",
    reusable: Mapping[str, Any] | None = None,
    equipped: list[int] | None = None,
    names: Any = None,
    pairs: weapon_profile.EnhancedPairs | None = None,
    include_descriptions: bool | None = None,
    include_icons: bool | None = None,
    god_roll_lookup: Callable[[int], tuple[bool, bool]] | None = None,
) -> list[dict[str, Any]]:
    """`sockets` 块。`scope="definition"` = 完整池；`scope="instance"` = 这一件能换的（组件 310）。

    体积口径（P6 实测）：
    - 定义级池子默认**不带** `description`/`icon_url` —— 池子回答"能滚到什么"，
      名字 + hash + 能不能滚 + 强化版 + 本地结论就够；要看效果用 `perk_description`，
      要图用 `info`。实测这两项曾占 17 KB/把（图标 13.2 + 描述 4.0）。
    - 实例级（组件 310，通常每栏 1–3 个）给全，因为那才是"这一件能换的"。
    """
    if include_descriptions is None:
        include_descriptions = scope == "instance"
    if include_icons is None:
        include_icons = scope == "instance"
    if scope == "instance":
        raw = weapon_profile.instance_options(
            manifest,
            definition,
            reusable,
            equipped=equipped,
            names=names,
            pairs=pairs,
            include_descriptions=include_descriptions,
            include_icons=include_icons,
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
        include_icons=include_icons,
        god_roll_lookup=god_roll_lookup,
    )
    expanded = [
        _normalize_equipped(dict(socket)) | {"options_available": True}
        for socket in raw
    ]
    return weapon_profile.with_equipped(expanded, equipped, manifest, pairs)


# ── weapon ───────────────────────────────────────────────────────────────


def _instance_extras(instance: Mapping[str, Any] | None) -> dict[str, Any]:
    """组件 300 的副本字段（含缺失清单），身份块与 `owned.instances[]` 共用。"""
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
        fields = _instance_extras(instance)
        block["gear_tier"] = fields["gear_tier"]
        block["item_level"] = fields["item_level"]
        block["quality"] = fields["quality"]
    if owned is not None:
        block["owned"] = owned
    # 同名多版本（复刻/重发）如实报：定义级 intent 按名字只能挑一个，不报出来调用方
    # 会把"另一个版本的池子"当成自己那把的（真机 2026-09-26：「千码凝视」特性栏 0 交集）。
    name = str(block.get("name") or "")
    if name:
        variants = weapon_profile.name_variants(manifest, name)
        if len(variants) > 1:
            block["name_variants"] = variants
            block["name_variants_note"] = (
                f"⚠️ Manifest 里同名的武器有 {len(variants)} 个版本（item_hash："
                f"{'、'.join(str(v) for v in variants)}），本次用的是 {block.get('item_hash')}。"
                "定义级池子只对这一个版本成立：要判**你手上那把**能滚什么，请用 "
                'weapon_assistant(intent="compare") 的副本行（组件 310 的可切换项）。'
            )
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


# 列表行在精简身份块之上**只多这五项**：挑枪时要用，且都不需要展开插槽池。
# 加字段前先回答：少了它，"我有哪些、哪件值得看"还答得出来吗？
LIST_ROW_EXTRA_KEYS: tuple[str, ...] = (
    "damage_type",    # 元素：组队、活动要按元素挑枪
    "ammo_type",      # 弹药：同上
    "gear_tier",      # 装备分级 T1–T5（null = 不在分级体系，不是 T0）
    "item_level",     # 赛季物品等级
    "roll_summary",   # 这个定义能滚几栏（定义级计数，不是整张池子）
)

LIST_ROW_KEYS: tuple[str, ...] = LEAN_IDENTITY_KEYS + LIST_ROW_EXTRA_KEYS

# 副本字段在列表行里**摊平**（与 `matched[]` 的列表行同一写法），不再嵌一层 `instance`
INSTANCE_ROW_KEYS: tuple[str, ...] = (
    "instance_id",
    "location",
    "power",
    "is_equipped",
    "locked",
    "tracked",
)


def list_row(
    weapon: Mapping[str, Any],
    *,
    stats: list[dict[str, Any]] | None = None,
    notes: list[str] | None = None,
) -> dict[str, Any]:
    """把完整模板投影成**列表行**（"我有哪些"这类回答的一行）。

    只留身份、副本位置/光等、属性值、逐条说明；`sockets`（插槽池）与 `options`
    （这一件能换什么）**不进列表** —— 真机实测它们占列表载荷的 2/3，而列表只是
    "有哪些"，看某一件的部件应该单查（`compare(weapon_name, item_instance_id)`）。
    """
    row: dict[str, Any] = {key: weapon.get(key) for key in LIST_ROW_KEYS}
    instance = weapon.get("instance")
    if isinstance(instance, Mapping):
        row.update({key: instance.get(key) for key in INSTANCE_ROW_KEYS})
    row["stats"] = list(stats or [])
    row["notes"] = list(notes or [])
    return row


def owned_block(
    instances: list[dict[str, Any]] | None,
    *,
    status: str | None = None,
) -> dict[str, Any]:
    """持有摘要（`analyze`/`compare` 用）。没读账号就是 `{"status": "not_checked"}`，不装作没有。"""
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
    fields = _instance_extras(instance)
    entry: dict[str, Any] = {
        "instance_id": instance_id,
        "location": location,
        "power": power,
        "item_level": fields["item_level"],
        "gear_tier": fields["gear_tier"],
        "is_equipped": is_equipped,
        "locked": locked,
    }
    if fields.get("legacy_tier"):
        entry["gear_tier_note"] = "这件不在分级体系内（组件里的 gearTier=0），不是 T0"
    if option_counts:
        entry["option_counts"] = dict(option_counts)
    return entry
