"""`inventory_assistant(intent="item")`：单件护甲载荷的接线与前置条件。"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from destiny_mcp.exceptions import InvalidArgumentError

from destiny_mcp.tools import _armor_branches as branches

STAT_WEAPONS = 2996146975
STAT_SUPER = 144602215
STAT_GRENADE = 1735777505

DEFS: dict[int, dict[str, Any]] = {
    157934631: {
        "displayProperties": {"name": "至高狂徒腿铠"},
        "itemTypeDisplayName": "腿部护甲",
        "equippingBlock": {"equipableItemSetHash": 741162535},
        "inventory": {"bucketTypeHash": 20886954},
    },
    544009373: {"displayProperties": {"name": "高能者"},
                "plug": {"plugCategoryIdentifier": "armor_archetypes"}},
    1001: {"displayProperties": {"name": ""}, "plug": {"plugCategoryIdentifier": "armor_stats"},
           "investmentStats": [{"statTypeHash": STAT_WEAPONS, "value": 30}]},
    1002: {"displayProperties": {"name": ""}, "plug": {"plugCategoryIdentifier": "armor_stats"},
           "investmentStats": [{"statTypeHash": STAT_SUPER, "value": 25}]},
    1003: {"displayProperties": {"name": ""}, "plug": {"plugCategoryIdentifier": "armor_stats"},
           "investmentStats": [{"statTypeHash": STAT_GRENADE, "value": 20}]},
    2001: {"displayProperties": {"name": "空模组插槽"},
           "plug": {"plugCategoryIdentifier": "enhancements.v2_general",
                    "energyCost": {"energyCost": 0}}},
}


class _Manifest:
    def get_item_definition(self, item_hash: int):
        return DEFS.get(item_hash)

    def get_item_info(self, item_hash: int):
        return {"icon": "https://www.bungie.net/common/destiny2_content/icons/x.jpg"}

    def get_english_name(self, item_hash: int):
        return "Prime Zealot Strides"

    def get_set_bonus_info(self, item_hash: int):
        return {
            "set_name": "埃希恩记忆",
            "perks": [
                {"required_set_count": 2, "perk_name": "放射虫裂隙"},
                {"required_set_count": 4, "perk_name": "集体之力"},
            ],
        }


class _Inventory:
    def __init__(self, raw: dict):
        self.raw = raw
        self.calls: list[tuple] = []

    async def get_armor_item(self, player_name: str, item_instance_id: str) -> dict:
        self.calls.append((player_name, item_instance_id))
        return self.raw


def _svc(raw: dict) -> dict:
    return {
        "inventory_svc": _Inventory(raw),
        "manifest": _Manifest(),
        "manifest_query_svc": None,
        "set_bonus_svc": None,
        "starside_svc": None,
    }


def _raw() -> dict:
    return {
        "item": {"itemHash": 157934631, "itemInstanceId": "6917530198796768597"},
        "definition": DEFS[157934631],
        "instance": {"gearTier": 5, "isEquipped": False,
                     "primaryStat": {"value": 550},
                     "energy": {"energyCapacity": 11, "energyUsed": 0, "energyUnused": 11}},
        "sockets": [{"plugHash": 2001}, {"plugHash": 544009373},
                    {"plugHash": 1001}, {"plugHash": 1002}, {"plugHash": 1003}],
        "stats": {str(STAT_WEAPONS): {"value": 30}, str(STAT_SUPER): {"value": 25},
                  str(STAT_GRENADE): {"value": 20}},
        "location": "hunter",
        "character_id": "2305843009679355779",
        "bucket": "Leg Armor",
    }


@pytest.mark.asyncio
async def test_item_intent_returns_the_full_payload() -> None:
    svc = _svc(_raw())
    result = await branches.armor_item(svc, "husky#1234", "6917530198796768597")

    assert result["ok"] is True
    armor = result["data"]["armor"]
    assert armor["identity"]["slot"] == "legs"
    assert armor["identity"]["gear_tier"] == 5
    assert armor["identity"]["archetype"]["name"] == "高能者"
    assert armor["identity"]["set"]["name"] == "埃希恩记忆"
    assert [tier["count"] for tier in armor["identity"]["set"]["tiers"]] == [2, 4]
    assert armor["instance"]["power"] == 550
    assert armor["identity"]["icon_url"].startswith("https://www.bungie.net/"), (
        "图标要跟列清单一样是完整 CDN 地址"
    )
    assert armor["identity"]["name_en"] == "Prime Zealot Strides"
    assert armor["instance"]["energy"]["capacity"] == 11
    assert armor["stats"]["roll"]["weapons"] == 30
    assert armor["stats"]["base"] == armor["stats"]["roll"]
    assert "腿部护甲" in result["summary"] and "T5" in result["summary"]
    assert result["next_actions"], "要告诉调用方换模组该看哪里"
    assert svc["inventory_svc"].calls == [("husky#1234", "6917530198796768597")]


@pytest.mark.asyncio
async def test_item_intent_requires_an_instance_id() -> None:
    """缺 ID 时给的是中文提示，而且不再往下走（服务层不该被调用）。"""
    svc = _svc(_raw())
    with pytest.raises(InvalidArgumentError) as excinfo:
        await branches.armor_item(svc, "husky#1234", "   ")

    assert "item_instance_id" in str(excinfo.value)
    assert svc["inventory_svc"].calls == []


@pytest.mark.asyncio
async def test_missing_id_becomes_an_error_envelope_through_the_tool() -> None:
    """走真正的工具入口时，异常要被 `handle_tool_error` 收成错误信封。"""
    from unittest.mock import patch

    from destiny_mcp.tools.assistants import inventory_assistant

    ctx = SimpleNamespace(
        request_context=SimpleNamespace(lifespan_context=_svc(_raw()))
    )
    with patch("destiny_mcp.tools._helpers.resolve_player_name", lambda name: "husky#1234"):
        response = await inventory_assistant(intent="item", ctx=ctx)

    assert response["ok"] is False
    assert response["error"]["code"] == "invalid_argument_error"
    assert "item_instance_id" in response["error"]["message"]


@pytest.mark.asyncio
async def test_legacy_piece_gets_a_plain_language_warning() -> None:
    raw = _raw()
    raw["instance"] = {"energy": {"energyCapacity": 10, "energyUsed": 0, "energyUnused": 10}}
    raw["sockets"] = [{"plugHash": 2001}]
    result = await branches.armor_item(_svc(raw), "husky#1234", "6917530198796768597")

    armor = result["data"]["armor"]
    assert armor["identity"]["armor_system"] == "legacy"
    assert armor["identity"]["gear_tier"] is None
    assert any("老护甲" in warning for warning in result["warnings"])


def test_display_fields_never_leak_into_canonical_build() -> None:
    """canonical_build 是要原样回传的可执行载荷：展示字段一律不进去。"""
    from destiny_mcp.tools._armor_branches import with_slot_keys

    payload = {
        "builds": [{
            "canonical_build": {"items": [{"slot": "helmet", "item_instance_id": "1"}]},
            "items": [{"slot": "helmets", "item_instance_id": "1"}],
        }],
        "recommendation": {"results": [{"build": {"items": [{"slot": "chests"}]}}]},
    }

    enriched = with_slot_keys(payload)

    canonical_item = enriched["builds"][0]["canonical_build"]["items"][0]
    assert "slot_key" not in canonical_item and "slot_display" not in canonical_item, (
        "canonical_build 里不许出现展示字段"
    )
    assert enriched["builds"][0]["items"][0]["slot_key"] == "helmet", "其它位置照旧补键"
    assert enriched["recommendation"]["results"][0]["build"]["items"][0]["slot_key"] == "chest"
