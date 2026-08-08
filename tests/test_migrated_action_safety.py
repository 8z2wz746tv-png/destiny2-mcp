"""Regression tests for migrated account-write and live-vendor behavior."""

from __future__ import annotations

import asyncio
from copy import deepcopy
from unittest.mock import AsyncMock

import pytest

from destiny_mcp.exceptions import TransferError
from destiny_mcp.models import InventoryItem
from destiny_mcp.services import transfer_service as transfer_service_module
from destiny_mcp.services.account_action_lock import (
    account_action_lock,
    serialized_account_action,
)
from destiny_mcp.services.transfer_service import TransferService
from destiny_mcp.services.vendor_service import VendorService


class _TransferResolver:
    async def resolve_player(self, player_name: str) -> dict:
        return {"membership_id": "membership-id", "membership_type": 3}

    async def resolve_character_id(
        self,
        membership_id: str,
        membership_type: int,
        character_name: str,
    ) -> str:
        assert character_name == "warlock"
        return "warlock-id"


class _TransferBungie:
    def __init__(self) -> None:
        self.responses = [
            {"ErrorCode": 1, "Message": "Ok"},
            {"ErrorCode": 1642, "Message": "Target inventory full"},
            {"ErrorCode": 1, "Message": "Ok"},
        ]
        self.timeline: list[tuple[str, object, object | None]] = []

    async def transfer_item(
        self,
        item_instance_id: str,
        item_hash: int,
        *,
        character_id: str,
        membership_type: int,
        to_vault: bool,
    ) -> dict:
        self.timeline.append(("transfer", to_vault, character_id))
        return self.responses.pop(0)


