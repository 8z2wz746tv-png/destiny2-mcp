"""装备编排的两条次序纪律：**预检在任何写入之前**、**模组被挡不许吞掉子职业那一步**。

真机证据（2026-10-03 复测）暴露的是两类很不一样的毛病：

1. 模组预检原本排在搬运+批量装备**之后** —— 预检注定失败的批次已经把装备换好了，
   只能整条回滚（实测一次 3.5 分钟）。要钉住的是"预检失败时**一个字节都没写**"。
2. 有 1 颗模组被上游挡住（1676）时那个分支**提前 return**，于是 `subclass`（Step 3）
   整段没跑，而回执只说"装备已经换上" —— **少做了一步却不说**，最难查的一类。

这两条都属于"编排的次序"，所以放在一个文件里：改动它们时应该同时看见两条。
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from destiny_mcp.exceptions import TransferError
from destiny_mcp.models import Loadout, LoadoutItem, LoadoutSubclassConfig, ModOperation
from destiny_mcp.services import write_readback
from destiny_mcp.services.loadout_equipment_service import LoadoutEquipmentService

ITEM = "item-1"
HELMET_HASH = 3091179819
BLOCKED_MOD = 644105
UNLOCKED_MOD = 25154119


def _equipment() -> LoadoutEquipmentService:
    service = LoadoutEquipmentService(
        MagicMock(name="bungie"), MagicMock(name="manifest"), MagicMock(name="resolver"),
    )
    service._resolver.resolve_player = AsyncMock(
        return_value={"membership_id": "mid", "membership_type": 3}
    )
    service._resolver.resolve_character_id = AsyncMock(return_value="char-1")
    service._resolver.get_profile = AsyncMock(return_value={
        "characters": {"data": {"char-1": {"classType": 1}}},
        "itemComponents": {
            "instances": {"data": {ITEM: {"energy": {"energyCapacity": 11, "energyUsed": 0}}}},
            "sockets": {"data": {ITEM: {"sockets": [{"plugHash": 0}]}}},
        },
    })
    service._transfer.transfer_item = AsyncMock(return_value=SimpleNamespace(success=True))
    service._transfer.equip_items = AsyncMock(return_value={"success": True})
    service.read_armor_mod_sockets = MagicMock(return_value={})
    # 这两条测的是**编排次序**，不是恢复点：抓快照那一步换掉（`equip_loadout` 从
    # 2026-10-03 起与 `equip_build` 共用同一条恢复路径，见 loadout_exact_flow 的 docstring）。
    service._capture_recovery_state = AsyncMock(return_value={
        "membership_id": "mid",
        "membership_type": 3,
        "character_id": "char-1",
        "previous_loadout": Loadout(id="prev", name="执行前配装", character="hunter", items=[]),
        "target_states": {},
    })
    return service


def _loadout(*, with_subclass: bool = True) -> Loadout:
    return Loadout(
        id="local-1",
        name="猎套",
        character="hunter",
        items=[LoadoutItem(
            item_hash=HELMET_HASH,
            name="光芒领主面具",
            slot="helmet",
            item_instance_id=ITEM,
            mods=[UNLOCKED_MOD],
        )],
        subclass=(
            LoadoutSubclassConfig(
                subclass_item_hash=111,
                subclass_instance_id="sub-1",
                super_hash=222,
                plug_sockets={3: 222},
            )
            if with_subclass
            else None
        ),
    )


# ── ① 预检失败 = 零写入 ─────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_preflight_failure_writes_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    """预检判死就一步写入都不许发生：不搬运、不换装、不写模组、不碰子职业。

    真机 2026-10-03：预检排在写入之后，失败时装备已经换好，只能回滚（3.5 分钟白烧）。
    """
    monkeypatch.setattr(write_readback, "ATTEMPTS", 1)
    service = _equipment()
    service._prepare_mod_operations = AsyncMock(
        side_effect=TransferError("模组预检", "等 12 秒仍读不到 '光芒领主面具' 的插槽数据")
    )
    service._insert_armor_mod = AsyncMock()
    service._apply_subclass_config = AsyncMock()
    service._transfer.transfer_item = AsyncMock()
    service._transfer.equip_items = AsyncMock()

    result = await service.equip_local("Alpha#0100", _loadout())

    assert result.success is False
    preflight = next(step for step in result.steps if step.action == "mod_preflight")
    assert "插槽数据" in preflight.detail
    assert "一个字节都没动" in result.message, result.message
    # 这才是这条守门的重点：**零写入**
    service._transfer.transfer_item.assert_not_awaited()
    service._transfer.equip_items.assert_not_awaited()
    service._insert_armor_mod.assert_not_awaited()
    service._apply_subclass_config.assert_not_awaited()


@pytest.mark.asyncio
async def test_preflight_blocks_before_any_write_on_a_blocked_plug(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """反向：预检**通过**（只是有一颗装不上、属于 `blocked`）时不许提前收手。

    `blocked`（这一位装不上某颗）是 ADR-013 的"装备照换、如实报"，把它当预检失败会把
    本来能成的批次拦下来 —— 所以这里钉住"搬运照做"。
    """
    monkeypatch.setattr(write_readback, "ATTEMPTS", 1)
    service = _equipment()
    service._prepare_mod_operations = AsyncMock(
        return_value=[ModOperation("blocked", BLOCKED_MOD, 0, "这一位装不上")]
    )
    service._insert_armor_mod = AsyncMock()
    service._apply_subclass_config = AsyncMock(return_value=(True, ""))

    result = await service.equip_local("Alpha#0100", _loadout())

    service._transfer.transfer_item.assert_awaited_once()
    service._transfer.equip_items.assert_awaited_once()
    assert any(step.action == "mod_blocked" for step in result.steps), result.steps


# ── ② 模组被挡不许吞掉子职业那一步 ───────────────────────────────────────


@pytest.mark.asyncio
async def test_a_blocked_mod_still_runs_the_subclass_step() -> None:
    """一颗模组被上游挡住时，子职业那一步**要跑**，回执要说清整条的净结果。

    真机 2026-10-03 第 1 轮：`equip_build` 里 1 颗被 1676 挡住 → 那个分支提前 return →
    Step 3（子职业/碎片）整段跳过，而回执只说"装备已经换上"，**少做了一步却不说**。
    """
    service = _equipment()
    service._prepare_mod_operations = AsyncMock(
        return_value=[ModOperation("mod", BLOCKED_MOD, 0)]
    )
    service._insert_armor_mod = AsyncMock(return_value={
        "ErrorCode": 1676,
        "ErrorStatus": "DestinyFailedPlugInsertionRules",
        "Message": "The request to modify an item failed. Refresh the item and try again.",
    })
    service.plug_insertion_conditions = MagicMock(return_value=["必须在赛季神器中选择"])
    service._apply_subclass_config = AsyncMock(return_value=(True, ""))

    result = await service.equip_local("Alpha#0100", _loadout())

    service._apply_subclass_config.assert_awaited_once()
    assert any(step.action == "mod_blocked" for step in result.steps), result.steps
    assert result.success is False, "被挡住不许报成功"
    assert "子职业" in result.message, "回执必须交代子职业那一步到底做没做"
    assert "装备已经换上" in result.message


@pytest.mark.asyncio
async def test_a_blocked_mod_reports_the_failed_subclass_step(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """被挡 + 子职业也失败时，回执必须同时说清两件事，不能只报"装备已经换上"。"""
    monkeypatch.setattr(write_readback, "ATTEMPTS", 1)
    service = _equipment()
    service._prepare_mod_operations = AsyncMock(
        return_value=[ModOperation("mod", BLOCKED_MOD, 0)]
    )
    service._insert_armor_mod = AsyncMock(return_value={
        "ErrorCode": 1676, "ErrorStatus": "DestinyFailedPlugInsertionRules",
    })
    service.plug_insertion_conditions = MagicMock(return_value=["必须在赛季神器中选择"])
    service._apply_subclass_config = AsyncMock(return_value=(False, "fragment '复原'：上游没给原因"))

    result = await service.equip_local("Alpha#0100", _loadout())

    assert "1 颗模组" in result.message, result.message
    assert "子职业" in result.message, result.message
    assert "做了但没成" in result.message, result.message


@pytest.mark.asyncio
async def test_a_mod_write_failure_still_runs_the_subclass_step(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """模组阶段**硬失败**（上游没给"挡住"原因的）也不再顺手跳过子职业。

    以前那条 `if not all_ok: return` 同样会把 Step 3 吞掉 —— 同一个毛病，另一条分支。
    """
    monkeypatch.setattr(write_readback, "ATTEMPTS", 1)
    service = _equipment()
    service._prepare_mod_operations = AsyncMock(
        return_value=[ModOperation("mod", UNLOCKED_MOD, 0)]
    )
    service._insert_armor_mod = AsyncMock(return_value={
        "ErrorCode": 500, "Message": "", "ErrorStatus": "",
    })
    service._apply_subclass_config = AsyncMock(return_value=(True, ""))

    result = await service.equip_local("Alpha#0100", _loadout())

    assert result.success is False
    # 模组阶段硬失败时**子职业那一步照样跑**（这正是 2026-10-03 那条"少做了一步却不说"）。
    # `equip_loadout` 现在与 `equip_build` 共用恢复路径，所以失败后会回滚 ——
    # 回滚不等于"没跑过"：这里钉的是它跑过。
    service._apply_subclass_config.assert_awaited_once()
    assert any(step.action == "rollback_verify" for step in result.steps), result.steps


# ── ③ 两条入口共用同一条恢复路径 ─────────────────────────────────────────


@pytest.mark.asyncio
async def test_equip_loadout_takes_a_recovery_point_like_equip_build(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`equip_loadout` 失败时**必须走恢复**，与 `equip_build` 一样。

    真机 2026-10-03 复测点名了这条差异：`equip_build` 走 `equip_with_recovery`（有恢复点），
    而 `equip_loadout` 直接调 `_equip_local_unlocked` —— 同一条链路失败时，前者把账号恢复回
    执行前，后者留下"装备换了、模组只写了一半"的混合状态且谁也还原不了。
    这里钉住"抓过恢复点、失败后恢复被 await 过"。
    """
    monkeypatch.setattr(write_readback, "ATTEMPTS", 1)
    service = _equipment()
    service._capture_recovery_state = AsyncMock(return_value={
        "membership_id": "mid",
        "membership_type": 3,
        "character_id": "char-1",
        "previous_loadout": Loadout(id="prev", name="执行前配装", character="hunter", items=[]),
        "target_states": {},
    })
    service._restore_exact_state = AsyncMock(return_value=True)
    # 模组阶段硬失败（上游没给"挡住"的原因）→ 走回滚那条路
    service._prepare_mod_operations = AsyncMock(
        return_value=[ModOperation("mod", UNLOCKED_MOD, 0)]
    )
    service._insert_armor_mod = AsyncMock(return_value={
        "ErrorCode": 500, "Message": "", "ErrorStatus": "",
    })
    service._apply_subclass_config = AsyncMock(return_value=(True, ""))

    result = await service.equip_local("Alpha#0100", _loadout())

    service._capture_recovery_state.assert_awaited_once()
    service._restore_exact_state.assert_awaited_once()
    assert result.success is False


