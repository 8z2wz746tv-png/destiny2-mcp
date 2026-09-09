"""Account-wide reads must not turn truncated or missing data into facts."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from destiny_mcp.exceptions import APIError, ConfigError
from destiny_mcp.services.collection_service import CollectionService
from destiny_mcp.services.inventory_service import InventoryService


VAULT_BUCKET = 138197802
WEAPON_BUCKET = 1498876634
HELMET_BUCKET = 3448274439


class _InventoryManifest:
    def __init__(self) -> None:
        self.items = {
            1000 + index: {
                "itemHash": 1000 + index,
                "name": f"测试武器 {index:03}",
                "nameEn": f"Test Weapon {index:03}",
                "itemType": 3,
                "itemTypeNameDisplay": "手炮",
                "tier": 5,
                "classType": -1,
            }
            for index in range(130)
        }
        self.last_search_limit = None
        self.last_type_limit = None

    def search(self, query: str, *, limit: int = 20) -> list[dict]:
        self.last_search_limit = limit
        return [item for item in self.items.values() if query in item["name"]]

    def search_by_type_name(self, query: str, *, limit: int = 100) -> list[dict]:
        self.last_type_limit = limit
        results = list(self.items.values())
        return results if limit <= 0 else results[:limit]

    def get_item_info(self, item_hash: int) -> dict | None:
        return self.items.get(item_hash)

    def get_item_name(self, item_hash: int) -> str:
        return self.items[item_hash]["name"]

    def get_english_name(self, item_hash: int) -> str:
        return self.items[item_hash]["nameEn"]

    def item_type_name(self, item_type: int) -> str:
        return "Weapon" if item_type == 3 else ""

    def bucket_name(self, bucket_hash: int) -> str:
        return "Kinetic Weapons" if bucket_hash == WEAPON_BUCKET else "Vault"


def _inventory_profile() -> dict:
    def item(index: int, bucket: int) -> dict:
        return {
            "itemHash": 1000 + index,
            "itemInstanceId": str(index + 1),
            "bucketHash": bucket,
        }

    return {
        "profileInventory": {
            "data": {"items": [item(index, VAULT_BUCKET) for index in range(129)]}
        },
        "characters": {
            "data": {"hunter": {"classType": 1}, "warlock": {"classType": 2}}
        },
        "characterInventories": {
            "data": {
                "hunter": {"items": []},
                "warlock": {"items": [item(129, WEAPON_BUCKET)]},
            }
        },
        "characterEquipment": {
            "data": {"hunter": {"items": []}, "warlock": {"items": []}}
        },
        "itemComponents": {"instances": {"data": {}}},
    }


def _inventory_service() -> tuple[InventoryService, _InventoryManifest]:
    manifest = _InventoryManifest()
    resolver = SimpleNamespace(
        resolve_player=AsyncMock(
            return_value={"membership_id": "member", "membership_type": 3}
        ),
        get_profile=AsyncMock(return_value=_inventory_profile()),
    )
    return InventoryService(None, manifest, resolver), manifest


async def test_type_search_is_unbounded_and_location_is_exact() -> None:
    service, manifest = _inventory_service()

    vault = await service.search_items_by_type("player", "手炮", "vault")
    warlock = await service.search_items_by_type("player", "手炮", "术士")

    assert manifest.last_type_limit == 0
    assert len(vault.items) == 129
    assert {item.location for item in vault.items} == {"vault"}
    assert len(warlock.items) == 1
    assert {item.location for item in warlock.items} == {"warlock"}


async def test_name_search_uses_all_manifest_candidates() -> None:
    service, manifest = _inventory_service()

    result = await service.search_items("player", "测试武器", "all")

    assert manifest.last_search_limit == 0
    assert len(result.items) == 130


async def test_inventory_read_rejects_missing_character_container() -> None:
    service, _ = _inventory_service()
    profile = _inventory_profile()
    del profile["characterInventories"]["data"]["hunter"]
    service._resolver.get_profile.return_value = profile

    with pytest.raises(ConfigError, match="库存组件不完整"):
        await service.get_inventory("player", "all")


async def test_armor_snapshot_rejects_missing_stats_instead_of_using_zeroes() -> None:
    profile = {
        "profileInventory": {"data": {"items": [{
            "itemHash": 500,
            "itemInstanceId": "helmet",
            "bucketHash": HELMET_BUCKET,
        }]}},
        "characters": {"data": {"hunter": {"classType": 1}}},
        "characterInventories": {"data": {"hunter": {"items": []}}},
        "characterEquipment": {"data": {"hunter": {"items": []}}},
        "itemComponents": {
            "instances": {"data": {"helmet": {}}},
            "stats": {"data": {}},
            "sockets": {"data": {"helmet": {"sockets": []}}},
        },
    }
    manifest = SimpleNamespace(get_item_info=lambda item_hash: {
        "itemHash": item_hash,
        "bucketTypeHash": HELMET_BUCKET,
        "classType": 1,
        "tier": 5,
    })
    resolver = SimpleNamespace(
        resolve_player=AsyncMock(
            return_value={"membership_id": "member", "membership_type": 3}
        ),
        get_profile=AsyncMock(return_value=profile),
    )
    service = InventoryService(None, manifest, resolver)

    with pytest.raises(ConfigError, match="避免把缺失值当成 0"):
        await service.get_armor_snapshot("player", "hunter")


class _CollectionResolver:
    async def resolve_player(self, player_name: str) -> dict:
        return {"membership_id": "member", "membership_type": 3}

    async def resolve_character_id(self, *args) -> str:
        return "hunter"

    async def get_profile(self, membership_id: str, membership_type: int, components: list[int]) -> dict:
        if components == [200]:
            return {"characters": {"data": {"hunter": {"classType": 1}}}}
        return {}


async def test_collectible_item_rejects_missing_status_component() -> None:
    service = CollectionService(
        None,
        SimpleNamespace(),
        _CollectionResolver(),  # type: ignore[arg-type]
    )

    with pytest.raises(APIError, match="不能把未知状态解释为未获得"):
        await service.get_collectible_item_status("player", "测试物品")


async def test_collectible_node_rejects_missing_status_component() -> None:
    bungie = SimpleNamespace(
        get_collectible_node_details=AsyncMock(
            return_value={"ErrorCode": 1, "Response": {}}
        )
    )
    service = CollectionService(
        bungie,
        SimpleNamespace(),
        _CollectionResolver(),  # type: ignore[arg-type]
    )

    with pytest.raises(APIError, match="不能将结果视为空节点"):
        await service.get_collectible_node_status(
            "player",
            123,
            character="hunter",
        )
