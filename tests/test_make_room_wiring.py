"""`move` 与 `equip_loadout` 的"撞格满自动腾一件"接线守门（ADR-029 P4）。

判据本身在 `tests/test_make_room.py`；这里钉的是**两条入口真的接上了**：
上游回 `NoRoomInDestination`（只从 `TransferItem` 端点来）→ 在目标角色的同一个桶里腾一件 →
**重试一次**；腾不出来就照原样报失败（不硬写）。
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from destiny_mcp.exceptions import TransferError
from destiny_mcp.models import InventoryItem, Loadout, LoadoutItem
from destiny_mcp.models.transfer import TransferResult
from destiny_mcp.services.transfer_service import TransferService

_NO_ROOM = (
    "操作失败：Transfer vault→character。Internalservererror: (\n"
    "  http_status: 500,\n  message: There are no item slots available to transfer this item.,\n"
    "  error_status: DestinyNoRoomInDestination,"
)


def _item(iid: str, name: str, *, bucket: str, char: str = "c1", power: int = 500,
          locked: bool = False, location: str = "character") -> InventoryItem:
    return InventoryItem(
        item_instance_id=iid, item_hash=1, name=name, location=location,
        bucket_type=bucket, character_id=char if location != "vault" else "",
        power=power, is_locked=locked,
    )


def _service(items: list[InventoryItem]) -> TransferService:
    service = TransferService(MagicMock(), MagicMock(), MagicMock())
    service._resolver.resolve_player = AsyncMock(
        return_value={"membership_id": "m1", "membership_type": 3})
    service._resolver.resolve_character_id = AsyncMock(return_value="c1")
    service._fetch_all_items = AsyncMock(return_value=items)
    # 取件路径不止一处（名字检索 / 实例检索 / 现场读取）——这条用例验的是**重试接线**，
    # 所以把"按实例 ID 取回那件"直接打桩；取件本身不是这里的被测对象。
    target = next((i for i in items if i.item_instance_id == "t1"), None)
    service._find_item = AsyncMock(return_value=target)
    service._manifest.get_item_info = lambda _h: {"tier": 5}
    # `move_item` **总是先按名字查 Manifest**（即使传了实例 ID），所以这条也要给真的检索结果。
    service._manifest.search = lambda _name, limit=0: [{"itemHash": 1, "name": "要搬的枪"}]
    return service


@pytest.mark.asyncio
async def test_move_frees_a_slot_and_retries_once() -> None:
    """`move` 撞格满：腾一件（低光等、没锁的那件）再搬一次。

    这里钉的是 `_transfer_item_with_make_room` —— `move_item` 的搬运段就是它（全链路那层
    还会套账号锁与上游重试，替身在那两层里不好造"恰好第一次失败"，所以钉在它真正调用的地方；
    腾格的判据本身见 `tests/test_make_room.py`）。
    """
    target = _item("t1", "要搬的枪", bucket="Kinetic Weapons", location="vault")
    low = _item("low", "最不该留的枪", bucket="Kinetic Weapons", power=100)
    locked = _item("lock", "锁着的枪", bucket="Kinetic Weapons", power=10, locked=True)
    worn = _item("worn", "在穿的枪", bucket="Kinetic Weapons", power=10)
    worn.is_equipped = True
    service = _service([target, low, locked, worn])

    calls: list[str] = []
    failed_once = {"done": False}

    async def transfer(player_name, instance_id, destination, *, from_character=None):
        calls.append(instance_id)
        if instance_id == "t1" and not failed_once["done"]:
            failed_once["done"] = True
            raise TransferError(_NO_ROOM)
        return TransferResult(success=True, item_instance_id=instance_id,
                              message=f"搬 {instance_id}", from_location="vault", to_location="c1")

    service.transfer_item = transfer  # type: ignore[method-assign]

    moved, steps, note = await service._transfer_item_with_make_room(
        "Tester#1234", target, "warlock")

    assert moved.success, moved
    assert calls == ["t1", "low", "t1"], f"应当：先搬(失败) → 腾 low → 再搬一次，实际 {calls}"
    assert any(step.action == "make_room" for step in steps), steps
    assert "已自动腾出" in note and "重试" in note, note


@pytest.mark.asyncio
async def test_nothing_to_move_reports_the_original_failure() -> None:
    """腾不出来（桶里全是穿着/锁定的）→ 照原样报失败，**不重试**。"""
    target = _item("t1", "要搬的枪", bucket="Kinetic Weapons", location="vault")
    locked = _item("lock", "锁着的枪", bucket="Kinetic Weapons", power=10, locked=True)
    service = _service([target, locked])

    calls: list[str] = []

    async def transfer(player_name, instance_id, destination, *, from_character=None):
        calls.append(instance_id)
        raise TransferError(_NO_ROOM)

    service.transfer_item = transfer  # type: ignore[method-assign]

    moved, steps, note = await service._transfer_item_with_make_room(
        "Tester#1234", target, "warlock")

    assert not moved.success
    assert calls == ["t1"], f"腾不出来就不许重试，实际 {calls}"
    assert not [step for step in steps if step.action == "make_room"], steps
    assert "没有可腾的件" in note, note


@pytest.mark.asyncio
async def test_equip_loadout_frees_a_slot_and_retries_that_item() -> None:
    """`equip_loadout` 逐件搬；某一件撞格满 → 腾一件 → **只重试那一件**，其余照常。"""
    from destiny_mcp.services.loadout_transfer_step import TransferStepMixin

    class _Service(TransferStepMixin):
        def __init__(self, transfer) -> None:
            self._transfer = transfer

    calls: list[str] = []
    room_calls: list[str] = []

    async def transfer_item(player_name, instance_id, destination, **kwargs):
        calls.append(instance_id)
        if instance_id == "b" and calls.count("b") == 1:
            raise TransferError(_NO_ROOM)
        return TransferResult(success=True, item_instance_id=instance_id, message="搬好了")

    async def make_room_in_bucket(player_name, destination, *, for_instance_id):
        room_calls.append(for_instance_id)
        from destiny_mcp.models import MoveItemStep

        return [MoveItemStep(action="make_room", detail="腾格：'备用的' → 仓库", success=True)], "已自动腾出备用的（搬到仓库）。"

    transfer = SimpleNamespace(
        transfer_item=transfer_item, make_room_in_bucket=make_room_in_bucket)
    service = _Service(transfer)
    loadout = Loadout(
        id="L1", name="测试套", character="warlock",
        items=[
            LoadoutItem(item_hash=1, name="甲", slot="helmet", item_instance_id="a"),
            LoadoutItem(item_hash=2, name="乙", slot="chest", item_instance_id="b"),
        ],
    )

    steps, transferred, all_ok = await service.transfer_loadout_items("Tester#1234", loadout)

    assert all_ok, steps
    assert room_calls == ["b"], "只为撞满的那一件腾"
    assert calls.count("b") == 2, f"那一件要重试一次，实际 {calls}"
    assert any(s.action == "make_room" for s in steps), steps
    assert transferred == ["a", "b"]


def test_the_equipment_bucket_comes_from_the_manifest_not_from_the_vault_bucket() -> None:
    """**仓库里的件**在 profile 里报的是 `Vault (General)`，不是真正的装备桶 —— 要按定义查。

    2026-10-06 真机第六次翻车：`move` 往满的臂铠格搬，腾格去找"角色身上的 Vault (General) 桶"，
    永远找不到可腾的件，于是白报"没有可腾的件"。判据一旦用错桶，整条自动腾格就是死的。
    """
    service = _service([])
    # 真形状：`get_item_info` 把 `bucketTypeHash` 放**顶层**（2026-10-06 实测键清单）。
    service._manifest.get_item_info = lambda _h: {"bucketTypeHash": 3448274439}
    service._manifest.bucket_name = lambda h: "Gauntlets" if h in (3448274439, -846692857) else ""

    vault_item = _item("v1", "仓库里的臂铠", bucket="Vault (General)", location="vault")
    assert service._equipment_bucket_name(vault_item) == "Gauntlets"

    # 定义里查不到才退回 profile 值（缺数据不编名字）
    service._manifest.get_item_info = lambda _h: {}
    assert service._equipment_bucket_name(vault_item) == "Vault (General)"