@pytest.mark.asyncio
async def test_second_transfer_hop_failure_rolls_item_back_to_source(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bungie = _TransferBungie()
    service = TransferService(bungie, object(), _TransferResolver())
    item = InventoryItem(
        item_instance_id="item-instance-id",
        item_hash=123,
        name="Test Weapon",
        location="hunter",
        character_id="hunter-id",
    )
    monkeypatch.setattr(service, "_find_item", AsyncMock(return_value=item))

    async def record_sleep(delay: float) -> None:
        bungie.timeline.append(("sleep", delay, None))

    monkeypatch.setattr(transfer_service_module.asyncio, "sleep", record_sleep)

    with pytest.raises(TransferError) as exc_info:
        await service.transfer_item(
            "TestGuardian#1234",
            "item-instance-id",
            "warlock",
        )

    assert "Target inventory full" in str(exc_info.value)
    assert "rolled back to hunter" in str(exc_info.value)
    assert bungie.timeline == [
        ("transfer", True, "hunter-id"),
        ("sleep", 0.75, None),
        ("transfer", False, "warlock-id"),
        ("sleep", 0.75, None),
        ("transfer", False, "hunter-id"),
    ]


@pytest.mark.asyncio
async def test_account_write_operations_using_one_client_are_serialized() -> None:
    class AccountClient:
        pass

    class WriteService:
        def __init__(self, client: AccountClient) -> None:
            self._account_action_lock = account_action_lock(client)

        @serialized_account_action
        async def write(
            self,
            name: str,
            entered: asyncio.Event,
            release: asyncio.Event | None,
            timeline: list[str],
        ) -> None:
            timeline.append(f"start:{name}")
            entered.set()
            if release is not None:
                await release.wait()
            timeline.append(f"end:{name}")

    client = AccountClient()
    first_service = WriteService(client)
    second_service = WriteService(client)
    first_entered = asyncio.Event()
    release_first = asyncio.Event()
    second_entered = asyncio.Event()
    timeline: list[str] = []

    first = asyncio.create_task(
        first_service.write("first", first_entered, release_first, timeline)
    )
    await first_entered.wait()
    second = asyncio.create_task(
        second_service.write("second", second_entered, None, timeline)
    )
    await asyncio.sleep(0)

    assert first_service._account_action_lock is second_service._account_action_lock
    assert second_entered.is_set() is False

    release_first.set()
    await asyncio.wait_for(asyncio.gather(first, second), timeout=1)

    assert timeline == ["start:first", "end:first", "start:second", "end:second"]


_BANSHEE_HASH = 672118013
_WEAPON_HASH = 700

_VENDOR_RESPONSE = {
    "vendors": {
        "data": {
            str(_BANSHEE_HASH): {
                "vendorHash": _BANSHEE_HASH,
                "nextRefreshDate": "2026-07-26T17:00:00Z",
            }
        }
    },
    "sales": {
        "data": {
            str(_BANSHEE_HASH): {
                "saleItems": {
                    "9": {
                        "vendorItemIndex": 42,
                        "itemHash": _WEAPON_HASH,
                        "costs": [],
                        "failureIndexes": [],
                        "apiPurchasable": True,
                        "augments": 0,
                    }
                }
            }
        }
    },
    "itemComponents": {},
    "failureStrings": [],
    "vendorGroups": {"data": {"groups": []}},
}


class _VendorResolver:
    async def resolve_player(self, player_name: str) -> dict:
        return {"membership_id": "membership-id", "membership_type": 3}

    async def resolve_character_id(
        self,
        membership_id: str,
        membership_type: int,
        character_name: str,
    ) -> str:
        return "hunter-id"


class _VendorManifest:
    _items = {
        _WEAPON_HASH: {
            "name": "Live Roll Weapon",
            "itemType": 3,
            "itemTypeName": "Weapon",
            "tier": 5,
            "icon": "https://www.bungie.net/item.jpg",
        },
        111: {"name": "Verified Barrel", "icon": "https://www.bungie.net/111.jpg"},
        222: {"name": "Verified Trait", "icon": "https://www.bungie.net/222.jpg"},
        999: {"name": "Wrong Index Perk", "icon": "https://www.bungie.net/999.jpg"},
    }

    def get_vendor_definition(self, vendor_hash: int) -> dict:
        return {"displayProperties": {"name": "Banshee-44"}}

    def get_item_info(self, item_hash: int) -> dict | None:
        return self._items.get(item_hash)

    def get_item_name(self, item_hash: int) -> str:
        return str((self._items.get(item_hash) or {}).get("name") or f"#{item_hash}")

    def get_plug_category_identifier(self, plug_hash: int) -> str:
        return "barrels" if plug_hash == 111 else "traits"

    def get_sandbox_perk_description(self, plug_hash: int) -> dict:
        return {"description": f"Verified description {plug_hash}"}

    def get_item_definition(self, item_hash: int) -> dict:
        raise AssertionError("Vendor rolls must not fall back to the manifest perk pool")


class _VendorBungie:
    def __init__(self, detail: object) -> None:
        self.detail = detail
        self.component_calls: list[tuple[str, int, str, int]] = []

    async def fetch_vendors(
        self,
        membership_id: str,
        membership_type: int,
        character_id: str,
    ) -> dict:
        return deepcopy(_VENDOR_RESPONSE)

    async def fetch_vendor_components(
        self,
        membership_id: str,
        membership_type: int,
        character_id: str,
        vendor_hash: int,
    ) -> dict:
        self.component_calls.append(
            (membership_id, membership_type, character_id, vendor_hash)
        )
        if isinstance(self.detail, Exception):
            raise self.detail
        return deepcopy(self.detail)


@pytest.mark.asyncio
async def test_vendor_perks_use_live_vendor_item_index_socket() -> None:
    bungie = _VendorBungie(
        {
            "itemComponents": {
                "sockets": {
                    "data": {
                        "9": {"sockets": [{"plugHash": 999}]},
                        "42": {
                            "sockets": [
                                {"plugHash": 111, "isEnabled": True},
                                {"plugHash": 222, "isEnabled": True},
                            ]
                        },
                    }
                }
            }
        }
    )
    result = await VendorService(
        bungie,
        _VendorManifest(),
        _VendorResolver(),
    ).get_vendor_inventory(
        "TestGuardian#1234",
        "hunter",
        "Banshee",
        compact=False,
    )

    item = result.vendors[0].sale_items[0]
    assert item.vendor_item_index == 42
    assert item.perks is not None
    assert [perk.plug_hash for perk in item.perks] == [111, 222]
    assert [perk.icon_url for perk in item.perks] == [
        "https://www.bungie.net/111.jpg",
        "https://www.bungie.net/222.jpg",
    ]
    assert bungie.component_calls == [
        ("membership-id", 3, "hunter-id", _BANSHEE_HASH)
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "detail",
    [
        pytest.param(RuntimeError("Bungie component API failed"), id="api-error"),
        pytest.param({"itemComponents": {}}, id="missing-components"),
    ],
)
async def test_vendor_component_failure_omits_unverified_perks(detail: object) -> None:
    bungie = _VendorBungie(detail)
    result = await VendorService(
        bungie,
        _VendorManifest(),
        _VendorResolver(),
    ).get_vendor_inventory(
        "TestGuardian#1234",
        "hunter",
        "Banshee",
        compact=False,
    )

    item = result.vendors[0].sale_items[0]
    assert item.name == "Live Roll Weapon"
    assert item.perks is None
    assert result.warnings
    assert any("已省略 Perk" in warning for warning in result.warnings)
    assert bungie.component_calls == [
        ("membership-id", 3, "hunter-id", _BANSHEE_HASH)
    ]