@pytest.mark.asyncio
async def test_a_doomed_preflight_never_rolls_back(monkeypatch: pytest.MonkeyPatch) -> None:
    """预检失败**不回滚**：那一步在任何写入之前，账号没动过，回滚没有对象。

    真机上回滚一次 3.5 分钟（逐件搬回 + 重装 + 回读），而这一趟什么都没做；
    更糟的是它会把"预检失败、这次没动账号"换成含糊的"已恢复执行前状态"。
    """
    monkeypatch.setattr(write_readback, "ATTEMPTS", 1)
    service = _equipment()
    service._capture_recovery_state = AsyncMock(return_value={
        "membership_id": "mid",
        "membership_type": 3,
        "character_id": "char-1",
        "previous_loadout": Loadout(id="prev", name="执行前配装", character="hunter", items=[]),
        "target_states": {},
    })
    service._restore_exact_state = AsyncMock(return_value=True)
    service._prepare_mod_operations = AsyncMock(
        side_effect=TransferError("模组预检", "找不到模组 644105 在 '光芒领主面具' 上的唯一兼容插槽。")
    )

    result = await service.equip_local("Alpha#0100", _loadout())

    assert result.success is False
    assert "一个字节都没动" in result.message, result.message
    service._restore_exact_state.assert_not_awaited()


