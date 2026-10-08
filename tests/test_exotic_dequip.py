"""批量装备前"顶下冲突金装"的守门（照 DIM `item-move-service.ts` 的 move aside exotics）。

真机 2026-10-06 实测：金装规则是**全身只能穿一件**，配装里的金装头盔 + 身上正穿的金装臂铠 →
上游批量 `EquipItems` **整批**回 1641，`equip_loadout` 全灭。DIM 的做法是发批量之前先给被顶下来的
那件找一件**同部位、非金装**的替身穿上去（`getSimilarItem({excludeExotic: true, exclusions})`）。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from unittest.mock import AsyncMock

import pytest

from destiny_mcp.models import InventoryItem, Loadout, LoadoutItem
from destiny_mcp.models.transfer import TransferResult
from destiny_mcp.services.loadout_transfer_step import ExoticDequipMixin

_TIER_EXOTIC = 6
_TIER_LEGENDARY = 5


@dataclass
class _Transfer:
    """替身：只实现 mixin 用到的那三个方法（列件 / 搬件 / 装件）。"""

    worn: list[InventoryItem]
    equipped: list[str] = field(default_factory=list)
    moved: list[str] = field(default_factory=list)

    async def list_character_items(self, player_name: str, character: str, *, include_vault: bool = False):
        vault = [i for i in self.worn if i.location == "vault"]
        body = [i for i in self.worn if i.location != "vault"]
        return body + vault if include_vault else body

    async def move_item(self, player_name: str, name: str, character: str, item_instance_id: str = ""):
        self.moved.append(item_instance_id)
        return TransferResult(success=True, item_instance_id=item_instance_id,
                              item_name=name, message=f"搬 {name}")

    async def equip_item(self, player_name: str, instance_id: str, character: str):
        self.equipped.append(instance_id)
        return TransferResult(success=True, item_instance_id=instance_id, message="装上了")


class _Service(ExoticDequipMixin):
    def __init__(self, manifest: dict[int, int], transfer: _Transfer) -> None:
        self._manifest = AsyncMock()
        # 按 hash 给 `inventory.tierType`（真形状：定义原文里 `tierType` 在 `inventory` 下）
        self._manifest.get_item_definition = lambda h: {"inventory": {"tierType": manifest.get(h, 0)}}
        self._transfer = transfer


def _item(iid: str, name: str, *, slot: str, hash_: int, power: int = 550,
          equipped: bool = False) -> InventoryItem:
    return InventoryItem(
        item_instance_id=iid, item_hash=hash_, name=name, location="warlock",
        bucket_type="", character_id="c1", power=power, is_equipped=equipped,
        slot=slot, slot_display=slot,
    )


def _loadout(items: list[LoadoutItem]) -> Loadout:
    return Loadout(id="L", name="测试套", character="warlock", items=items)


_H_EXOTIC_HELM = 100
_H_EXOTIC_ARMS = 200
_H_LEGEND_ARMS = 201


@pytest.mark.asyncio
async def test_a_worn_exotic_is_dequipped_with_a_non_exotic_before_the_batch() -> None:
    """身上穿着金装臂铠、配装要穿金装头盔 → 先拿**非金装**臂铠顶下来，再让批量装备去发。"""
    manifest = {
        _H_EXOTIC_HELM: _TIER_EXOTIC, _H_EXOTIC_ARMS: _TIER_EXOTIC, _H_LEGEND_ARMS: _TIER_LEGENDARY,
    }
    worn = [
        _item("w1", "金装臂铠", slot="gauntlets", hash_=_H_EXOTIC_ARMS, equipped=True),
        _item("w2", "普通臂铠", slot="gauntlets", hash_=_H_LEGEND_ARMS, power=540),
        _item("w3", "普通头盔", slot="helmet", hash_=300, equipped=True),
    ]
    transfer = _Transfer(worn)
    service = _Service(manifest, transfer)
    loadout = _loadout([
        LoadoutItem(item_hash=_H_EXOTIC_HELM, name="金装头盔", slot="helmet", item_instance_id="p1"),
    ])

    steps, ok, note = await service.dequip_conflicting_exotics("Tester#1234", loadout)

    assert ok and steps and transfer.equipped == ["w2"], f"该顶下 w2（非金装），实际 {transfer.equipped}"
    assert steps[0].action == "downgrade" and steps[0].success
    assert "顶下" in note and "金装" in note


@pytest.mark.asyncio
async def test_no_non_exotic_replacement_fails_without_equipping_anything() -> None:
    """没有非金装可顶 → **如实拒绝**（照 DIM 的 DimError：先把那件脱下来），不硬发批量、不动账号。"""
    manifest = {_H_EXOTIC_HELM: _TIER_EXOTIC, _H_EXOTIC_ARMS: _TIER_EXOTIC}
    worn = [
        _item("w1", "金装臂铠", slot="gauntlets", hash_=_H_EXOTIC_ARMS, equipped=True),
        _item("w2", "另一件金装臂铠", slot="gauntlets", hash_=_H_EXOTIC_ARMS),
    ]
    transfer = _Transfer(worn)
    service = _Service(manifest, transfer)
    loadout = _loadout([
        LoadoutItem(item_hash=_H_EXOTIC_HELM, name="金装头盔", slot="helmet", item_instance_id="p1"),
    ])

    steps, ok, note = await service.dequip_conflicting_exotics("Tester#1234", loadout)

    assert not ok and transfer.equipped == [], "拒绝时一个字节都不许动"
    assert not [s for s in steps if s.action == "downgrade"], steps
    assert "金装位" in note and "脱" in note


@pytest.mark.asyncio
async def test_no_conflict_does_nothing_at_all() -> None:
    """配装里没有金装（或冲突那一格本来就要换金装）→ 一步都不做。"""
    manifest = {_H_LEGEND_ARMS: _TIER_LEGENDARY, 300: _TIER_LEGENDARY}
    worn = [
        _item("w1", "金装臂铠", slot="gauntlets", hash_=_H_EXOTIC_ARMS, equipped=True),
        _item("w2", "普通臂铠", slot="gauntlets", hash_=_H_LEGEND_ARMS),
    ]
    transfer = _Transfer(worn)
    service = _Service(manifest, transfer)
    loadout = _loadout([
        LoadoutItem(item_hash=300, name="普通头盔", slot="helmet", item_instance_id="p1"),
    ])

    steps, ok, note = await service.dequip_conflicting_exotics("Tester#1234", loadout)

    assert steps == [] and ok and note == "", (steps, ok, note)
    assert transfer.equipped == []


@pytest.mark.asyncio
async def test_the_replacement_can_come_from_the_vault() -> None:
    """身上没有非金装时**去仓库拉一件**再搬进来穿（DIM：*including de-equip replacements pulled
    from the vault*）—— 只看身上会把"仓库里有 79 件"误判成"没得顶"。"""
    manifest = {_H_EXOTIC_HELM: _TIER_EXOTIC, _H_EXOTIC_ARMS: _TIER_EXOTIC,
                _H_LEGEND_ARMS: _TIER_LEGENDARY}
    worn = [
        _item("w1", "金装臂铠", slot="gauntlets", hash_=_H_EXOTIC_ARMS, equipped=True),
        # 仓库里的那件：**`slot` 是空串**（profile 给 Vault (General)，这是真形状！）→ 部位只能按
        # 物品定义的 bucketTypeHash 认；职业也要对得上（classType 2 = 术士）
        _item("v1", "仓库里的普通臂铠", slot="", hash_=_H_LEGEND_ARMS,
              power=540).model_copy(update={"location": "vault", "character_id": ""}),
    ]
    transfer = _Transfer(worn)
    service = _Service(manifest, transfer)
    # 真形状：`get_item_info` 里 `classType`/`bucketTypeHash` 都在**顶层**（3551918588 = 臂铠桶）
    service._manifest.get_item_info = lambda _h: {"classType": 2, "bucketTypeHash": 3551918588}
    loadout = _loadout([
        LoadoutItem(item_hash=_H_EXOTIC_HELM, name="金装头盔", slot="helmet", item_instance_id="p1"),
    ])

    steps, ok, note = await service.dequip_conflicting_exotics("Tester#1234", loadout)

    assert ok, (steps, note)
    assert transfer.moved == ["v1"], f"该先把仓库那件搬进来，实际 {transfer.moved}"
    assert transfer.equipped == ["v1"], f"再穿上它，实际 {transfer.equipped}"
    assert [s.action for s in steps] == ["transfer", "downgrade"], steps
