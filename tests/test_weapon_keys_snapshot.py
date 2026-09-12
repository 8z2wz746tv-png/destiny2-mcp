"""P4：武器各 intent 的**键集合**快照。

为什么用快照而不是逐个字段断言：形状统一的目的是"同一把武器到哪都长一个样"，
最容易悄悄退化的也正是键集合（少一个 `frame`、冒出一个 `tier`、`slot_name` 又回来）。
这里把每个 intent 的顶层键、身份块键、插槽键、选项键、属性键全部钉死；
改了形状就必须改这张表，改的人得先回答"调用方要不要跟着改"。

同时也覆盖工具层分支（例如 perk_pool 曾经漏了 await —— 服务单测是同步替身，测不出来）。
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from destiny_mcp.services import profile_components, weapon_payload
from destiny_mcp.services.manifest_query_service import ManifestQueryService
from destiny_mcp.services.perk_service import PerkService
from destiny_mcp.services.weapon_analysis_service import WeaponAnalysisService
from destiny_mcp.services.weapon_compare_service import WeaponCompareService
from destiny_mcp.services.weapon_detail_service import WeaponDetailService
from destiny_mcp.services.weapon_roll_filter_service import WeaponRollFilterService
from destiny_mcp.tools.assistants import weapon_assistant

WEAPON_HASH = 100
POOL_INT = 700
POOL_TRAIT = 701
VALUE = 2**32 - WEAPON_HASH  # 账号数据是无符号的


class _Manifest:
    """一把随机 roll 传说武器的替身：固有 + 两条特性栏 + 一个模组栏。"""

    def __init__(self) -> None:
        self._items = {
            WEAPON_HASH: {"itemHash": WEAPON_HASH, "name": "测试武器", "itemType": 3,
                          "itemTypeNameDisplay": "手炮", "tier": 5, "icon": "/w.png"},
            900: {"name": "精确重击框架", "icon": "/intrinsic.png"},
            901: {"name": "快速命中", "icon": "/p1.png"},
            902: {"name": "萤火虫", "icon": "/p2.png"},
            903: {"name": "战术模组", "icon": "/mod.png"},
        }

    def search(self, query: str, *, limit: int = 20) -> list[dict]:
        if query == "快速命中":
            return [{"itemHash": 901, "name": "快速命中", "itemType": 19, "icon": "/p1.png"}]
        return [{"itemHash": WEAPON_HASH, "name": "测试武器", "itemType": 3, "icon": "/w.png"}]

    def list_weapon_catalog(self, weapon_type: str = "", *, weapon_name: str = "") -> list[dict]:
        return [{"itemHash": WEAPON_HASH, "name": "测试武器", "itemType": 3, "icon": "/w.png"}]

    def get_item_definition(self, item_hash: int) -> dict:
        if item_hash == WEAPON_HASH:
            return {
                "hash": WEAPON_HASH,
                "itemType": 3,
                "itemTypeDisplayName": "手炮",
                "displayProperties": {"name": "测试武器", "icon": "/w.png", "description": "说明"},
                "inventory": {"tierType": 5},
                "equippingBlock": {"ammoType": 1},
                "defaultDamageType": 1,
                "breakerType": 2,
                "stats": {
                    "statGroupHash": 5,
                    "primaryBaseStatHash": 4284893193,
                    "stats": {"4284893193": {"statHash": 4284893193, "value": 140}},
                },
                "sockets": {"socketEntries": [
                    {"singleInitialItemHash": 900},
                    {"randomizedPlugSetHash": POOL_TRAIT},
                    {"reusablePlugSetHash": POOL_INT},
                ]},
            }
        if item_hash == 900:
            return {"plug": {"plugCategoryIdentifier": "intrinsics"}}
        if item_hash == 901:
            return {
                "hash": 901,
                "displayProperties": {"name": "快速命中", "icon": "/p1.png",
                                      "description": "命中后加快装填。"},
                "plug": {"plugCategoryIdentifier": "frames"},
            }
        return {}

    def get_plug_set_plugs(self, plug_set_hash: int) -> list[dict]:
        if plug_set_hash == POOL_TRAIT:
            return [
                {"plugItemHash": 901, "name": "快速命中", "plugCategoryIdentifier": "frames"},
                {"plugItemHash": 902, "name": "萤火虫", "plugCategoryIdentifier": "frames"},
            ]
        return [{"plugItemHash": 903, "name": "战术模组",
                 "plugCategoryIdentifier": "v400.weapon.mod_guns"}]

    def get_item_info(self, item_hash: int) -> dict:
        return self._items.get(item_hash, {})

    def get_plug_category_identifier(self, plug_hash: int) -> str:
        if plug_hash == 900:
            return "intrinsics"
        return "v400.weapon.mod_guns" if plug_hash == 903 else "frames"

    def get_sandbox_perk_description(self, plug_hash: int) -> None:
        return None

    def get_item_description(self, plug_hash: int) -> str:
        return "效果说明"

    def get_english_name(self, item_hash: int) -> str:
        return "Test Weapon" if item_hash == WEAPON_HASH else "Perk"

    def get_definition(self, table: str, hash_id: int) -> dict | None:
        if table == "DestinyStatGroupDefinition" and hash_id == 5:
            return {"scaledStats": [{"statHash": 4284893193, "displayAsNumeric": True}]}
        if table == "DestinyStatDefinition" and hash_id == 4284893193:
            return {"displayProperties": {"name": "每分钟发射数"}}
        return None


class _Resolver:
    async def resolve_player(self, player_name: str) -> dict:
        return {"membership_id": "m1", "membership_type": 3}

    async def get_profile(self, membership_id: str, membership_type: int, components: list[int]) -> dict:
        assert components in (profile_components.WEAPON_DETAIL,)
        return {
            "characters": {"data": {"c1": {"classType": 1}}},
            "profileInventory": {"data": {"items": [
                {"itemHash": WEAPON_HASH, "itemInstanceId": "i1", "bucketHash": 138197802,
                 "state": 1},
            ]}},
            "characterInventories": {"data": {"c1": {"items": []}}},
            "characterEquipment": {"data": {"c1": {"items": []}}},
            "itemComponents": {
                "instances": {"data": {"i1": {
                    "primaryStat": {"value": 1900}, "gearTier": 5, "itemLevel": 20, "quality": 3,
                }}},
                "sockets": {"data": {"i1": {"sockets": [{"plugHash": 900}]}}},
                "stats": {"data": {"i1": {"stats": {"4284893193": {"value": 140}}}}},
                "reusablePlugs": {"data": {"i1": {"plugs": {
                    "1": [{"plugItemHash": 901, "canInsert": True}],
                }}}},
            },
        }


@pytest.fixture
def services() -> dict[str, Any]:
    manifest = _Manifest()
    resolver = _Resolver()
    perk_svc = PerkService(manifest)  # type: ignore[arg-type]
    query_svc = ManifestQueryService(manifest)  # type: ignore[arg-type]
    detail_svc = WeaponDetailService(  # type: ignore[arg-type]
        manifest, resolver, None, lookup_factory=perk_svc.god_roll_lookup
    )
    compare_svc = WeaponCompareService(  # type: ignore[arg-type]
        manifest, resolver, perk_svc, None, None
    )
    return {
        "manifest": manifest,
        "resolver": resolver,
        "perk_svc": perk_svc,
        "manifest_query_svc": query_svc,
        "weapon_detail_svc": detail_svc,
        "weapon_compare_svc": compare_svc,
        "weapon_roll_filter_svc": WeaponRollFilterService(manifest),  # type: ignore[arg-type]
        "weapon_analysis_svc": WeaponAnalysisService(perk_svc, compare_svc, query_svc),
        "starside_svc": None,
        "profile_cache": None,
    }


def _ctx(services: dict[str, Any]) -> SimpleNamespace:
    return SimpleNamespace(
        request_context=SimpleNamespace(lifespan_context=services)
    )


# ── 快照表：改了形状就必须改这里 ─────────────────────────────────────────

WEAPON_BLOCK_KEYS = sorted([
    "ammo_type", "breaker_type", "damage_type", "description", "frame", "has_enhanced",
    "icon_url", "intrinsic", "is_craftable", "item_hash", "name", "name_en", "rarity",
    "rarity_tier", "rpm", "roll_kind", "roll_summary", "trait_ids", "watermark",
    "weapon_type",
])

SOCKET_KEYS = sorted([
    "equipped", "kind", "option_count", "options", "options_available", "scope", "slot",
    "socket_index",
])

OPTION_KEYS = sorted([
    "can_roll", "description", "enhanced", "enhanced_plug_hash", "icon_url", "name",
    "plug_category", "plug_hash",
])

STAT_KEYS = sorted(["display", "display_as_numeric", "is_primary", "name", "stat_hash", "value"])

ROLL_SUMMARY_KEYS = sorted(["option_counts", "random_columns", "roll_kind", "scope"])


async def _call(services: dict[str, Any], **kwargs) -> dict:
    return await weapon_assistant(ctx=_ctx(services), **kwargs)


@pytest.mark.parametrize(
    ("intent", "kwargs", "expected_keys"),
    [
        ("info", {"weapon_name": "测试武器"},
         ["community_references", "farming_list", "sockets", "stats", "weapon", "weapon_schema_version"]),
        ("stats", {"weapon_name": "测试武器"},
         ["stats", "weapon", "weapon_schema_version"]),
        ("perk_pool", {"weapon_name": "测试武器"},
         ["community_references", "farming_list", "sockets", "weapon", "weapon_schema_version"]),
        ("god_roll", {"weapon_name": "测试武器"},
         ["farming_list", "god_roll", "weapon", "weapon_schema_version"]),
        ("popularity", {"weapon_name": "测试武器"},
         ["popularity", "weapon_schema_version"]),
        ("catalyst", {"weapon_name": "测试武器"},
         ["catalyst", "weapon_schema_version"]),
        ("analyze", {"weapon_name": "测试武器", "include_inventory": False},
         ["community_references", "farming_list", "god_roll", "inventory", "inventory_status",
          "sockets", "stats", "weapon", "weapon_schema_version"]),
        ("compare", {"weapon_name": "测试武器"},
         ["comparison", "farming_list", "weapon_schema_version"]),
        ("type", {"weapon_type": "手炮"},
         ["farming_list", "weapon_schema_version", "weapons"]),
        ("perk_description", {"perk_name": "快速命中"},
         ["community_references", "perk", "weapon_schema_version"]),
        ("filter_rolls", {"weapon_type": "手炮"},
         ["checked_count", "coverage_complete", "farming_list", "filters", "matched",
          "matched_count", "not_matched", "perk_scope", "returned_count", "scope",
          "scope_label", "scoped_count", "truncated", "unknown", "unknown_count",
          "weapon_schema_version"]),
        ("catalog", {"weapon_type": "手炮"},
         ["checked_count", "farming_list", "filters", "matched", "matched_count",
          "returned_count", "scope", "scope_label", "truncated", "weapon_schema_version"]),
    ],
)
async def test_intent_toplevel_keys(services, intent, kwargs, expected_keys) -> None:
    response = await _call(services, intent=intent, **kwargs)

    assert response["ok"] is True, response
    assert sorted(response["data"].keys()) == sorted(expected_keys)
    assert response["data"]["weapon_schema_version"] == weapon_payload.WEAPON_SCHEMA_VERSION


@pytest.mark.parametrize("intent", ["info", "perk_pool", "god_roll", "analyze"])
async def test_weapon_block_keys_are_identical_everywhere(services, intent) -> None:
    response = await _call(services, intent=intent, weapon_name="测试武器")

    weapon = response["data"]["weapon"]
    assert sorted(weapon.keys()) == WEAPON_BLOCK_KEYS
    assert sorted(weapon["roll_summary"].keys()) == ROLL_SUMMARY_KEYS
    assert weapon["name"] == "测试武器"
    assert weapon["name_en"] == "Test Weapon"
    assert weapon["rarity"] == "传说"
    assert weapon["rpm"] == 140
    assert weapon["roll_kind"] == "random"


@pytest.mark.parametrize("intent", ["info", "perk_pool"])
async def test_socket_and_option_keys(services, intent) -> None:
    response = await _call(services, intent=intent, weapon_name="测试武器")

    sockets = response["data"]["sockets"]
    assert sockets, "至少要有固有/特性/模组栏"
    for socket in sockets:
        assert sorted(socket.keys()) == SOCKET_KEYS, socket
        assert socket["scope"] == "definition"
        # 单把武器才展开池子；列表类用 column_list（options_available=false）
        assert socket["options_available"] is True
        for option in socket["options"]:
            assert sorted(option.keys()) == OPTION_KEYS, option


async def test_stats_keys_and_primary_flag(services) -> None:
    response = await _call(services, intent="stats", weapon_name="测试武器")

    stats = response["data"]["stats"]
    assert [stat["name"] for stat in stats] == ["每分钟发射数"]
    for stat in stats:
        assert sorted(stat.keys()) == STAT_KEYS
    assert stats[0]["is_primary"] is True
    assert stats[0]["display"] == "140"  # 定义值；实例值只在副本级覆盖


async def test_type_items_carry_definition_pool_and_instance_options(services) -> None:
    response = await _call(services, intent="type", weapon_type="手炮")

    block = response["data"]["weapons"]
    assert sorted(block.keys()) == ["items", "query", "returned", "total", "truncated"]
    assert block["total"] == block["returned"] == len(block["items"]) == 1
    item = block["items"][0]
    assert sorted(item.keys()) == ["notes", "options", "perks_complete", "sockets", "stats", "weapon"]
    assert item["weapon"]["instance"]["instance_id"] == "i1"
    assert item["weapon"]["gear_tier"] == 5
    # 定义级 sockets 带 equipped（现在装的固有框架），实例级 options 是能换的
    assert item["sockets"][0]["equipped"] == {"plug_hash": 900, "name": "精确重击框架"}
    assert [entry["scope"] for entry in item["options"]] == ["instance"]
    assert [stat["value"] for stat in item["stats"]] == [140]  # 实例值覆盖定义值


async def test_compare_instances_use_the_same_weapon_block(services) -> None:
    response = await _call(services, intent="compare", weapon_name="测试武器")

    comparison = response["data"]["comparison"]
    assert sorted(comparison.keys()) == ["differences", "instances", "weapon"]
    instance = comparison["instances"][0]
    assert sorted(instance.keys()) == ["options", "sockets", "stats", "weapon"]
    # 副本级身份块 = 定义字段 + 副本字段（三个组件 300 的值）+ instance 摘要
    assert sorted(instance["weapon"].keys()) == sorted(
        WEAPON_BLOCK_KEYS + ["instance", "gear_tier", "item_level", "quality"]
    )
    assert comparison["weapon"]["owned"]["count"] == 1


async def test_list_rows_use_the_lean_identity_block(services) -> None:
    """列表类只能有精简身份块：多一个键就意味着"列表又开始各长一个样"。"""
    response = await _call(services, intent="catalog", weapon_type="手炮")

    row = response["data"]["matched"][0]
    lean = set(weapon_payload.LEAN_IDENTITY_KEYS)
    assert lean.issubset(row.keys())
    assert set(row.keys()) - lean == {
        "matched_perk_details", "matched_perks", "owned", "ownership_checked", "source",
    }


async def test_missing_weapon_name_is_an_error_envelope_not_a_crash(services) -> None:
    for intent in ("analyze", "popularity"):
        response = await _call(services, intent=intent)
        assert response["ok"] is False
        assert response["error"]["code"] == "missing_weapon_name"
