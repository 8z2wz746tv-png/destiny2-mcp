"""回滚（`_restore_exact_state`）的省钱纪律：**先比对，再写**。

真机证据（2026-10-03）：回滚过去逐颗重写记录里的模组，连"这个槽已经装着它"都不比一下。
上游对"再装一次"回的是 HTTP 500 + 1679 `DestinySocketAlreadyHasPlug`，客户端还会退避重试，
**每颗白花约 10 秒**；而回滚要恢复的常常本来就是没被改动过的那些格 —— 一次回滚逐颗写下来
就是几分钟白等。

判据的唯一出处是 `loadout_armor_state.socket_diffs`（穿快照那条路的 `_restore_one` 也用它），
这里钉住"回滚那一侧真的接到同一份判据上"。
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from destiny_mcp.models import Loadout, LoadoutItem
from destiny_mcp.services import loadout_verify
from destiny_mcp.services.loadout_equipment_service import LoadoutEquipmentService

ITEM = "item-1"
HELMET_HASH = 3091179819
#: 头盔桶（无符号）：`parse_items_from_profile` 靠它认出这是护甲
HELMET_BUCKET = 3448274439
SOCKET_MOD = 25154119          # 现场装着的那颗
OTHER_MOD = 4087056174         # 记录里要恢复的另一颗（与现场不同）


def _item(*, mod_sockets: dict[int, int], location: str = "hunter") -> LoadoutItem:
    return LoadoutItem(
        item_hash=HELMET_HASH,
        name="光芒领主面具",
        slot="helmet",
        item_instance_id=ITEM,
        mod_sockets=mod_sockets,
        source_location=location,
        source_character_id="char-1",
        was_equipped=True,
    )


def _service(live_sockets: list[dict]) -> LoadoutEquipmentService:
    service = LoadoutEquipmentService(
        MagicMock(name="bungie"), MagicMock(name="manifest"), MagicMock(name="resolver"),
    )
    service._resolver.resolve_player = AsyncMock(
        return_value={"membership_id": "mid", "membership_type": 3}
    )
    service._resolver.resolve_character_id = AsyncMock(return_value="char-1")
    service._resolver.get_profile = AsyncMock(return_value={
        "characters": {"data": {"char-1": {"classType": 1}}},
        "characterEquipment": {"data": {}},
        "characterInventories": {"data": {"char-1": {"items": []}}},
        "itemComponents": {
            "instances": {"data": {ITEM: {"energy": {"energyCapacity": 11, "energyUsed": 0}}}},
            "sockets": {"data": {ITEM: {"sockets": live_sockets}}},
        },
    })
    service._transfer.transfer_item = AsyncMock(return_value=MagicMock(success=True))
    service.read_armor_mod_sockets = MagicMock(return_value={0: SOCKET_MOD})
    service._insert_armor_mod = AsyncMock(return_value={"ErrorCode": 1})
    return service


def _stub_final_verify(monkeypatch: pytest.MonkeyPatch) -> None:
    """收尾那道回读核对与这两条守门无关：换掉，省得替身还要造一份完整档案。"""
    monkeypatch.setattr(loadout_verify, "verify_loadout", AsyncMock(return_value=True))
    monkeypatch.setattr(
        loadout_verify, "verify_restored_items", AsyncMock(return_value=True)
    )


def _recovery(recorded: LoadoutItem, previous: Loadout) -> dict:
    return {
        "membership_id": "mid",
        "membership_type": 3,
        "character_id": "char-1",
        "previous_loadout": previous,
        "target_states": {ITEM: recorded},
    }


@pytest.mark.asyncio
async def test_rollback_does_not_rewrite_a_socket_that_already_matches(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """现场与记录一致时**一个写入都不发**：省掉上游那四次 500 退避重试（每颗约 10 秒）。"""
    previous = Loadout(id="prev", name="执行前配装", character="hunter", items=[])
    recorded = _item(mod_sockets={0: SOCKET_MOD})
    service = _service([{"plugHash": SOCKET_MOD}])

    _stub_final_verify(monkeypatch)
    steps: list = []
    ok = await service._restore_exact_state(
        "Alpha#0100", Loadout(id="x", name="x", character="hunter", items=[]),
        _recovery(recorded, previous), steps,
    )

    assert ok is True, [step.detail for step in steps]
    service._insert_armor_mod.assert_not_awaited()
    same = [step for step in steps if step.action == "rollback_mod"]
    assert same and same[0].success is True
    assert "与执行前一致" in same[0].detail, same[0].detail


@pytest.mark.asyncio
async def test_rollback_writes_only_the_socket_that_differs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """对不上的那一格照写，**没对不上的不写** —— 一半一半才是这条判据真正的形状。"""
    previous = Loadout(id="prev", name="执行前配装", character="hunter", items=[])
    recorded = _item(mod_sockets={0: SOCKET_MOD, 1: OTHER_MOD})
    service = _service([{"plugHash": SOCKET_MOD}, {"plugHash": 999}])
    # 现场：0 号一致、1 号是别的 → 只有 1 号要写
    service.read_armor_mod_sockets = MagicMock(return_value={0: SOCKET_MOD, 1: 999})

    _stub_final_verify(monkeypatch)
    steps: list = []
    ok = await service._restore_exact_state(
        "Alpha#0100", Loadout(id="x", name="x", character="hunter", items=[]),
        _recovery(recorded, previous), steps,
    )

    assert ok is True, [step.detail for step in steps]
    service._insert_armor_mod.assert_awaited_once()
    args = service._insert_armor_mod.await_args.args
    assert args[0] == ITEM
    assert args[2] == 1, "只该写 1 号槽（0 号本来就一致）"
    assert args[1] == OTHER_MOD
