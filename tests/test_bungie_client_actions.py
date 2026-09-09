from __future__ import annotations

import time
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from destiny_mcp.bungie_client import BungieClient
from destiny_mcp.exceptions import DestinyMCPError
from destiny_mcp.models import InventoryItem
from destiny_mcp.services.collection_service import CollectionService
from destiny_mcp.services.loadout_service import LoadoutService
from destiny_mcp.services.transfer_service import TransferService


class FakeRest:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, dict]] = []
        self.response = {"ok": True}

    async def static_request(self, method: str, path: str, **kwargs):
        self.calls.append((method, path, kwargs))
        return self.response


def make_client() -> tuple[BungieClient, FakeRest]:
    client = BungieClient()
    rest = FakeRest()
    client._rest = rest
    client._access_token = "test-token"
    client._token_expires_at = time.time() + 3600
    return client, rest


@pytest.mark.asyncio
@pytest.mark.parametrize("payload", [None, 0, {"ok": True}, {"equipResults": []}])
async def test_post_action_preserves_unwrapped_sdk_payload(payload) -> None:
    client, rest = make_client()
    rest.response = payload

    result = await client.clear_loadout(0, "230584", 3)

    assert result["ErrorCode"] == 1
    assert result["Response"] == payload


@pytest.mark.asyncio
@pytest.mark.parametrize("equip_results, expected_success", [
    ([{"itemInstanceId": 101, "equipStatus": 1},
      {"itemInstanceId": "102", "equipStatus": 1}], True),
    ([{"itemInstanceId": 101, "equipStatus": 1},
      {"itemInstanceId": 102, "equipStatus": 1648}], False),
    ([{"itemInstanceId": 101, "equipStatus": 1}], False),
    ([{"itemInstanceId": 101, "equipStatus": 1},
      {"itemInstanceId": 101, "equipStatus": 1}], False),
    ([], False),
    (None, False),
    ([None, {"itemInstanceId": 102}], False),
])
async def test_batch_equip_checks_every_requested_item(equip_results, expected_success) -> None:
    client, rest = make_client()
    rest.response = {"equipResults": equip_results}
    resolver = SimpleNamespace(
        resolve_player=AsyncMock(return_value={"membership_id": "11", "membership_type": 3}),
        resolve_character_id=AsyncMock(return_value="22"),
    )
    service = TransferService(client, object(), resolver)
    service._fetch_all_items = AsyncMock(return_value=[
        InventoryItem(item_instance_id=item_id, item_hash=int(item_id),
                      name=item_id, location="hunter", character_id="22")
        for item_id in ("101", "102")
    ])

    result = await service.equip_items("TestGuardian#1234", ["101", "102"], "hunter")

    assert result["success"] is expected_success
    assert len(result["item_results"]) == 2
    if expected_success:
        assert all(item["success"] for item in result["item_results"])
    else:
        assert not all(item["success"] for item in result["item_results"])
        assert "102" in result["message"]


@pytest.mark.asyncio
async def test_item_action_endpoints_use_official_paths_and_payloads() -> None:
    client, rest = make_client()

    await client.pull_from_postmaster("691752", 123456, "230584", 3, stack_size=2)
    await client.set_item_lock_state("691753", "230584", 3, state=True)
    await client.set_quest_tracked_state("691754", "230584", 3, state=False)
    await client.equip_items(["691755", "691756"], "230584", 3)

    assert [call[1] for call in rest.calls] == [
        "Destiny2/Actions/Items/PullFromPostmaster/",
        "Destiny2/Actions/Items/SetLockState/",
        "Destiny2/Actions/Items/SetTrackedState/",
        "Destiny2/Actions/Items/EquipItems/",
    ]
    assert rest.calls[0][2]["json"] == {
        "itemReferenceHash": 123456,
        "stackSize": 2,
        "itemId": 691752,
        "characterId": 230584,
        "membershipType": 3,
    }
    assert rest.calls[1][2]["json"]["state"] is True
    assert rest.calls[2][2]["json"]["state"] is False
    assert rest.calls[3][2]["json"]["itemIds"] == [691755, 691756]


@pytest.mark.asyncio
async def test_official_loadout_write_endpoints_use_slot_index_payloads() -> None:
    client, rest = make_client()

    await client.snapshot_loadout(
        2,
        "230584",
        3,
        name_hash=11,
        icon_hash=22,
        color_hash=33,
    )
    await client.update_loadout_identifiers(2, "230584", 3, icon_hash=44)
    await client.clear_loadout(2, "230584", 3)

    assert [call[1] for call in rest.calls] == [
        "Destiny2/Actions/Loadouts/SnapshotLoadout/",
        "Destiny2/Actions/Loadouts/UpdateLoadoutIdentifiers/",
        "Destiny2/Actions/Loadouts/ClearLoadout/",
    ]
    assert rest.calls[0][2]["json"] == {
        "loadoutIndex": 2,
        "characterId": 230584,
        "membershipType": 3,
        "nameHash": 11,
        "iconHash": 22,
        "colorHash": 33,
    }
    assert rest.calls[1][2]["json"] == {
        "loadoutIndex": 2,
        "characterId": 230584,
        "membershipType": 3,
        "iconHash": 44,
    }
    assert rest.calls[2][2]["json"] == {
        "loadoutIndex": 2,
        "characterId": 230584,
        "membershipType": 3,
    }


