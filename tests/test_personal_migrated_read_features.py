"""Regression tests for read-only MCP features ported to the personal edition."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from destiny_mcp.exceptions import WeaponPopularityDataError
from destiny_mcp.manifest import ManifestManager
from destiny_mcp.services.inventory_analysis_service import InventoryAnalysisService
from destiny_mcp.services.weapon_popularity_service import WeaponPopularityService
from destiny_mcp.services.weapon_roll_filter_service import WeaponRollFilterService
from destiny_mcp.utils.hash_utils import to_signed


def test_manifest_catalog_includes_all_matching_weapon_definitions() -> None:
    manifest = ManifestManager()
    unsigned_hash = 0xF0000001
    rocket = {
        "itemHash": unsigned_hash,
        "name": "测试火箭筒",
        "nameEn": "Test Launcher",
        "itemType": 3,
        "itemTypeName": "Weapon",
        "itemTypeNameDisplay": "火箭发射器",
        "tier": 5,
        "icon": "https://www.bungie.net/rocket.png",
        "damageType": 4,
        "ammoType": 3,
    }
    hand_cannon = {
        **rocket,
        "itemHash": 200,
        "name": "测试手炮",
        "nameEn": "Test Hand Cannon",
        "itemTypeNameDisplay": "手炮",
    }
    # The Manifest keeps signed and unsigned lookup aliases for the same item.
    manifest._hash_index = {
        unsigned_hash: rocket,
        to_signed(unsigned_hash): rocket,
        200: hand_cannon,
    }
    manifest._name_index = {"test launcher": [rocket], "test hand cannon": [hand_cannon]}

    results = manifest.list_weapon_catalog("火箭筒")

    assert [item["itemHash"] for item in results] == [unsigned_hash]
    assert results[0]["nameEn"] == "Test Launcher"


class _CatalogManifest:
    def list_weapon_catalog(self, weapon_type: str, *, weapon_name: str = "") -> list[dict]:
        assert weapon_type == "火箭筒"
        assert weapon_name == ""
        return [
            {
                "itemHash": 100,
                "name": "命中武器",
                "nameEn": "Matching Weapon",
                "itemTypeNameDisplay": "火箭发射器",
                "tier": 5,
                "icon": "/matching.png",
                "damageType": 4,
                "ammoType": 3,
            },
            {
                "itemHash": 101,
                "name": "未命中武器",
                "nameEn": "Other Weapon",
                "itemTypeNameDisplay": "火箭发射器",
                "tier": 5,
                "icon": "/other.png",
                "damageType": 2,
                "ammoType": 3,
            },
        ]

    def get_item_definition(self, item_hash: int) -> dict:
        plug_set_hash = 500 if item_hash == 100 else 501
        return {
            "sockets": {
                "socketEntries": [{"randomizedPlugSetHash": plug_set_hash}]
            }
        }

    def get_plug_set_plugs(self, plug_set_hash: int) -> list[dict]:
        if plug_set_hash == 500:
            return [
                {
                    "plugItemHash": 201,
                    "name": "集体爆破",
                    "plugCategoryIdentifier": "perks",
                },
                {
                    "plugItemHash": 202,
                    "name": "追踪模块",
                    "plugCategoryIdentifier": "perks",
                },
            ]
        return [
            {
                "plugItemHash": 203,
                "name": "爆破专家",
                "plugCategoryIdentifier": "perks",
            }
        ]

    def get_item_info(self, item_hash: int) -> dict:
        return {
            "itemHash": item_hash,
            "name": {201: "集体爆破", 202: "追踪模块", 203: "爆破专家"}[item_hash],
            "icon": f"/perk-{item_hash}.png",
        }

    def get_sandbox_perk_description(self, perk_hash: int) -> dict:
        return {"description": f"perk {perk_hash} description"}

    def get_plug_category_identifier(self, plug_hash: int) -> str:
        return "perks"


def test_catalog_perk_filter_does_not_depend_on_account_ownership() -> None:
    result = WeaponRollFilterService(_CatalogManifest()).filter_catalog(
        weapon_type="火箭筒",
        required_perks=["集体爆破"],
    )

    assert result["scope"] == "manifest_catalog"
    assert result["checked_count"] == 2
    assert result["matched_count"] == 1
    match = result["matched"][0]
    assert match["name"] == "命中武器"
    assert match["matched_perks"] == ["集体爆破"]
    assert match["matched_perk_details"][0]["icon_url"] == "/perk-201.png"
    assert match["owned"] is False
    assert match["ownership_checked"] is False


def _popularity_document() -> dict[str, Any]:
    return {
        "schema_version": 1,
        "snapshots": [
            {
                "weapon": {
                    "name": "测试火箭筒",
                    "weapon_type": "火箭发射器",
                    "version_label": "#1/1",
                    "manifest_variant": {"is_holofoil": False},
                    "intrinsic": {"name": "高冲击框架", "description": "高伤害框架。"},
                    "stats": {"每分钟发射数": 20},
                },
                "source": {
                    "kind": "test_fixture",
                    "label": "测试数据",
                    "captured_at": "2026-07-25",
                    "snapshot_sha256": "fixture",
                },
                "popular_combinations": [
                    {"perks": ["集体爆破"], "selection_rate": 42.0}
                ],
                "perk_columns": [
                    {
                        "slot_name": "trait_1",
                        "label": "特性 1",
                        "items": [{"name": "集体爆破", "selection_rate": 42.0}],
                    }
                ],
                "masterworks": [],
                "mods": [],
            }
        ],
    }


class _PopularityManifest:
    _items = {
        100: {
            "itemHash": 100,
            "name": "测试火箭筒",
            "nameEn": "Test Launcher",
            "itemType": 3,
            "icon": "https://www.bungie.net/weapon.png",
        },
        200: {"itemHash": 200, "name": "高冲击框架", "icon": "/intrinsic.png"},
        201: {"itemHash": 201, "name": "集体爆破", "icon": "/collective-action.png"},
    }

    def search(self, query: str, *, limit: int = 20) -> list[dict]:
        return [self._items[100]] if query == "测试火箭筒" else []

    def get_item_definition(self, item_hash: int) -> dict:
        assert item_hash == 100
        return {
            "isHolofoil": False,
            "sockets": {
                "socketEntries": [
                    {"singleInitialItemHash": 200},
                    {"randomizedPlugSetHash": 500},
                ]
            },
        }

    def get_plug_set_plugs(self, plug_set_hash: int) -> list[dict]:
        assert plug_set_hash == 500
        return [{"plugItemHash": 201}]

    def get_item_info(self, item_hash: int) -> dict | None:
        return self._items.get(item_hash)


def _write_popularity(path: Path, payload: dict[str, Any]) -> Path:
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return path


def test_weapon_popularity_returns_recorded_snapshot_and_missing_is_none(
    tmp_path: Path,
) -> None:
    service = WeaponPopularityService(
        _PopularityManifest(),
        _write_popularity(tmp_path / "popularity.json", _popularity_document()),
    )

    result = service.get_weapon_popularity("测试火箭筒")

    assert result is not None
    assert result["weapon"]["item_hash"] == 100
    assert result["perk_columns"][0]["items"][0] == {
        "name": "集体爆破",
        "selection_rate": 42.0,
        "plug_hash": 201,
        "icon_url": "/collective-action.png",
    }
    assert service.get_weapon_popularity("不存在的武器") is None


def test_weapon_popularity_rejects_invalid_json(tmp_path: Path) -> None:
    invalid = tmp_path / "invalid.json"
    invalid.write_text('{"schema_version": 1, "snapshots": [', encoding="utf-8")

    with pytest.raises(WeaponPopularityDataError, match="数据无效"):
        WeaponPopularityService(_PopularityManifest(), invalid)


class _DuplicateManifest:
    _items = {
        100: {
            "itemType": 3,
            "itemTypeNameDisplay": "火箭发射器",
            "icon": "/weapon.png",
            "classType": -1,
        },
        101: {
            "itemType": 3,
            "itemTypeNameDisplay": "手炮",
            "icon": "/hand-cannon.png",
            "classType": -1,
        },
        201: {"name": "集体爆破", "icon": "/collective-action.png"},
        202: {"name": "追踪模块", "icon": "/tracking.png"},
        299: {"name": "着色器", "icon": "/shader.png"},
    }

    def get_item_info(self, item_hash: int) -> dict | None:
        return self._items.get(item_hash)

    def get_item_name(self, item_hash: int) -> str:
        return {100: "测试火箭筒", 101: "测试手炮"}.get(item_hash, f"#{item_hash}")

    def get_english_name(self, item_hash: int) -> str:
        return {100: "Test Launcher", 101: "Test Hand Cannon"}.get(item_hash, "")

    def item_type_name(self, item_type: int) -> str:
        return "Weapon" if item_type == 3 else "Unknown"

    def bucket_name(self, bucket_hash: int) -> str:
        return {
            138197802: "Vault (General)",
            953998645: "Power Weapons",
            2465295065: "Energy Weapons",
        }.get(bucket_hash, "Unknown")

    def get_plug_category_identifier(self, plug_hash: int) -> str:
        return {201: "perks", 202: "perks", 299: "shader"}[plug_hash]


class _DuplicateResolver:
    def __init__(self, profile: dict[str, Any]) -> None:
        self.profile = profile
        self.requested_components: list[int] = []

    async def resolve_player(self, player_name: str) -> dict[str, Any]:
        assert player_name == "Guardian#1234"
        return {"membership_id": "membership", "membership_type": 3}

    async def get_profile(
        self,
        membership_id: str,
        membership_type: int,
        components: list[int],
    ) -> dict[str, Any]:
        assert (membership_id, membership_type) == ("membership", 3)
        self.requested_components = components
        return self.profile


def _duplicate_profile() -> dict[str, Any]:
    return {
        "characters": {"data": {"hunter": {"classType": 1}}},
        "profileInventory": {
            "data": {
                "items": [
                    {
                        "itemHash": 100,
                        "itemInstanceId": "instance-vault",
                        "bucketHash": 138197802,
                        "quantity": 1,
                    }
                ]
            }
        },
        "characterInventories": {
            "data": {
                "hunter": {
                    "items": [
                        {
                            "itemHash": 100,
                            "itemInstanceId": "instance-hunter",
                            "bucketHash": 953998645,
                            "quantity": 1,
                        }
                    ]
                }
            }
        },
        "characterEquipment": {
            "data": {
                "hunter": {
                    "items": [
                        {
                            "itemHash": 101,
                            "itemInstanceId": "instance-unique",
                            "bucketHash": 2465295065,
                            "isEquipped": True,
                            "quantity": 1,
                        }
                    ]
                }
            }
        },
        "itemComponents": {
            "instances": {
                "data": {
                    "instance-vault": {"primaryStat": {"value": 550}},
                    "instance-hunter": {"primaryStat": {"value": 550}},
                    "instance-unique": {
                        "primaryStat": {"value": 550},
                        "isEquipped": True,
                    },
                }
            },
            "sockets": {
                "data": {
                    "instance-vault": {
                        "sockets": [{"plugHash": 201}, {"plugHash": 299}]
                    },
                    "instance-hunter": {"sockets": [{"plugHash": 202}]},
                    "instance-unique": {"sockets": []},
                }
            },
        },
    }


@pytest.mark.asyncio
async def test_duplicate_weapon_scan_groups_instances_and_returns_current_perks() -> None:
    resolver = _DuplicateResolver(_duplicate_profile())
    result = await InventoryAnalysisService(
        _DuplicateManifest(),
        resolver,
    ).find_duplicate_weapons("Guardian#1234")

    assert resolver.requested_components == [102, 200, 201, 205, 300, 305]
    assert result["scan"]["duplicate_scan_complete"] is True
    assert result["scan"]["weapon_instances"] == 3
    assert result["scan"]["duplicate_groups"] == 1
    assert result["warnings"] == []
    duplicate = result["duplicates"][0]
    assert duplicate["name"] == "测试火箭筒"
    assert duplicate["instance_count"] == 2
    assert [item["location"] for item in duplicate["instances"]] == ["猎人", "仓库"]
    assert [perk["name"] for perk in duplicate["instances"][0]["perks"]] == ["追踪模块"]
    assert [perk["name"] for perk in duplicate["instances"][1]["perks"]] == ["集体爆破"]
    assert all(
        perk["name"] != "着色器"
        for instance in duplicate["instances"]
        for perk in instance["perks"]
    )
