"""P3：副本级数据（组件 300 / 305 / 310）——T 级、上锁、以及"这一件能换成什么"。

真机核对到的事实（写进注释也写进断言，防止以后被"看起来差不多"改掉）：
- `item.state` 的位序按官方生成的 `ItemState`：Locked=1、Tracked=2、Masterwork=4、
  Crafted=8、HighlightedObjective=16（老资料写的 Crafted=4 / Masterworked=32 是错的）；
- 每栏可插数由 310 决定，不是由 T 级算出来的：非锻造件 T5 大多 3、T4/T3 各 2、T2 只有 1，
  锻造件（Crafted 位）通常只列当前选中的那一个，无 T 级的旧装备 1–6 不等；
- 310 里会出现"这一栏这个副本一个都插不进去"（canInsert 全 false），此时该栏不输出。
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from destiny_mcp.manifest import ManifestManager
from destiny_mcp.manifest_names import names_for
from destiny_mcp.models import WeaponDetail
from destiny_mcp.services import weapon_profile as wp
from destiny_mcp.services.weapon_detail_service import WeaponDetailService
from destiny_mcp.utils.hash_utils import to_signed

VAULT_BUCKET = 138197802


# ── 纯函数：state 位与副本字段 ────────────────────────────────────────────


def test_item_state_bit_order_matches_current_official_enum() -> None:
    assert wp.ITEM_STATE_LOCKED == 1
    assert wp.ITEM_STATE_TRACKED == 2
    assert wp.ITEM_STATE_MASTERWORK == 4
    assert wp.ITEM_STATE_CRAFTED == 8


@pytest.mark.parametrize(
    ("state", "locked", "tracked"),
    [(0, False, False), (1, True, False), (2, False, True), (3, True, True), (5, True, False)],
)
def test_item_state_flags_decodes_bits(state, locked, tracked) -> None:
    assert wp.item_state_flags(state) == {"locked": locked, "tracked": tracked}


@pytest.mark.parametrize("state", [None, "1", 1.0, True])
def test_item_state_flags_stays_unknown_instead_of_guessing(state) -> None:
    assert wp.item_state_flags(state) == {"locked": None, "tracked": None}


def test_instance_fields_reads_gear_tier_level_and_quality() -> None:
    fields = wp.instance_fields({"gearTier": 5, "itemLevel": 20, "quality": 7})

    assert fields["gear_tier"] == 5
    assert fields["item_level"] == 20
    assert fields["quality"] == 7
    assert fields["missing"] == []


@pytest.mark.parametrize("instance", [None, {}, {"gearTier": None}, {"gearTier": True}])
def test_instance_fields_are_null_with_a_reason_when_unread(instance) -> None:
    fields = wp.instance_fields(instance)

    assert fields["gear_tier"] is None
    assert fields["missing"] == ["gear_tier", "item_level", "quality"]


def test_weapon_detail_defaults_are_empty_not_fabricated() -> None:
    """P4 形状：身份块在 `weapon` 里，副本字段为 null 而不是 False。"""
    detail = WeaponDetail()

    assert detail.weapon == {}
    assert detail.sockets == []
    assert detail.options == []
    assert detail.stats == []
    assert detail.notes == []


# ── 纯函数：实例级可换部件 ────────────────────────────────────────────────


class _StubManifest:
    """3 条特性 + 1 个装饰栏；插件名/图标可查。"""

    def __init__(self) -> None:
        self._items = {n: {"name": name, "icon": ""} for n, name in (
            (300, "狂暴"), (301, "快速命中"), (302, "高强度弹药"), (303, "丰裕"),
            (900, "星象仪"), (800, "斩首武器"),
        )}

    def get_item_definition(self, item_hash: int) -> dict:
        if item_hash == 100:
            return {
                "sockets": {"socketEntries": [
                    {"randomizedPlugSetHash": 200},
                    {"randomizedPlugSetHash": 201},
                    {"reusablePlugSetHash": 202},
                ]},
            }
        return {}

    def get_plug_set_plugs(self, plug_set_hash: int) -> list[dict]:
        return {
            200: [{"plugItemHash": n, "name": self._items[n]["name"],
                   "plugCategoryIdentifier": "frames"} for n in (300, 301, 302, 303)],
            201: [{"plugItemHash": 800, "name": "斩首武器", "plugCategoryIdentifier": "frames"}],
            202: [{"plugItemHash": 900, "name": "星象仪", "plugCategoryIdentifier": "shaders"}],
        }[plug_set_hash]

    def get_item_info(self, plug_hash: int) -> dict:
        return self._items.get(plug_hash, {})

    def get_plug_category_identifier(self, plug_hash: int) -> str:
        return "shaders" if plug_hash == 900 else "frames"

    def get_sandbox_perk_description(self, plug_hash: int) -> None:
        return None

    def get_definition(self, table: str, hash_id: int) -> dict | None:
        return None


@pytest.fixture
def stub() -> _StubManifest:
    return _StubManifest()


def test_instance_options_keep_only_insertable_plugs(stub) -> None:
    reusable = {"plugs": {"0": [
        {"plugItemHash": 300, "canInsert": True},
        {"plugItemHash": 301, "canInsert": False},
        {"plugItemHash": 302, "canInsert": True},
        {"plugItemHash": 302, "canInsert": True},  # 重复项只留一个
    ]}}

    options = wp.instance_options(
        stub, stub.get_item_definition(100), reusable, names=names_for(stub)
    )

    assert [socket["socket_index"] for socket in options] == [0]
    socket = options[0]
    assert socket["scope"] == "instance"
    assert socket["kind"] == "trait"
    # 同名栏位统一编号：这把替身有两个特性栏，所以是「特性1」
    assert socket["slot"] == "特性1"
    assert socket["option_count"] == 2
    assert [option["name"] for option in socket["options"]] == ["狂暴", "高强度弹药"]


def test_instance_options_skip_columns_this_copy_cannot_change(stub) -> None:
    reusable = {"plugs": {"0": [{"plugItemHash": 300, "canInsert": False}]}}

    assert wp.instance_options(stub, stub.get_item_definition(100), reusable) == []


def test_instance_options_report_what_is_equipped_now(stub) -> None:
    reusable = {"plugs": {"0": [{"plugItemHash": 300, "canInsert": True},
                                {"plugItemHash": 301, "canInsert": True}]}}

    options = wp.instance_options(
        stub, stub.get_item_definition(100), reusable, equipped=[0, 301, 0]
    )

    assert options[0]["socket_index"] == 0
    assert options[0]["equipped_plug_hash"] == 0
    assert options[0]["equipped_name"] == ""


def test_instance_options_fall_back_to_definition_labels_for_unknown_index(stub) -> None:
    """310 里的槽位下标在定义里找不到时，也不能丢数据。"""
    reusable = {"plugs": {"9": [{"plugItemHash": 900, "canInsert": True}]}}

    options = wp.instance_options(stub, stub.get_item_definition(100), reusable)

    assert options[0]["socket_index"] == 9
    assert options[0]["kind"] == "shader"
    assert options[0]["slot"] == "着色器"


@pytest.mark.parametrize("reusable", [None, {}, {"plugs": None}, {"plugs": "x"}])
def test_instance_options_are_empty_without_component_310(stub, reusable) -> None:
    assert wp.instance_options(stub, stub.get_item_definition(100), reusable) == []


def test_instance_options_truncate_bulky_columns_like_definition_scope(stub) -> None:
    stub._items.update({n: {"name": f"着色器{n}", "icon": ""} for n in range(910, 920)})
    reusable = {"plugs": {"2": [{"plugItemHash": n, "canInsert": True} for n in range(910, 920)]}}

    options = wp.instance_options(stub, stub.get_item_definition(100), reusable)

    assert options[0]["option_count"] == 10
    assert len(options[0]["options"]) == wp.NON_DETAIL_OPTION_SAMPLE
    assert options[0]["options_truncated"] is True


def test_instance_options_keep_roll_columns_full_but_only_sample_mods(stub) -> None:
    """列表里每件副本都展开十几个模组会撑爆响应，roll 栏才是"能换成什么"的重点。"""
    stub._items.update({n: {"name": f"模组{n}", "icon": ""} for n in range(500, 512)})
    definition = {"sockets": {"socketEntries": [
        {"randomizedPlugSetHash": 200},
        {"reusablePlugSetHash": 203},
    ]}}
    stub.get_plug_set_plugs = lambda h: {
        200: [{"plugItemHash": n, "name": stub._items[n]["name"],
               "plugCategoryIdentifier": "frames"} for n in (300, 301, 302, 303)],
        203: [{"plugItemHash": n, "name": stub._items[n]["name"],
               "plugCategoryIdentifier": "v400.weapon.mod_guns"} for n in range(500, 512)],
    }[h]
    reusable = {"plugs": {
        "0": [{"plugItemHash": n, "canInsert": True} for n in (300, 301, 302, 303)],
        "1": [{"plugItemHash": n, "canInsert": True} for n in range(500, 512)],
    }}

    options = {socket["kind"]: socket for socket in wp.instance_options(stub, definition, reusable)}

    assert options["trait"]["option_count"] == 4
    assert len(options["trait"]["options"]) == 4
    assert "options_truncated" not in options["trait"]
    assert options["mod"]["option_count"] == 12
    assert len(options["mod"]["options"]) == wp.NON_DETAIL_OPTION_SAMPLE
    assert options["mod"]["options_truncated"] is True


# ── 服务层：组件接得上、缺了要说清楚 ──────────────────────────────────────


@pytest.fixture
def account():
    manifest = ManifestManager()
    item_hash = 0xF0000100
    info = {
        "itemHash": item_hash, "name": "测试手炮", "itemType": 3,
        "itemTypeNameDisplay": "手炮", "tier": 5, "icon": "",
    }
    manifest._hash_index[item_hash] = info
    manifest._hash_index[to_signed(item_hash)] = info
    manifest._name_index[info["name"]] = [info]
    manifest.get_item_definition = lambda h: {
        "sockets": {"socketEntries": [{"randomizedPlugSetHash": 800}]},
    } if h == item_hash else {}
    manifest.get_plug_set_plugs = lambda h: [
        {"plugItemHash": 300, "name": "狂暴", "plugCategoryIdentifier": "frames"},
    ]
    manifest.get_plug_category_identifier = lambda h: "frames"
    manifest.get_sandbox_perk_description = lambda h: None
    manifest.get_item_description = lambda h: ""
    manifest.get_item_info = lambda h: (
        {"name": "狂暴", "icon": ""} if h == 300
        else (info if h == item_hash else {})
    )

    profile = {
        "profileInventory": {"data": {"items": [
            {"itemHash": item_hash, "itemInstanceId": "inst-1", "bucketHash": VAULT_BUCKET,
             "state": 1},
        ]}},
        "characters": {"data": {"t": {"classType": 0}}},
        "characterInventories": {"data": {"t": {"items": []}}},
        "characterEquipment": {"data": {"t": {"items": []}}},
        "itemComponents": {
            "instances": {"data": {"inst-1": {
                "gearTier": 4, "itemLevel": 12, "quality": 3,
                "primaryStat": {"value": 1990}, "damageTypeHash": 0,
            }}},
            "sockets": {"data": {"inst-1": {"sockets": [{"plugHash": 300}]}}},
            "stats": {"data": {"inst-1": {"stats": {}}}},
            "reusablePlugs": {"data": {"inst-1": {"plugs": {
                "0": [{"plugItemHash": 300, "canInsert": True}],
            }}}},
        },
    }
    resolver = SimpleNamespace(
        resolve_player=AsyncMock(return_value={"membership_id": "test", "membership_type": 3}),
        get_profile=AsyncMock(return_value=profile),
    )
    return SimpleNamespace(
        manifest=manifest, profile=profile, service=WeaponDetailService(manifest, resolver)
    )


async def test_detail_carries_instance_fields_and_options(account):
    result = await account.service.get_weapon_details_by_type("test", "")
    detail = result.weapons[0]
    weapon = detail.weapon

    assert (weapon["gear_tier"], weapon["item_level"], weapon["quality"]) == (4, 12, 3)
    assert weapon["instance"]["locked"] is True
    assert weapon["instance"]["tracked"] is False
    assert weapon["instance"]["location"] == "仓库"
    assert [option["slot"] for option in detail.options] == ["特性"]
    assert detail.options[0]["scope"] == "instance"
    assert detail.options[0]["equipped"] == {"plug_hash": 300, "name": "狂暴"}
    assert [socket["slot"] for socket in detail.sockets] == ["特性"]
    assert detail.sockets[0]["equipped"] == {"plug_hash": 300, "name": "狂暴"}
    assert [stat["name"] for stat in detail.stats] == []
    assert detail.notes == []


async def test_missing_component_310_is_explained_not_silent(account):
    del account.profile["itemComponents"]["reusablePlugs"]

    detail = (await account.service.get_weapon_details_by_type("test", "")).weapons[0]

    assert detail.options == []
    assert any("310" in note for note in detail.notes)


async def test_missing_component_300_is_null_with_a_reason(account):
    account.profile["itemComponents"]["instances"]["data"] = {}

    detail = (await account.service.get_weapon_details_by_type("test", "")).weapons[0]

    assert detail.weapon["gear_tier"] is None
    assert detail.weapon["item_level"] is None
    assert detail.weapon["quality"] is None
    assert any("300" in note for note in detail.notes)


async def test_missing_state_field_is_null_with_a_reason(account):
    del account.profile["profileInventory"]["data"]["items"][0]["state"]

    detail = (await account.service.get_weapon_details_by_type("test", "")).weapons[0]

    assert detail.weapon["instance"]["locked"] is None
    assert detail.weapon["instance"]["tracked"] is None
    assert any("state" in note for note in detail.notes)
