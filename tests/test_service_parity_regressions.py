from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from destiny_mcp.build.models import InventorySnapshot
from destiny_mcp.exceptions import APIError, ConfigError, ItemNotFoundError
from destiny_mcp.models import WeeklyMilestone, WeeklyResetResponse
from destiny_mcp.services.inventory_service import _normalize_inventory_item_type
from destiny_mcp.services.weapon_analysis_service import WeaponAnalysisService
from destiny_mcp.services.weapon_service import WeaponService
from destiny_mcp.services.weekly_analysis_service import WeeklyAnalysisService


async def test_weapon_service_forwards_requested_instance_id() -> None:
    expected = object()
    compare = AsyncMock(return_value=expected)
    service = object.__new__(WeaponService)
    service._compare_svc = SimpleNamespace(compare_weapon_instances=compare)

    result = await service.compare_weapon_instances(
        "Guardian#1234",
        "命运使者",
        "instance-1",
    )

    assert result is expected
    compare.assert_awaited_once_with("Guardian#1234", "命运使者", "instance-1")


async def test_weapon_analysis_suggests_registered_assistant_tools() -> None:
    class Compare:
        async def compare_weapon_instances(self, player_name: str, weapon_name: str):
            raise ItemNotFoundError(weapon_name)

    service = object.__new__(WeaponAnalysisService)
    service._compare_svc = Compare()
    warnings: list[str] = []
    next_actions: list[dict] = []

    result, status = await service._load_inventory_comparison(
        "命运使者",
        "Guardian#1234",
        True,
        warnings,
        next_actions,
    )

    assert result is None
    assert status == "complete"
    assert next_actions == [{
        "label": "缩短武器名或检查是否在其他账号",
        "tool": "inventory_assistant",
        "arguments": {"intent": "search", "item_name": "命运使者"},
    }]


async def test_weapon_analysis_does_not_report_zero_when_inventory_lookup_fails() -> None:
    class Perks:
        async def get_weapon_perks(self, weapon_name: str):
            return None

        async def get_god_roll(self, weapon_name: str) -> str:
            return ""

    class Compare:
        async def compare_weapon_instances(self, player_name: str, weapon_name: str):
            raise APIError("读取武器副本", "暂时不可用")

    service = WeaponAnalysisService(
        Perks(),  # type: ignore[arg-type]
        Compare(),  # type: ignore[arg-type]
        SimpleNamespace(get_weapon_full_info=lambda name: {"name": name}),
    )

    result = await service.analyze_weapon(
        "测试武器",
        player_name="Guardian#1234",
    )

    assert result["inventory"] is None
    assert result["inventory_status"] == "unavailable"
    assert "账号副本数未知" in result["summary"]
    assert "0 个账号副本" not in result["summary"]


async def test_weekly_analysis_suggests_world_assistant() -> None:
    class Weekly:
        async def get_weekly_reset(self) -> WeeklyResetResponse:
            return WeeklyResetResponse(
                reset_time="2026-08-04T17:00:00Z",
                milestones=[
                    WeeklyMilestone(milestone_hash=1, name="A"),
                    WeeklyMilestone(milestone_hash=2, name="B"),
                ],
            )

    result = await WeeklyAnalysisService(Weekly()).summarize_weekly_reset(limit=1)

    assert result["next_actions"] == [{
        "label": "查看完整周常明细",
        "tool": "world_assistant",
        "arguments": {"intent": "weekly_full"},
    }]
    assert 'world_assistant(intent="weekly_full")' in result["warnings"][0]


def test_armor_snapshot_keeps_absolute_manifest_icon_url() -> None:
    class Manifest:
        def get_item_info(self, item_hash: int) -> dict:
            return {
                "classType": 1,
                "tier": 5,
                "bucketTypeHash": 3448274439,
                "icon": "https://www.bungie.net/common/helmet.png",
            }

        def get_item_name(self, item_hash: int) -> str:
            return "测试头盔"

        def get_item_definition(self, item_hash: int) -> dict:
            return {}

        def get_set_bonus_info(self, item_hash: int) -> None:
            return None

    profile = {
        "profileInventory": {"data": {"items": [{
            "itemHash": 100,
            "itemInstanceId": "helmet-1",
            "bucketHash": 3448274439,
        }]}},
        "characters": {"data": {}},
        "characterInventories": {"data": {}},
        "characterEquipment": {"data": {}},
        "itemComponents": {
            "instances": {"data": {"helmet-1": {}}},
            "stats": {"data": {}},
            "sockets": {"data": {}},
        },
    }

    snapshot = InventorySnapshot.from_profile(profile, Manifest())  # type: ignore[arg-type]

    assert snapshot.helmets[0].icon_url == (
        "https://www.bungie.net/common/helmet.png"
    )


def test_inventory_get_rejects_specific_weapon_types_with_routing_hint() -> None:
    assert _normalize_inventory_item_type("武器") == "weapon"
    assert _normalize_inventory_item_type("护甲") == "armor"

    with pytest.raises(ConfigError, match='intent="type"'):
        _normalize_inventory_item_type("火箭筒")
