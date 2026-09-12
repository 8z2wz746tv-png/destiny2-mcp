"""P2：定义级插槽、强化配对、退役标记、perk 数值效果、god_roll 三态。

真实数据部分依赖本机 Manifest（没有就跳过）；合成部分覆盖边界，任何机器都能跑。
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from destiny_mcp.exceptions import ManifestError
from destiny_mcp.manifest import ManifestManager
from destiny_mcp.manifest_names import names_for
from destiny_mcp.services import weapon_profile as wp
from destiny_mcp.services.perk_service import PerkService

ROOT = Path(__file__).parents[1]
MANIFEST_ZH = ROOT / "manifest" / "destiny_manifest_zh.sqlite3"
requires_manifest = pytest.mark.skipif(
    not MANIFEST_ZH.is_file(), reason="需要本机 Manifest（manifest/destiny_manifest_zh.sqlite3）"
)

WEAPON_PERKS = 4241085061


# ── 合成替身：一把"随机 roll"武器 ─────────────────────────────────────────


class _StubManifest:
    """有随机池 + 一个装饰插槽，plug 带 can_roll / 效果 / 强化配对。"""

    def __init__(self) -> None:
        self._items = {
            300: {"name": "狂暴", "icon": "", "investmentStats": [{"statTypeHash": 1, "value": 10}]},
            301: {"name": "退役的 Perk", "icon": ""},
            302: {"name": "强化狂暴", "icon": ""},
            900: {"name": "星象仪", "icon": ""},
            1: {"name": "射程", "icon": ""},
        }

    def search(self, query: str, *, limit: int = 20) -> list[dict]:
        return [{"itemHash": 100, "name": "测试武器", "itemType": 3}]

    def get_item_definition(self, item_hash: int) -> dict:
        if item_hash in self._items:
            return dict(self._items[item_hash])
        if item_hash == 100:
            return {
                "hash": 100,
                "displayProperties": {"name": "测试武器"},
                "inventory": {"tierType": 5},
                "sockets": {
                    "socketCategories": [
                        {"socketCategoryHash": WEAPON_PERKS, "socketIndexes": [0, 1]},
                    ],
                    "socketEntries": [
                        {"randomizedPlugSetHash": 200},
                        {"reusablePlugSetHash": 201},
                    ],
                },
            }
        return {}

    def get_plug_set_plugs(self, plug_set_hash: int) -> list[dict]:
        if plug_set_hash == 200:
            return [
                {"plugItemHash": 300, "name": "狂暴", "plugCategoryIdentifier": "frames",
                 "currentlyCanRoll": True},
                {"plugItemHash": 301, "name": "退役的 Perk", "plugCategoryIdentifier": "frames",
                 "currentlyCanRoll": False},
                {"plugItemHash": 302, "name": "强化狂暴", "plugCategoryIdentifier": "frames",
                 "currentlyCanRoll": True},
            ]
        return [
            {"plugItemHash": 900, "name": "星象仪", "plugCategoryIdentifier": "shaders",
             "currentlyCanRoll": True},
        ]

    def get_item_info(self, plug_hash: int) -> dict:
        return self._items.get(plug_hash, {})

    def get_plug_category_identifier(self, plug_hash: int) -> str:
        return "frames" if plug_hash in (300, 301, 302) else "shaders"

    def get_sandbox_perk_description(self, plug_hash: int) -> None:
        return None

    def get_definition(self, table: str, hash_id: int) -> dict | None:
        if table == "DestinyStatDefinition" and hash_id == 1:
            return {"displayProperties": {"name": "射程"}}
        return None


@pytest.fixture
def stub_manifest() -> _StubManifest:
    return _StubManifest()


@pytest.fixture
def stub_pairs() -> wp.EnhancedPairs:
    return wp.EnhancedPairs({"300": {"enhanced": 302, "name": "狂暴", "category": "frames"}})


def test_socket_options_covers_every_socket_kind(stub_manifest, stub_pairs) -> None:
    definition = stub_manifest.get_item_definition(100)

    sockets = wp.socket_options(
        stub_manifest, definition, names=names_for(stub_manifest), pairs=stub_pairs
    )

    assert [socket["kind"] for socket in sockets] == ["trait", "shader"]
    assert [socket["option_count"] for socket in sockets] == [3, 1]
    assert all(socket["scope"] == "definition" for socket in sockets)
    assert [socket["socket_index"] for socket in sockets] == [0, 1]


def test_can_roll_marks_retired_perks(stub_manifest, stub_pairs) -> None:
    definition = stub_manifest.get_item_definition(100)

    sockets = wp.socket_options(
        stub_manifest, definition, names=names_for(stub_manifest), pairs=stub_pairs
    )
    options = {option["name"]: option for option in sockets[0]["options"]}

    assert options["狂暴"]["can_roll"] is True
    assert options["退役的 Perk"]["can_roll"] is False


def test_stat_effects_come_from_investment_stats(stub_manifest, stub_pairs) -> None:
    definition = stub_manifest.get_item_definition(100)

    sockets = wp.socket_options(
        stub_manifest, definition, names=names_for(stub_manifest), pairs=stub_pairs
    )

    assert sockets[0]["options"][0]["stat_effects"] == [{"stat": "射程", "value": 10}]


def test_enhanced_pair_points_both_ways(stub_manifest, stub_pairs) -> None:
    definition = stub_manifest.get_item_definition(100)

    sockets = wp.socket_options(
        stub_manifest, definition, names=names_for(stub_manifest), pairs=stub_pairs
    )
    options = {option["name"]: option for option in sockets[0]["options"]}

    # P6：`enhanced` 布尔与 `enhanced_plug_hash` 表达同一件事，只留后者
    assert options["狂暴"]["enhanced_plug_hash"] == 302
    assert options["狂暴"]["enhanced_plug_hash"] == 302
    assert options["强化狂暴"]["enhanced_plug_hash"] == 302  # 自己就是强化版时指向自己
    assert options["强化狂暴"]["enhanced_plug_hash"] == 302  # 指自己 = "我就是强化版"
    assert options["退役的 Perk"]["enhanced_plug_hash"] == 0
    assert wp.has_enhanced(sockets) is True


def test_cosmetic_sockets_are_capped_and_say_so() -> None:
    """着色器/大师杰作这类插槽动辄上百个选项：给计数 + 少量样本 + truncated 标记。"""
    manifest = SimpleNamespace(
        search=lambda query, limit=20: [{"itemHash": 1, "itemType": 3, "name": "测试"}],
        get_item_definition=lambda item_hash: {
            "hash": 1,
            "inventory": {"tierType": 5},
            "sockets": {
                "socketEntries": [{"reusablePlugSetHash": 500}],
            },
        },
        get_plug_set_plugs=lambda plug_set_hash: [
            {"plugItemHash": 1000 + index, "name": f"着色器{index}",
             "plugCategoryIdentifier": "shaders", "currentlyCanRoll": True}
            for index in range(50)
        ],
        get_item_info=lambda plug_hash: {"name": f"着色器{plug_hash - 1000}", "icon": ""},
        get_plug_category_identifier=lambda plug_hash: "shaders",
        get_sandbox_perk_description=lambda plug_hash: None,
    )

    sockets = wp.socket_options(manifest, manifest.get_item_definition(1), names=names_for(manifest))  # type: ignore[arg-type]

    assert sockets[0]["option_count"] == 50
    assert len(sockets[0]["options"]) == wp.NON_DETAIL_OPTION_SAMPLE
    assert sockets[0]["options_truncated"] is True


def test_roll_summary_and_fixed_perks(stub_manifest, stub_pairs) -> None:
    definition = stub_manifest.get_item_definition(100)
    sockets = wp.socket_options(
        stub_manifest, definition, names=names_for(stub_manifest), pairs=stub_pairs
    )

    summary = wp.roll_summary(sockets)

    assert summary["roll_kind"] == "random"
    assert summary["random_columns"] == ["特性"]
    assert summary["option_counts"] == {"特性": 3}
    # 特性栏有 3 个选项 → 不属于"固定内容"
    assert wp.fixed_roll_perks(sockets) == []


# ── god_roll 三态 ────────────────────────────────────────────────────────


class _WishlistWithPerks:
    def has_data(self, item_hash: int) -> bool:
        return True

    def get_god_roll_perks(self, item_hash: int):
        return SimpleNamespace(
            pve_perks={300}, pvp_perks={302}, sources=["某愿单.txt"]
        )


async def test_god_roll_recommended_branch(stub_manifest) -> None:
    service = PerkService(  # type: ignore[arg-type]
        stub_manifest, popularity=None, wishlist=_WishlistWithPerks()
    )

    result = await service.get_god_roll("测试武器")

    assert result["kind"] == "recommended"
    assert result["source"] == "dim_wishlist"
    assert result["source_detail"] == "某愿单.txt"
    assert result["pve"] == [{"plug_hash": 300, "name": "狂暴"}]
    assert result["pvp"] == [{"plug_hash": 302, "name": "强化狂暴"}]


class _FixedStubManifest(_StubManifest):
    """同样的武器，但固有槽只有一个固定件 —— 固定 roll 形态。"""

    def get_item_definition(self, item_hash: int) -> dict:
        definition = super().get_item_definition(item_hash)
        if "sockets" not in definition:  # plug 定义原样返回，只改武器
            return definition
        definition["sockets"]["socketEntries"] = [{"singleInitialItemHash": 300}]
        definition["sockets"]["socketCategories"] = [
            {"socketCategoryHash": 3956125808, "socketIndexes": [0]},
        ]
        return definition

    def get_plug_category_identifier(self, plug_hash: int) -> str:
        return "intrinsics"


async def test_god_roll_fixed_branch_lists_fixed_perks() -> None:
    """固定 roll 武器：说清没有推荐，并列出真正固定的内容。"""
    service = PerkService(  # type: ignore[arg-type]
        _FixedStubManifest(), popularity=None, wishlist=_WishlistWithPerks()
    )

    result = await service.get_god_roll("测试武器")

    assert result["kind"] == "fixed"
    assert result["source"] == "manifest"
    assert [perk["name"] for perk in result["fixed_perks"]] == ["狂暴"]
    assert "固定" in result["note"]


class _MissingStubManifest(_StubManifest):
    def search(self, query: str, *, limit: int = 20) -> list[dict]:
        return []


async def test_god_roll_unknown_weapon_still_raises() -> None:
    service = PerkService(  # type: ignore[arg-type]
        _MissingStubManifest(), popularity=None, wishlist=_WishlistWithPerks()
    )

    with pytest.raises(ManifestError):
        await service.get_god_roll("不存在的武器")


# ── 真实数据 ─────────────────────────────────────────────────────────────


@pytest.fixture(scope="module")
def real_manifest() -> ManifestManager:
    manager = ManifestManager()
    manager._load_from_file()
    return manager


def _definition(manifest: ManifestManager, name: str) -> dict:
    hits = manifest.search(name, limit=5)
    weapon = next((hit for hit in hits if hit.get("itemType") == 3), None)
    assert weapon is not None, f"Manifest 里找不到武器 {name}"
    return manifest.get_item_definition(weapon["itemHash"]) or {}


@requires_manifest
def test_real_weapon_covers_masterwork_mod_and_memento(real_manifest: ManifestManager) -> None:
    """非 perk 插槽以前全丢；现在大师杰作/模组/纪念物/追踪器都要在。"""
    sockets = wp.socket_options(
        real_manifest, _definition(real_manifest, "遗产"), names=names_for(real_manifest)
    )
    kinds = {socket["kind"] for socket in sockets}

    assert {"intrinsic", "barrel", "magazine", "trait", "mod", "masterwork", "tracker", "memento"} <= kinds


@requires_manifest
def test_real_weapon_caps_cosmetic_options(real_manifest: ManifestManager) -> None:
    """遗产的着色器插槽有几百个选项、大师杰作上百个：必须被裁并标记。"""
    sockets = wp.socket_options(
        real_manifest, _definition(real_manifest, "遗产"), names=names_for(real_manifest)
    )
    capped = {socket["slot"]: socket for socket in sockets if socket.get("options_truncated")}

    assert capped, "应当有被裁的装饰类插槽"
    for socket in capped.values():
        assert len(socket["options"]) == wp.NON_DETAIL_OPTION_SAMPLE
        assert socket["option_count"] > len(socket["options"])


@requires_manifest
def test_real_stat_effects_on_a_barrel(real_manifest: ManifestManager) -> None:
    """箭头制退器：后坐方向 +30、操控性 +10（plug 自带 investmentStats）。"""
    sockets = wp.socket_options(
        real_manifest, _definition(real_manifest, "遗产"), names=names_for(real_manifest)
    )
    barrel = next(socket for socket in sockets if socket["kind"] == "barrel")
    option = next(item for item in barrel["options"] if item["name"] == "箭头制退器")

    effects = {entry["stat"]: entry["value"] for entry in option.get("stat_effects") or []}

    assert effects.get("后坐方向") == 30
    assert effects.get("操控性") == 10


@requires_manifest
def test_real_enhanced_pairs_are_usable(real_manifest: ManifestManager) -> None:
    pairs = wp.load_enhanced_pairs()

    assert pairs.size >= 200, f"配对表太小：{pairs.size}"
    sockets = wp.socket_options(
        real_manifest, _definition(real_manifest, "遗产"), names=names_for(real_manifest), pairs=pairs
    )
    trait = next(socket for socket in sockets if socket["kind"] == "trait")

    assert wp.has_enhanced(sockets) is True
    assert any(option["enhanced_plug_hash"] for option in trait["options"]), "特性栏应能指出强化版"