@pytest.mark.asyncio
async def test_collection_and_stats_endpoints_use_official_paths() -> None:
    client, rest = make_client()

    await client.get_collectible_node_details(3, "461168", "230584", 999, [800])
    await client.get_unique_weapon_history(3, "461168", "230584")
    await client.get_destiny_aggregate_activity_stats(3, "461168", "230584")
    await client.get_leaderboards(3, "461168", maxtop=10, modes="5", statid="kills")
    await client.get_leaderboards_for_character(3, "461168", "230584", maxtop=10)
    await client.get_clan_leaderboards("12345", modes="4")

    assert [call[1] for call in rest.calls] == [
        "Destiny2/3/Profile/461168/Character/230584/Collectibles/999/",
        "Destiny2/3/Account/461168/Character/230584/Stats/UniqueWeapons/",
        "Destiny2/3/Account/461168/Character/230584/Stats/AggregateActivityStats/",
        "Destiny2/3/Account/461168/Stats/Leaderboards/",
        "Destiny2/Stats/Leaderboards/3/461168/230584/",
        "Destiny2/Stats/Leaderboards/Clans/12345/",
    ]
    assert rest.calls[0][2]["params"] == {"components": "800"}
    assert rest.calls[3][2]["params"] == {
        "maxtop": 10,
        "modes": "5",
        "statid": "kills",
    }
    assert rest.calls[5][2]["params"] == {"modes": "4"}
    assert all(call[2]["auth"] == "test-token" for call in rest.calls[3:6])


def test_official_loadout_slot_number_is_user_facing() -> None:
    assert LoadoutService._slot_number_to_index(1) == 0
    assert LoadoutService._slot_number_to_index(10) == 9
    with pytest.raises(DestinyMCPError):
        LoadoutService._slot_number_to_index(0)
    with pytest.raises(DestinyMCPError):
        LoadoutService._slot_number_to_index(11)


def test_search_official_loadout_identifiers_formats_manifest_results() -> None:
    class FakeManifest:
        def search_definitions_by_name(self, table, query, *, limit, scan_limit):
            assert table == "DestinyLoadoutIconDefinition"
            assert query == "raid"
            return [{
                "hash": 123,
                "displayProperties": {
                    "name": "Raid",
                    "description": "Raid icon",
                    "icon": "/common/icon.png",
                },
            }]

    service = object.__new__(LoadoutService)
    service._manifest = FakeManifest()

    result = service.search_official_loadout_identifiers("icon", "raid", limit=5)

    assert result["success"] is True
    assert result["results"]["icon"][0] == {
        "hash": 123,
        "name": "Raid",
        "description": "Raid icon",
        "icon_url": "https://www.bungie.net/common/icon.png",
    }


def test_search_collectible_nodes_formats_node_candidates() -> None:
    class FakeManifest:
        def search_definitions_by_name(self, table, query, *, limit, scan_limit):
            assert table == "DestinyPresentationNodeDefinition"
            return [{
                "hash": 456,
                "displayProperties": {"name": "异域武器", "description": "Exotics"},
                "children": {
                    "collectibles": [{}, {}],
                    "presentationNodes": [{}],
                    "records": [],
                },
            }]

    service = CollectionService(None, FakeManifest(), None)

    result = service.search_collectible_nodes("异域", limit=5)

    assert result["success"] is True
    assert result["nodes"][0]["node_hash"] == 456
    assert result["nodes"][0]["collectible_count"] == 2


@pytest.mark.asyncio
async def test_get_collectible_item_status_reads_profile_collectible_state() -> None:
    class FakeResolver:
        async def resolve_player(self, player_name):
            return {"membership_id": "461168", "membership_type": 3}

        async def get_profile(self, membership_id, membership_type, components):
            if components == [200]:
                return {"characters": {"data": {"230584": {"classType": 1}}}}
            return {
                "profileCollectibles": {
                    "data": {
                        "collectibles": {
                            "9001": {"state": 0},
                        },
                    },
                },
                "characterCollectibles": {"data": {}},
            }

    class FakeManifest:
        def search(self, query, *, limit):
            return [{"itemHash": 111, "name": "死亡使者"}]

        def get_item_definition(self, item_hash):
            return {"collectibleHash": 9001}

        def get_definition(self, table, item_hash):
            return {"displayProperties": {"name": "死亡使者收藏品"}}

        def get_item_name(self, item_hash):
            return "死亡使者"

    service = CollectionService(None, FakeManifest(), FakeResolver())

    result = await service.get_collectible_item_status("me", "死亡使者")

    assert result["success"] is True
    assert result["items"][0]["collectible_hash"] == 9001
    assert result["items"][0]["acquired"] is True
    assert result["items"][0]["state_labels"] == ["已获得"]
