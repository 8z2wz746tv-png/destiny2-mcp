"""P1：武器画像与名称表的判定测试。

分两层：
- 纯函数用合成数据（不依赖本机 Manifest，任何机器都能跑）；
- 真实形态用本机 Manifest（没有 Manifest 时跳过），断言的是从真实数据里核对出来的事实：
  遗产 rpm=65（不是 investmentStats 的 30）、泰拉巴没有框架但有异域专属特性、
  可锻造的异域武器也有随机池、稀有度五档齐全。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from destiny_mcp.manifest import ManifestManager
from destiny_mcp.manifest_names import AMMO_TYPE_NAMES, ManifestNames, names_for
from destiny_mcp.services import weapon_profile as wp

ROOT = Path(__file__).parents[1]
MANIFEST_ZH = ROOT / "manifest" / "destiny_manifest_zh.sqlite3"
requires_manifest = pytest.mark.skipif(
    not MANIFEST_ZH.is_file(), reason="需要本机 Manifest（manifest/destiny_manifest_zh.sqlite3）"
)


# ── 纯函数：稀有度 ────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("tier", "expected"),
    [(6, "异域"), (5, "传说"), (4, "稀有"), (3, "罕见"), (2, "普通")],
)
def test_rarity_covers_all_five_tiers(tier: int, expected: str) -> None:
    assert wp.rarity_of(tier) == expected


def test_rarity_does_not_pretend_for_unknown_tiers() -> None:
    assert wp.rarity_of(9) == "未知(9)"
    assert wp.rarity_of(0) == ""
    assert wp.rarity_of(None) == ""


# ── 纯函数：固定 / 随机 ───────────────────────────────────────────────────


def _definition_with_sockets(**entries: object) -> dict:
    return {"sockets": {"socketEntries": [entries]}}


def test_socket_kinds_counts_three_states() -> None:
    definition = {
        "sockets": {
            "socketEntries": [
                {"singleInitialItemHash": 1},
                {"reusablePlugSetHash": 2},
                {"reusablePlugSetHash": 3},
                {"randomizedPlugSetHash": 4},
            ]
        }
    }

    assert wp.socket_kinds(definition) == {"fixed": 1, "option": 2, "random": 1}
    assert wp.roll_kind(definition) == "random"


def test_weapon_without_randomized_pool_is_fixed() -> None:
    definition = _definition_with_sockets(reusablePlugSetHash=2)

    assert wp.socket_kinds(definition) == {"fixed": 0, "option": 1, "random": 0}
    assert wp.roll_kind(definition) == "fixed"


# ── 纯函数：框架 ──────────────────────────────────────────────────────────


def test_frame_requires_the_frame_suffix() -> None:
    assert wp.frame_of("精确重击框架") == "精确重击框架"
    assert wp.frame_of("速射框架") == "速射框架"


def test_exotic_intrinsic_is_not_a_frame() -> None:
    """异域的固有槽放的是专属特性（泰拉巴=贪食野兽），不能当框架。"""
    assert wp.frame_of("贪食野兽") is None
    assert wp.frame_of("") is None
    assert wp.frame_of("导线步枪") is None


# ── 纯函数：属性取值 ──────────────────────────────────────────────────────


def test_display_stat_wins_over_investment_stat() -> None:
    """显示值优先：遗产的每分钟发射数投资值 30、显示值 65。"""
    definition = {
        "stats": {"stats": {str(wp.RPM_STAT_HASH): {"value": 65}}},
        "investmentStats": [{"statTypeHash": wp.RPM_STAT_HASH, "value": 30}],
    }

    assert wp.rpm_of(definition) == 65
    assert wp.stat_value(definition, wp.RPM_STAT_HASH) == 65


def test_falls_back_to_investment_stat_and_then_none() -> None:
    only_investment = {"investmentStats": [{"statTypeHash": wp.RPM_STAT_HASH, "value": 30}]}
    assert wp.rpm_of(only_investment) == 30
    assert wp.rpm_of({}) is None
    assert wp.rpm_of(None) is None


# ── 纯函数：可锻造 / 插槽归类 ─────────────────────────────────────────────


def test_craftable_comes_from_recipe_hash() -> None:
    assert wp.is_craftable({"inventory": {"recipeItemHash": 123}}) is True
    assert wp.is_craftable({"inventory": {}}) is False
    assert wp.is_craftable(None) is False


@pytest.mark.parametrize(
    ("plug_category", "expected"),
    [
        ("intrinsics", "intrinsic"),
        ("barrels", "barrel"),
        ("v400.weapon.magazines", "magazine"),
        ("frames", "trait"),
        ("origins", "trait"),
        ("v400.weapon.mod_damage", "mod"),
        ("v400.plugs.weapons.masterworks.trackers", "tracker"),
        ("mementos", "memento"),
        ("shaders", "shader"),
        ("catalysts", "catalyst"),
        ("完全没见过的类别", "other"),
    ],
)
def test_slot_kind_maps_categories(plug_category: str, expected: str) -> None:
    assert wp.slot_kind(plug_category) == expected


def test_unknown_slot_kind_keeps_a_label() -> None:
    assert wp.slot_label(wp.slot_kind("完全没见过的类别")) == "插槽"


# ── 名称表（用假 Manifest）───────────────────────────────────────────────


class _FakeManifest:
    def __init__(self) -> None:
        self._tables = {
            "DestinyStatDefinition": [
                {"hash": 4284893193, "displayProperties": {"name": "每分钟发射数"}},
                {"hash": 999, "displayProperties": {}},
            ],
            "DestinyDamageTypeDefinition": [
                {"hash": 3373582085, "enumValue": 1, "displayProperties": {"name": "动能伤害"}},
                {"hash": 2303181850, "enumValue": 2, "displayProperties": {"name": "电弧伤害"}},
                {"hash": 3949783978, "enumValue": 7, "displayProperties": {"name": "缚丝"}},
            ],
            "DestinyBreakerTypeDefinition": [
                {"hash": 485622768, "enumValue": 1, "displayProperties": {"name": "贯穿护盾"}},
                {"hash": -1116161591, "enumValue": 3, "displayProperties": {"name": "眩晕"}},
            ],
        }

    def iter_definitions(self, table: str, *, limit: int = 1000) -> list[dict]:
        return list(self._tables.get(table, []))

    def get_definition(self, table: str, hash_id: int) -> dict | None:
        for row in self._tables.get(table, []):
            if row.get("hash") == hash_id:
                return row
        return None


def test_damage_type_strips_the_suffix_and_accepts_enum_or_hash() -> None:
    names = ManifestNames(_FakeManifest())  # type: ignore[arg-type]

    assert names.damage_type(3373582085) == "动能"
    assert names.damage_type(2303181850) == "电弧"
    assert names.damage_type(2) == "电弧"
    assert names.damage_type(3949783978) == "缚丝"
    assert names.damage_type(0) == ""
    assert names.damage_type(None) == ""


def test_breaker_type_maps_enum_and_hash() -> None:
    names = ManifestNames(_FakeManifest())  # type: ignore[arg-type]

    assert names.breaker_type(3) == "眩晕"
    assert names.breaker_type(1) == "贯穿护盾"
    assert names.breaker_type(485622768) == "贯穿护盾"
    assert names.breaker_type(0) == ""


def test_unknown_names_fall_back_softly() -> None:
    names = ManifestNames(_FakeManifest())  # type: ignore[arg-type]

    assert names.stat(999) == ""
    assert names.damage_type(4242) == ""
    assert names.breaker_type(9) == ""
    assert ManifestNames.ammo_type(2) == AMMO_TYPE_NAMES[2]
    assert ManifestNames.ammo_type(None) == ""


def test_names_are_cached_per_manifest() -> None:
    fake = _FakeManifest()

    assert names_for(fake) is names_for(fake)


# ── 真实形态 ─────────────────────────────────────────────────────────────


@pytest.fixture(scope="module")
def real_manifest() -> ManifestManager:
    """直接加载本机 sqlite；不走网络（ensure_loaded 需要 bungie client）。"""
    manager = ManifestManager()
    manager._load_from_file()
    return manager


def _definition(manifest: ManifestManager, name: str) -> dict:
    hits = manifest.search(name, limit=5)
    weapon = next((hit for hit in hits if hit.get("itemType") == 3), None)
    assert weapon is not None, f"Manifest 里找不到武器 {name}"
    return manifest.get_item_definition(weapon["itemHash"]) or {}


@requires_manifest
def test_legacy_rpm_uses_the_display_value(real_manifest: ManifestManager) -> None:
    """遗产：投资值 30、显示值 65；身份块必须给 65（清单里也写 65）。"""
    definition = _definition(real_manifest, "遗产")

    assert wp.rpm_of(definition) == 65
    assert wp.roll_kind(definition) == "random"
    assert wp.is_craftable(definition) is True


@requires_manifest
def test_exotic_has_intrinsic_but_no_frame(real_manifest: ManifestManager) -> None:
    definition = _definition(real_manifest, "泰拉巴")
    fields = wp.identity_fields(real_manifest, definition)

    assert fields["rarity"] == "异域"
    assert fields["frame"] is None
    assert fields["intrinsic"] == "贪食野兽"
    assert wp.roll_kind(definition) == "fixed"


@requires_manifest
def test_craftable_exotic_still_rolls_random(real_manifest: ManifestManager) -> None:
    """枯骨鳞片：异域但可锻造，有随机池 —— 所以判据是随机池，不是稀有度。"""
    definition = _definition(real_manifest, "枯骨鳞片")

    assert wp.roll_kind(definition) == "random"
    assert wp.is_craftable(definition) is True


@requires_manifest
def test_breaker_type_and_rarity_on_real_weapons(real_manifest: ManifestManager) -> None:
    names = names_for(real_manifest)

    queenbreaker = wp.identity_fields(real_manifest, _definition(real_manifest, "弑后者"), names=names)
    assert queenbreaker["breaker_type"] == "眩晕"  # 屏障勇士
    assert queenbreaker["rarity"] == "异域"
    assert queenbreaker["frame"] is None  # 固有槽是「导线步枪」，不是框架

    common = wp.identity_fields(real_manifest, _definition(real_manifest, "刚愎自用"), names=names)
    assert common["rarity"] == "普通"
    assert common["frame"] == "攻击型框架"

    rare = wp.identity_fields(real_manifest, _definition(real_manifest, "Ψ卷云II"), names=names)
    assert rare["rarity"] == "稀有"
    assert rare["rpm"] == 540


@requires_manifest
def test_identity_fields_cover_the_agreed_keys(real_manifest: ManifestManager) -> None:
    fields = wp.identity_fields(real_manifest, _definition(real_manifest, "遗产"))

    assert set(fields) == {
        "item_hash", "name", "name_en", "rarity", "rarity_tier", "weapon_type",
        "frame", "intrinsic", "rpm", "ammo_type", "damage_type", "breaker_type",
        "is_craftable", "trait_ids", "watermark", "description", "icon_url",
    }
    assert fields["name_en"] == "Heritage"
    assert fields["ammo_type"] == "绿弹"
    assert fields["damage_type"] == "动能"
    assert fields["trait_ids"], "traitIds 不该为空（武器族/版本）"


@requires_manifest
def test_no_duplicate_rarity_table_left_in_services() -> None:
    """稀有度表只能有一份：服务里不许再出现 {5: ..., 6: ...} 这种局部表。"""
    offenders = []
    for path in (ROOT / "destiny_mcp").rglob("*.py"):
        if path.name in {"weapon_profile.py"}:
            continue
        text = path.read_text(encoding="utf-8")
        if "{5:" in text and "6:" in text and "传说" in text:
            offenders.append(path.relative_to(ROOT).as_posix())

    assert not offenders, f"这些文件里还有自己的稀有度表：{offenders}"
