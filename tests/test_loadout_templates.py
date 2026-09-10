from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from destiny_mcp.models import Loadout, LoadoutListResponse
from destiny_mcp.services.loadout_service import LoadoutService
from destiny_mcp.tools.assistants import loadout_assistant


class FakeManifest:
    def __init__(self) -> None:
        self.items = {
            101: {"itemType": 2, "tier": 6, "bucketTypeHash": 14239492, "name": "猎鹰胸甲"},
            201: {"itemType": 3, "tier": 5, "bucketTypeHash": 1498876634, "name": "测试手炮"},
            301: {"itemType": 16, "tier": 0, "bucketTypeHash": 3284755031, "name": "棱镜"},
            401: {"itemType": 19, "name": "护甲模组"},
            402: {"itemType": 19, "name": "辉耀炽热"},
        }

    def get_definition(self, table: str, hash_id: int) -> dict | None:
        if table == "DestinyLoadoutNameDefinition" and hash_id == 9001:
            return {"displayProperties": {"name": "宗师官方槽"}}
        return None

    def get_item_info(self, item_hash: int) -> dict | None:
        return self.items.get(item_hash)

    def get_item_name(self, item_hash: int) -> str:
        return (self.items.get(item_hash) or {}).get("name", f"#{item_hash}")

    def get_plug_category_identifier(self, plug_hash: int) -> str:
        return "enhancements.v2_chest" if plug_hash == 401 else "weapon.frame"


class FakeClient:
    pass


@pytest.mark.asyncio
async def test_native_slot_is_normalized_to_build_template() -> None:
    profile = {
        "characters": {"data": {"character-1": {"classType": 1}}},
        "profileInventory": {"data": {"items": [
            {"itemInstanceId": "armor-1", "itemHash": 101},
            {"itemInstanceId": "weapon-1", "itemHash": 201},
            {"itemInstanceId": "subclass-1", "itemHash": 301},
        ]}},
        "characterInventories": {"data": {}},
        "characterEquipment": {"data": {}},
        "characterLoadouts": {"data": {"character-1": {"loadouts": [{
            "loadoutIndex": 2,
            "nameHash": 9001,
            "iconHash": 9002,
            "colorHash": 9003,
            "items": [
                {"itemInstanceId": "armor-1", "plugItemHashes": [401]},
                {"itemInstanceId": "weapon-1", "plugItemHashes": [402]},
                {"itemInstanceId": "subclass-1", "plugItemHashes": []},
            ],
        }]}}},
    }
    resolver = SimpleNamespace(
        resolve_player=AsyncMock(return_value={"membership_id": "player", "membership_type": 3}),
        get_profile=AsyncMock(return_value=profile),
    )
    service = LoadoutService(FakeClient(), FakeManifest(), resolver)

    result = await service._fetch_native("player")

    assert len(result) == 1
    loadout = result[0]
    assert loadout.id == "bungie:character-1:2"
    assert loadout.name == "宗师官方槽"
    assert loadout.slot_number == 3
    assert loadout.native_character_id == "character-1"
    assert loadout.name_hash == 9001
    assert [item.slot for item in loadout.items] == ["chest"]

    template = loadout.build_template
    assert template["format_version"] == "destiny2_build_template_v1"
    assert template["class"]["id"] == "hunter"
    assert template["subclass"] == "棱镜"
    assert template["weapons"][0]["name"] == "测试手炮"
    assert template["weapons"][0]["perk_hashes"] == [402]
    assert template["armor"]["exotic"] == "猎鹰胸甲"
    assert template["armor"]["mods"]["chest"] == ["护甲模组"]
    assert template["source"] == {
        "provider": "bungie",
        "content_scope": "official_loadout_slot",
        "character_id": "character-1",
        "slot_number": 3,
        "name_hash": 9001,
        "icon_hash": 9002,
        "color_hash": 9003,
    }
    assert template["executable"] is False
    assert template["execution"] == {
        "supported": True,
        "mode": "bungie_native_slot",
        "loadout_id": "bungie:character-1:2",
        "requires_confirmation": True,
    }


@pytest.mark.asyncio
async def test_native_slot_without_index_uses_twentieth_array_position() -> None:
    profile = {
        "characters": {"data": {"character-1": {"classType": 1}}},
        "profileInventory": {"data": {"items": []}},
        "characterInventories": {"data": {}},
        "characterEquipment": {"data": {}},
        "characterLoadouts": {"data": {"character-1": {
            "loadouts": [{"items": []} for _ in range(20)]
        }}},
    }
    resolver = SimpleNamespace(
        resolve_player=AsyncMock(return_value={"membership_id": "player", "membership_type": 3}),
        get_profile=AsyncMock(return_value=profile),
    )
    service = LoadoutService(FakeClient(), FakeManifest(), resolver)

    result = await service._fetch_native("player")

    assert len(result) == 20
    assert result[-1].id == "bungie:character-1:19"
    assert result[-1].slot_number == 20


def test_legacy_local_loadout_is_migrated_to_build_template(tmp_path) -> None:
    legacy = Loadout(
        id="legacy-1",
        name="旧版配装",
        character="hunter",
        items=[
            {
                "item_hash": 101,
                "name": "猎鹰胸甲",
                "slot": "chest",
                "item_instance_id": "armor-1",
                "mods": [401],
            }
        ],
        native_character_id="character-1",
    ).model_dump(exclude={"build_template"})
    path = tmp_path / "loadouts.json"
    path.write_text(json.dumps({"loadouts": [legacy]}), encoding="utf-8")

    service = LoadoutService(FakeClient(), FakeManifest(), SimpleNamespace(), path)

    result = service._load_local()

    assert len(result) == 1
    template = result[0].build_template
    assert template["format_version"] == "destiny2_build_template_v1"
    assert template["class"]["id"] == "hunter"
    assert template["armor"]["exotic"] == "猎鹰胸甲"
    assert template["armor"]["mods"]["chest"] == ["护甲模组"]
    assert template["source"]["provider"] == "local"


@pytest.mark.asyncio
async def test_loadout_assistant_labels_account_scope_and_community_route() -> None:
    saved = Loadout(id="local-1", name="当前方案", character="hunter", items=[])
    loadout_service = SimpleNamespace(
        get_loadouts=AsyncMock(return_value=LoadoutListResponse(
            player_name="player",
            loadouts=[saved],
        ))
    )
    ctx = SimpleNamespace(
        request_context=SimpleNamespace(
            lifespan_context={"loadout_svc": loadout_service}
        )
    )

    response = await loadout_assistant(intent="list", character="hunter", ctx=ctx)

    assert response["ok"] is True
    assert response["data"]["scope"] == "account_saved_loadouts"
    assert response["data"]["loadout_format"] == "destiny2_build_template_v1"
    assert response["data"]["loadouts"][0]["id"] == "local-1"
    assert response["data"]["community_route"] == {
        "tool": "build_assistant",
        "arguments": {"intent": "community", "character": "hunter"},
    }
