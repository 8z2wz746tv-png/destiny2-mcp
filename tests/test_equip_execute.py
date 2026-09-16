"""`execute_equip_plan`：先顶下、再装目标；失败要回滚并说清停点。

守的是三件事：
1. 顺序 —— 计划里第几步就第几步，先顶下再装；
2. 失败**回滚** —— 前半程已经动过的部位要换回动手前那件，回滚没做全就照实说；
3. 回读 —— 目标真穿上了才算成功；回读不到报 `unverified`（上游 profile 有同步窗口）。
"""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from destiny_mcp.models import EquipPlan, EquipPlanStep
from destiny_mcp.services import write_readback
from destiny_mcp.services.transfer_service import TransferService

WARLOCK_CLASS_TYPE = 2


@pytest.fixture(autouse=True)
def _no_readback_delay(monkeypatch):
    monkeypatch.setattr(write_readback, "DELAY_SECONDS", 0)


def _plan() -> EquipPlan:
    return EquipPlan(
        status="ready",
        character="warlock",
        character_id="c1",
        target_item="逃逸艺术家",
        target_item_instance_id="gaunt-exotic",
        target_slot="gauntlets",
        steps=[
            EquipPlanStep(
                action="downgrade",
                item="圣贤保护者法袍",
                item_instance_id="chest-legendary",
                slot="chest",
                replaces="星火协议",
            ),
            EquipPlanStep(
                action="equip",
                item="逃逸艺术家",
                item_instance_id="gaunt-exotic",
                slot="gauntlets",
            ),
        ],
    )


class _Bungie:
    def __init__(self, fail_on: str = "") -> None:
        self.equips: list[str] = []
        self._fail_on = fail_on

    async def equip_item(self, item_instance_id, character_id, membership_type):
        self.equips.append(item_instance_id)
        if item_instance_id == self._fail_on:
            return {"ErrorCode": 1622, "Message": "DestinyItemUniqueEquipRestricted"}
        return {"ErrorCode": 1, "Message": "Ok"}


class _Item:
    def __init__(self, instance_id: str, slot: str, name: str, *, equipped: bool) -> None:
        self.item_instance_id = instance_id
        self.slot = slot
        self.name = name
        self.is_equipped = equipped
        self.item_hash = 1
        self.character_id = "c1"


def _service(fail_on: str = "", target_equipped_after: bool = True):
    """假的 service：装备状态随写入变化（回读才对得上）。"""
    worn = {"chest": "chest-exotic", "gauntlets": "gaunt-legendary"}
    names = {
        "chest-exotic": "星火协议",
        "chest-legendary": "圣贤保护者法袍",
        "gaunt-legendary": "光芒领主手套",
        "gaunt-exotic": "逃逸艺术家",
    }
    bungie = _Bungie(fail_on)

    original = bungie.equip_item

    async def equip(item_instance_id, character_id, membership_type):
        result = await original(item_instance_id, character_id, membership_type)
        if result.get("ErrorCode") == 1:
            worn["chest" if item_instance_id.startswith("chest") else "gauntlets"] = item_instance_id
        return result

    bungie.equip_item = equip  # type: ignore[assignment]

    def _items():
        return [
            _Item(instance_id, slot, name, equipped=worn[slot] == instance_id)
            for slot, instance_id in list(worn.items())
            for name in [names[instance_id]]
        ] + [_Item("gaunt-legendary", "gauntlets", "光芒领主手套", equipped=worn["gauntlets"] == "gaunt-legendary")]

    service = TransferService(bungie, AsyncMock(), AsyncMock())
    service._resolver.resolve_player = AsyncMock(
        return_value={"membership_id": "1", "membership_type": 3}
    )
    service._resolver.resolve_character_id = AsyncMock(return_value="c1")
    service._resolver.get_profile = AsyncMock(return_value={})
    service._character_armor = lambda profile, char_id, class_type: _items()
    service._equipped_keys = lambda profile, char_id: {
        i.item_instance_id for i in _items() if i.is_equipped
    }
    service._describe_worn = AsyncMock(return_value="chest=圣贤保护者法袍")
    service._last_worn = worn
    return service, bungie


async def test_happy_path_equips_in_plan_order() -> None:
    service, bungie = _service()

    result = await service.execute_equip_plan("P#1", _plan(), "warlock")

    assert bungie.equips == ["chest-legendary", "gaunt-exotic"], "必须先顶下、再装目标"
    assert result["success"] is True and result["verified"] is True
    assert result["rolled_back"] is False


async def test_failure_rolls_back_what_was_already_changed() -> None:
    """第 2 步失败 → 第 1 步动过的胸甲要换回原来的星火协议。"""
    service, bungie = _service(fail_on="gaunt-exotic")

    result = await service.execute_equip_plan("P#1", _plan(), "warlock")

    assert result["success"] is False
    assert result["stopped_at"] == 2
    assert result["rolled_back"] is True
    assert bungie.equips == ["chest-legendary", "gaunt-exotic", "chest-exotic"]
    assert "已回滚" in result["message"]


async def test_failed_rollback_is_reported_honestly() -> None:
    """回滚也失败时不许说"已还原"。"""
    service, bungie = _service(fail_on="gaunt-exotic")
    original = bungie.equip_item

    async def equip(item_instance_id, character_id, membership_type):
        if item_instance_id == "chest-exotic":
            bungie.equips.append(item_instance_id)
            return {"ErrorCode": 500, "Message": "上游还在同步"}
        return await original(item_instance_id, character_id, membership_type)

    bungie.equip_item = equip  # type: ignore[assignment]

    result = await service.execute_equip_plan("P#1", _plan(), "warlock")

    assert result["rolled_back"] is False
    assert "回滚没做全" in result["message"]


async def test_readback_miss_is_unverified_not_success() -> None:
    """上游说成功、回读没看到目标 → unverified，不算成功。"""
    service, bungie = _service()

    async def equip(item_instance_id, character_id, membership_type):
        bungie.equips.append(item_instance_id)
        return {"ErrorCode": 1, "Message": "Ok"}  # 状态不变 → 回读永远看不到目标

    bungie.equip_item = equip  # type: ignore[assignment]

    result = await service.execute_equip_plan("P#1", _plan(), "warlock")

    assert result["success"] is False
    assert result["unverified"] is True
    assert "回读没确认" in result["message"]