# ── ④ 回执要点名"被换掉的是哪颗" ─────────────────────────────────────────


@pytest.mark.asyncio
async def test_energy_clear_names_the_plug_it_replaces() -> None:
    """`mod_clear` 的回执要写清**换下的是哪一颗**。

    真机 2026-10-03 之前的回执只有「为 'X' 腾出能量：'某件' 插槽 N」—— 玩家第一个要问的
    "我原来那颗去哪了"在回执里查不到，只能再逐件读一遍护甲。名字与 `mod` 那条路同一口径
    （`mod_label`），所以这里替身 Manifest 取不到名字时会退化成 `#hash`。
    """
    service = _equipment()
    # 名字取不到时 `mod_label` 会退化成 hash 本身 —— 这也正是要钉的：回执里必须有**那颗的
    # 标识**，不能只有"为某个东西腾能量"（真机回执的毛病就是后者）。
    service._manifest.get_item_name = MagicMock(return_value="")
    service._resolver.get_profile = AsyncMock(return_value={
        "characters": {"data": {"char-1": {"classType": 1}}},
        "itemComponents": {
            "instances": {"data": {ITEM: {"energy": {"energyCapacity": 11, "energyUsed": 0}}}},
            # 插槽 0 现在装着要换下那颗（腾能量就是为了它）
            "sockets": {"data": {ITEM: {"sockets": [{"plugHash": BLOCKED_MOD}]}}},
        },
    })
    service._prepare_mod_operations = AsyncMock(
        return_value=[ModOperation("clear", BLOCKED_MOD, 0)]
    )
    service._insert_armor_mod = AsyncMock(return_value={"ErrorCode": 1})

    result = await service.equip_local("Alpha#0100", _loadout())

    clears = [step for step in result.steps if step.action == "mod_clear"]
    assert clears, [step.action for step in result.steps]
    assert "换下" in clears[0].detail, clears[0].detail
    assert str(BLOCKED_MOD) in clears[0].detail, clears[0].detail
