"""Focused regressions for exact build confirmation and execution."""

from __future__ import annotations

import asyncio
import os
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
import anyio

os.environ.setdefault("BUNGIE_API_KEY", "dummy")
os.environ.setdefault("BUNGIE_CLIENT_ID", "1")
os.environ.setdefault("BUNGIE_CLIENT_SECRET", "dummy")

from destiny_mcp.build.constraints import parse as parse_constraints
from destiny_mcp.build.models import (
    Armor,
    BuildRequest,
    InventorySnapshot,
)
from destiny_mcp.build_contracts import CanonicalBuild
from destiny_mcp.exceptions import BuildValidationError
from destiny_mcp.models import (
    ArmorStats,
    Loadout,
    LoadoutItem,
    LoadoutOperationResult,
    MoveItemStep,
)
from destiny_mcp.services import build_candidates as build_candidates_module
from destiny_mcp.services import loadout_exact_flow as exact_flow_module
from destiny_mcp.services import loadout_verify
from destiny_mcp.services import write_readback
from destiny_mcp.build.snapshot_version import snapshot_version
from destiny_mcp.services.build_service import BuildService
from destiny_mcp.services.loadout_equipment_service import LoadoutEquipmentService
from destiny_mcp.tools import _build_confirmation
from destiny_mcp.tools.assistants import build_assistant


class _Client:
    pass


def _loadout() -> Loadout:
    return Loadout(
        id="exact",
        name="Exact",
        character="hunter",
        items=[
            LoadoutItem(
                item_hash=100,
                name="Helmet",
                slot="helmet",
                item_instance_id="item-1",
                mods=[400],
            )
        ],
    )


def stub_verify(monkeypatch: pytest.MonkeyPatch, value: bool) -> AsyncMock:
    """回读核对的替身：`_verify_loadout` 已搬成模块函数 `loadout_verify.verify_loadout`。

    搬家的原因：`loadout_equipment_service.py` 贴着体量上限，而"核对"与"执行"本来就是两件事。
    """
    stub = AsyncMock(return_value=value)
    monkeypatch.setattr(loadout_verify, "verify_loadout", stub)
    return stub


def _equipment_service() -> LoadoutEquipmentService:
    return LoadoutEquipmentService(
        _Client(),  # type: ignore[arg-type]
        MagicMock(),
        MagicMock(),
    )


def _exact_contract() -> tuple[InventorySnapshot, CanonicalBuild]:
    slot_data = [
        ("helmets", "helmet"),
        ("gauntlets", "gauntlets"),
        ("chests", "chest"),
        ("legs", "legs"),
        ("class_items", "class_item"),
    ]
    armor = [
        Armor(
            item_instance_id=f"item-{index}",
            item_hash=index,
            name=f"Armor {index}",
            slot=source_slot,
            stats=ArmorStats(weapons=10 + index),
            energy_capacity=10,
        )
        for index, (source_slot, _) in enumerate(slot_data, 1)
    ]
    snapshot = InventorySnapshot(
        helmets=[armor[0]],
        gauntlets=[armor[1]],
        chests=[armor[2]],
        legs=[armor[3]],
        class_items=[armor[4]],
    )
    build = CanonicalBuild(
        class_type="hunter",
        items=[
            LoadoutItem(
                item_hash=item.item_hash,
                name=item.name,
                slot=target_slot,
                item_instance_id=item.item_instance_id,
                mods=[1000 + index],
            )
            for index, (item, (_, target_slot)) in enumerate(
                zip(armor, slot_data), 1
            )
        ],
        snapshot_version=snapshot_version(snapshot),
        execution_id="candidate-1",
    )
    return snapshot, build


def _build_service() -> BuildService:
    return BuildService(
        _Client(),  # type: ignore[arg-type]
        MagicMock(),
        MagicMock(),
    )


def _tool_context(services: dict) -> SimpleNamespace:
    return SimpleNamespace(
        request_context=SimpleNamespace(lifespan_context=services)
    )


def test_exotic_confirmation_token_rejects_tampering_and_other_player(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    now = 1_000
    monkeypatch.setattr(_build_confirmation.time, "time", lambda: now)
    arguments = {
        "intent": "recommend",
        "character": "hunter",
        "exotic_name": "Caliban's Hand",
        "confirmed_exotic_hash": 123,
    }
    token = _build_confirmation.issue_exotic_confirmation_token(
        arguments,
        player_name="Alpha#0100",
    )

    assert _build_confirmation.verify_exotic_confirmation_token(
        token,
        arguments,
        player_name="Alpha#0100",
    )
    assert not _build_confirmation.verify_exotic_confirmation_token(
        token,
        {**arguments, "confirmed_exotic_hash": 456},
        player_name="Alpha#0100",
    )
    assert not _build_confirmation.verify_exotic_confirmation_token(
        token,
        arguments,
        player_name="Beta#0200",
    )


def test_exotic_confirmation_token_expires(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    now = 1_000
    monkeypatch.setattr(_build_confirmation.time, "time", lambda: now)
    monkeypatch.setattr(_build_confirmation, "_TOKEN_TTL_SECONDS", 10)
    arguments = {"intent": "find", "confirmed_exotic_hash": 123}
    token = _build_confirmation.issue_exotic_confirmation_token(
        arguments,
        player_name="Alpha#0100",
    )

    now = 1_010
    assert not _build_confirmation.verify_exotic_confirmation_token(
        token,
        arguments,
        player_name="Alpha#0100",
    )


@pytest.mark.asyncio
async def test_build_assistant_requires_fuzzy_exotic_confirmation() -> None:
    build_service = SimpleNamespace(
        resolve_exotic_armor=MagicMock(return_value={
            "status": "confirmation_required",
            "matches": [
                {
                    "name": "卡利班之手",
                    "nameEn": "Caliban's Hand",
                    "item_hash": 123,
                    "icon_url": "https://bungie.example/caliban.jpg",
                    "class_type": 1,
                }
            ],
        }),
        recommend_build=AsyncMock(return_value={"results": [{"id": "build-1"}]}),
    )
    ctx = _tool_context({"build_svc": build_service})

    confirmation = await build_assistant(
        intent="recommend",
        player_name="Alpha#0100",
        character="hunter",
        exotic_name="卡利班手",
        class_target=200,
        include_subclass_fragment=True,
        priority_stats=["weapons", "melee"],
        ctx=ctx,
    )

    assert confirmation["error"]["code"] == "exotic_confirmation_required"
    build_service.recommend_build.assert_not_awaited()
    arguments = confirmation["candidates"][0]["arguments"]
    assert arguments["confirmed_exotic_hash"] == 123
    assert arguments["class_target"] == 200
    assert arguments["include_subclass_fragment"] is True
    assert arguments["priority_stats"] == ["weapons", "melee"]
    assert isinstance(arguments["exotic_confirmation_token"], str)

    confirmed = await build_assistant(**arguments, ctx=ctx)

    assert confirmed["ok"] is True
    request = build_service.recommend_build.await_args.args[1]
    assert request.exotic_name == "卡利班之手"
    assert request.class_target == 200
    assert request.include_subclass_fragment is True
    assert request.priority_stats == ["weapons", "melee"]


@pytest.mark.asyncio
async def test_farm_target_confirmation_preserves_two_piece_fallback() -> None:
    infer_required_armor = AsyncMock(return_value={
        "reason": "farm_target_ready",
        "farm_options": [],
        "farm_plans": [{
            "replacement_count": 2,
            "baseline": "equipped",
            "pieces": [
                {
                    "replacement_slot": "gauntlets",
                    "archetype_hash": 11,
                    "archetype_name": "Specialist",
                    "primary_stat": "class_stat",
                    "secondary_stat": "weapons",
                    "tertiary_stat": "health",
                    "base_stats": {"weapons": 25, "health": 20, "class_stat": 30},
                    "masterworked_stats": {"weapons": 25, "health": 20, "class_stat": 30},
                    "tuning_hash": 12,
                    "projected_stats": {"weapons": 25, "health": 20, "class_stat": 30},
                },
                {
                    "replacement_slot": "chests",
                    "archetype_hash": 21,
                    "archetype_name": "Brawler",
                    "primary_stat": "melee",
                    "secondary_stat": "health",
                    "tertiary_stat": "class_stat",
                    "base_stats": {"health": 25, "class_stat": 20, "melee": 30},
                    "masterworked_stats": {"health": 25, "class_stat": 20, "melee": 30},
                    "tuning_hash": 22,
                    "projected_stats": {"health": 25, "class_stat": 20, "melee": 30},
                },
            ],
            "projected_total": {"class_stat": 200, "weapons": 150},
            "stat_mods": [123],
            "locked_items": [{
                "slot": "helmets",
                "name": "卡利班之手",
                "item_instance_id": "private-id",
            }],
        }],
        "assumptions": ["单件已完整搜索且无解。"],
    })
    build_service = SimpleNamespace(
        resolve_exotic_armor=MagicMock(return_value={
            "status": "confirmation_required",
            "matches": [{
                "name": "卡利班之手",
                "nameEn": "Caliban's Hand",
                "item_hash": 123,
                "icon_url": "https://bungie.example/caliban.jpg",
                "class_type": 1,
            }],
        }),
        infer_required_armor=infer_required_armor,
    )
    ctx = _tool_context({"build_svc": build_service})

    confirmation = await build_assistant(
        intent="farm_target",
        player_name="Alpha#0100",
        character="hunter",
        exotic_name="卡利班手",
        class_target=200,
        replacement_slot="gauntlets",
        ctx=ctx,
    )

    arguments = confirmation["candidates"][0]["arguments"]
    assert arguments["max_replacements"] == 2
    confirmed = await build_assistant(**arguments, ctx=ctx)

    assert confirmed["ok"] is True
    assert "最少替换两件" in confirmed["summary"]
    assert confirmed["data"]["query"]["max_replacements"] == 2
    assert infer_required_armor.await_args.kwargs == {
        "replacement_slot": "gauntlets",
        "baseline": "equipped",
        "max_replacements": 2,
    }
    plan = confirmed["data"]["farm_target"]["farm_plans"][0]
    assert plan["projected_total"]["class"] == 200
    for private_field in (
        "archetype_hash",
        "tuning_hash",
        "stat_mods",
        "item_instance_id",
    ):
        assert private_field not in str(plan)


@pytest.mark.asyncio
async def test_build_assistant_returns_structured_domain_errors() -> None:
    build_service = SimpleNamespace(
        recommend_build=AsyncMock(
            side_effect=BuildValidationError(
                "必须指定 hunter、warlock 或 titan。"
            )
        )
    )

    result = await build_assistant(
        intent="recommend",
        player_name="Alpha#0100",
        ctx=_tool_context({"build_svc": build_service}),
    )

    assert result["ok"] is False
    assert result["error"]["code"] == "build_validation_error"


@pytest.mark.asyncio
async def test_equip_build_by_execution_id_against_the_real_service(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """工具层"只给候选 ID"那条路对**真服务**跑一遍。

    替身对替身的测试抓不到"工具 await 了一个同步方法"这类形状错配
    （真机语料 ⑯b 抓到过：候选查询在进程内、是同步的，工具层却 await 它）。
    """
    from destiny_mcp.tools import _armor_branches as armor_branches

    async def _no_preview(svc, player_name, canonical_build):
        return []

    monkeypatch.setattr(armor_branches, "equip_preview", _no_preview)
    service = _build_service()
    snapshot, build = _exact_contract()
    service._candidates.register(build, "Alpha#0100")
    service._inventory.get_armor_snapshot = AsyncMock(return_value=snapshot)
    service._equipment.equip_with_recovery = AsyncMock(return_value=LoadoutOperationResult(
        success=True,
        loadout_name="Exact",
        message="OK",
    ))
    services = {"build_svc": service}

    confirm = await armor_branches.equip_build(
        services, "Alpha#0100", None, build.execution_id, "hunter", False
    )

    assert confirm["error"]["code"] == "confirmation_required"
    assert confirm["candidates"][0]["execution_id"] == build.execution_id
    service._inventory.get_armor_snapshot.assert_not_awaited()

    done = await armor_branches.equip_build(
        services, "Alpha#0100", None, build.execution_id, "hunter", True
    )

    assert done["ok"] is True
    service._equipment.equip_with_recovery.assert_awaited_once()
    # 一次确认只能用一次：同一个 ID 再来一次就是"不认识"
    replay = await armor_branches.equip_build(
        services, "Alpha#0100", None, build.execution_id, "hunter", True
    )
    assert replay["error"]["code"] == "unknown_execution_id"


@pytest.mark.asyncio
async def test_exact_exotic_name_solves_without_a_confirmation_round() -> None:
    """唯一**精确**匹配不再多问一轮（ADR-017）：直接求解，并说明用的是哪件。

    模糊/多个命中仍然要确认（下一条测试钉住），这条只管"没有选择可做"的那种。
    """
    build_service = SimpleNamespace(
        resolve_exotic_armor=MagicMock(return_value={
            "status": "exact",
            "query": "快速装弹松身裤",
            "canonical_name": "快速装弹松身裤",
            "matches": [{
                "name": "快速装弹松身裤",
                "name_en": "St0mp-EE5",
                "item_hash": 456,
                "icon_url": "",
                "class_type": 0,
                "score": 1.0,
            }],
        }),
        recommend_build=AsyncMock(return_value={"results": [{"id": "build-1"}]}),
    )

    result = await build_assistant(
        intent="recommend",
        player_name="Alpha#0100",
        character="hunter",
        exotic_name="快速装弹松身裤",
        class_target=200,
        ctx=_tool_context({"build_svc": build_service}),
    )

    assert result["ok"] is True, result.get("error")
    build_service.recommend_build.assert_awaited_once()
    resolution = (result["data"]["query"] or {}).get("exotic_resolution") or {}
    assert resolution.get("status") == "exact_match"
    assert resolution.get("item_hash") == 456
    assert (result["data"]["query"] or {}).get("exotic_name") == "快速装弹松身裤"


@pytest.mark.asyncio
async def test_fuzzy_exotic_name_still_requires_confirmation() -> None:
    """模糊命中（要挑一件）仍然停一轮：不替玩家选定，并给出每个候选的 arguments。"""
    build_service = SimpleNamespace(
        resolve_exotic_armor=MagicMock(return_value={
            "status": "confirmation_required",
            "query": "卡利班手",
            "matches": [
                {"name": "卡利班之手", "name_en": "Caliban's Hand", "item_hash": 1,
                 "icon_url": "", "class_type": 1, "score": 0.8},
                {"name": "卡利班之握", "name_en": "Caliban's Grips", "item_hash": 2,
                 "icon_url": "", "class_type": 1, "score": 0.7},
            ],
        }),
        recommend_build=AsyncMock(return_value={"results": []}),
    )

    result = await build_assistant(
        intent="recommend",
        player_name="Alpha#0100",
        character="hunter",
        exotic_name="卡利班手",
        ctx=_tool_context({"build_svc": build_service}),
    )

    assert result["error"]["code"] == "exotic_confirmation_required"
    assert [row["item_hash"] for row in result["candidates"]] == [1, 2]
    build_service.recommend_build.assert_not_awaited()


@pytest.mark.asyncio
async def test_apply_failure_always_carries_a_reason() -> None:
    """写入失败的回执**不许留白**：`TimeoutError()` 的 str 是空的（真机 2026-09-23 踩到）。

    那次 `apply` 步骤 detail 为空，谁也看不出发生了什么；现在至少给出异常类型与"状态未知"。
    """
    service = _build_service()
    snapshot, build = _exact_contract()
    service._candidates.register(build, "Alpha#0100")
    service._inventory.get_armor_snapshot = AsyncMock(return_value=snapshot)
    service._equipment._capture_recovery_state = AsyncMock(return_value={})
    service._equipment._restore_exact_state = AsyncMock(return_value=True)

    async def _boom(*args, **kwargs):
        raise TimeoutError()

    service._equipment._equip_local_unlocked = _boom

    result = await service.equip_build("Alpha#0100", build, "hunter")

    assert result["success"] is False
    apply_step = next(step for step in result["steps"] if step["action"] == "apply")
    assert apply_step["detail"].strip(), "失败必须带原因，不能是空串"
    assert "超时" in apply_step["detail"]
    assert "未知" in apply_step["detail"], "超时后写入是否生效是未知的，要说清"


def test_priority_stats_keep_strict_order_and_remove_duplicates() -> None:
    parsed = parse_constraints(
        BuildRequest(
            character_class="hunter",
            priority_stats=["melee", "weapons", "melee"],
        ),
        MagicMock(),
    )

    assert parsed.ordered_priority_indices == [4, 0]


@pytest.mark.asyncio
async def test_exact_equipment_verifies_success(monkeypatch: pytest.MonkeyPatch) -> None:
    service = _equipment_service()
    service._capture_recovery_state = AsyncMock(return_value={})
    service._equip_local_unlocked = AsyncMock(return_value=LoadoutOperationResult(
        success=True,
        loadout_name="Exact",
        message="applied",
    ))
    stub_verify(monkeypatch, True)
    service._restore_exact_state = AsyncMock()

    result = await service.equip_with_recovery("Alpha#0100", _loadout())

    assert result.success is True
    assert result.steps[-1].action == "verify"
    service._restore_exact_state.assert_not_awaited()


@pytest.mark.asyncio
async def test_a_blocked_plug_keeps_the_equipment_and_skips_rollback() -> None:
    """上游拒绝某一颗插件（1675 等）时**不回退**：装备已经换好了，如实汇报就行。

    这条分支以前**没有测试**盖住（`_equip_local_unlocked` 返回 `success=False` + 一个
    `mod_blocked` step）。2026-09-22 起它是"调谐写不进去不回退"的落地处 —— 调谐插件现在跟在
    `mods` 里由同一个执行器写，所以要钉住：既不能回退，也不能报成"成功"。
    """
    service = _equipment_service()
    service._capture_recovery_state = AsyncMock(return_value={})
    service._equip_local_unlocked = AsyncMock(return_value=LoadoutOperationResult(
        success=False,
        loadout_name="Exact",
        message="装备已经换上；但 1 颗模组被上游拒绝写入",
        steps=[MoveItemStep(action="mod_blocked", detail="要材料（1675）", success=False)],
    ))
    service._restore_exact_state = AsyncMock(return_value=True)

    result = await service.equip_with_recovery("Alpha#0100", _loadout())

    assert result.success is False, "被挡住不许报成功"
    service._restore_exact_state.assert_not_awaited()
    assert not any(step.action == "verify" for step in result.steps)


@pytest.mark.asyncio
async def test_exact_equipment_does_not_roll_back_an_unconfirmed_readback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """**这条测试原来叫 `…rolls_back_failed_verification`，它钉的正是要修的那个 bug。**

    旧代码把 `equip_with_recovery` 外侧那次回读写成"读一次"，读不到就 `success=False` + 整条回滚；
    而内层（`_equip_local_unlocked` 的 Step 4）是重试约 10.5 秒、并明确写"别当成没装上"。
    于是同步窗口一超过 10.5 秒，**同一份账号状态被读成两种结论**，把已经换好的配装整条改回
    旧状态。`write_readback` 的口径写得很清楚：没确认 ≠ 没换成。

    所以**改的是测试**：写入阶段一路成功、只是回读重试满窗口后仍对不上时，正确结论是
    "未确认" —— **不启动回滚**，`verified=False` 让上层自己决定怎么说，`success` 保持
    "写入都成功了"（`ok=true` 说的是"写入成功"，具体确认与否由 `verified` 承担）。
    真正该回滚的是"写入阶段就失败了"——那条由
    `test_partial_batch_equip_stops_mods_and_rolls_back` 盖住（`_equip_local_unlocked`
    返回 `success=False`，`_restore_exact_state` 必须被 await）。
    """
    monkeypatch.setattr(write_readback, "ATTEMPTS", 1)
    service = _equipment_service()
    recovery = {"captured": True}
    service._capture_recovery_state = AsyncMock(return_value=recovery)
    service._equip_local_unlocked = AsyncMock(return_value=LoadoutOperationResult(
        success=True,
        loadout_name="Exact",
        message="applied",
    ))
    stub_verify(monkeypatch, False)
    service._restore_exact_state = AsyncMock(return_value=True)

    result = await service.equip_with_recovery("Alpha#0100", _loadout())

    assert result.verified is False, "没确认要如实记在 verified 上"
    assert "别当成没装上" in result.message, result.message
    service._restore_exact_state.assert_not_awaited()
    verify_step = next(step for step in result.steps if step.action == "verify")
    assert verify_step.success is False


@pytest.mark.asyncio
async def test_exact_equipment_reads_back_through_the_shared_retry_window(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """外侧回读**必须走 `write_readback` 的重试**，而不是读一次就下结论。

    真机 2026-10-03 第 3 轮：内层重试满约 10.5 秒、按口径报"别当成没装上"，而外侧**只读一次**
    就判失败，于是同步窗口一超过 10.5 秒，同一份状态被读成两种结论、整条配装白白回滚。
    这里让前两次读返回 False、第三次 True：换回"读一次"的实现，这条立刻变红。
    """
    monkeypatch.setattr(write_readback, "ATTEMPTS", 4)
    monkeypatch.setattr(write_readback, "DELAY_SECONDS", 0)
    service = _equipment_service()
    service._capture_recovery_state = AsyncMock(return_value={})
    service._equip_local_unlocked = AsyncMock(return_value=LoadoutOperationResult(
        success=True,
        loadout_name="Exact",
        message="applied",
        verified=None,  # 内层没给结论（替身路径）→ 外侧要自己按同步窗口重试
    ))
    reads = []

    async def slow_to_sync(*args):
        reads.append(1)
        return len(reads) >= 3

    monkeypatch.setattr(loadout_verify, "verify_loadout", slow_to_sync)
    service._restore_exact_state = AsyncMock()

    result = await service.equip_with_recovery("Alpha#0100", _loadout())

    assert len(reads) == 3, f"要重试到对上为止，实际读了 {len(reads)} 次"
    assert result.verified is True
    assert result.success is True
    service._restore_exact_state.assert_not_awaited()


@pytest.mark.asyncio
async def test_exact_equipment_reuses_the_inner_verdict_without_a_second_read(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """内层已经核对过就不再读第二遍：同一份判据、同一个窗口，重读只是白等一个窗口。

    要读第二遍的只有一种情形 —— 内层没给结论（`verified=None`：外层直接拿别的替身跑了
    `_equip_local_unlocked`），那时才按 `write_readback` 重试（见上一条）。
    """
    service = _equipment_service()
    service._capture_recovery_state = AsyncMock(return_value={})
    service._equip_local_unlocked = AsyncMock(return_value=LoadoutOperationResult(
        success=True,
        loadout_name="Exact",
        message="applied",
        verified=True,
    ))
    verify = stub_verify(monkeypatch, False)
    service._restore_exact_state = AsyncMock()

    result = await service.equip_with_recovery("Alpha#0100", _loadout())

    assert result.success is True
    assert result.verified is True
    verify.assert_not_awaited()
    service._restore_exact_state.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("cancel_mode", ["asyncio", "mcp_scope"])
@pytest.mark.parametrize("phase", ["apply", "verify", "rollback"])
async def test_cancelled_equipment_recovers_before_releasing_account_lock(
    cancel_mode: str, phase: str, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """取消发生在哪一段，都要**先把执行前状态恢复完**再放开账号写入的锁。

    `phase="rollback"` 那档要让**写入阶段真的失败**：2026-10-03 之后"写入全成功、只是
    回读没确认"不再触发回滚（口径见 `write_readback` 的"没确认 ≠ 没换成"），
    所以只有 `applied.success=False` 才会走到恢复那一趟 —— 这正是这一档要取消的东西。

    这条测试原本靠 `await asyncio.wait_for(interrupted.wait(), 1)` 去"等取消时机"，
    而 `verify` 那一步内层会按 `write_readback` 重试整整一个同步窗口（8 × 1.5 秒）——
    于是"读到哪一次才算被取消"取决于读循环的节奏，测试变成一场竞速：把外层回读改成
    复用 `write_readback`（同一个窗口）之后，取消总是落在 `sleep(1.5)` 里，
    那个 1 秒的等待就先超时了（真机口径没变，是测试自己不稳）。

    所以这里把等待换成**自旋 + 明确的截止时间**：不再猜"第几次读会被取消"，
    只规定"取消必须在我等够之前落地"。钉的契约一条没动 —— 锁在恢复完成前不放、
    恢复期间别的写入进不来、恢复动作确实发生过。
    """
    service = _equipment_service()
    recovery = {"captured": True}
    service._capture_recovery_state = AsyncMock(return_value=recovery)
    interrupted = asyncio.Event()
    restoring = asyncio.Event()
    release_restore = asyncio.Event()
    next_write = asyncio.Event()
    state = {"equipment": "before"}
    scopes = []

    async def apply(*args):
        state["equipment"] = "changed"
        if phase == "apply":
            interrupted.set()
            await asyncio.Event().wait()
        return LoadoutOperationResult(success=phase != "rollback", message="synthetic")

    async def verify(*args):
        if phase == "verify":
            interrupted.set()
            await asyncio.Event().wait()
        return False

    # 前两档取消在"写入还没结束"的时候，这条回读窗口用不上；压成 1 次省掉等待，
    # rollback 档根本不走回读（写入阶段就失败了）。
    monkeypatch.setattr(write_readback, "ATTEMPTS", 1)

    async def restore(*args):
        if phase == "rollback" and not interrupted.is_set():
            interrupted.set()
            await asyncio.Event().wait()
        # Real recovery calls serialized transfer methods in the same task.
        async with service._equip_lock:
            restoring.set()
            await release_restore.wait()
            state["equipment"] = "before"
            return True

    service._equip_local_unlocked = apply
    monkeypatch.setattr(loadout_verify, "verify_loadout", verify)
    service._restore_exact_state = AsyncMock(side_effect=restore)

    async def run():
        with anyio.CancelScope() as scope:
            scopes.append(scope)
            await service.equip_with_recovery("Alpha#0100", _loadout())

    async def subsequent_write():
        async with service._equip_lock:
            assert state["equipment"] == "before"
            next_write.set()

    async def wait_for_event(event: asyncio.Event, deadline: float) -> None:
        """自旋等待（不用 `wait_for`）：等不到就是这条守门真的没触发，直接判红。"""
        loop = asyncio.get_running_loop()
        while loop.time() < deadline:
            if event.is_set():
                return
            await asyncio.sleep(0.01)
        raise AssertionError("取消没有在等够的时间内落到该落的那一段上")

    task = asyncio.create_task(run())
    other = None
    try:
        await wait_for_event(interrupted, asyncio.get_running_loop().time() + 30)
        if cancel_mode == "asyncio":
            task.cancel()
        else:
            scopes[0].cancel()
        await wait_for_event(restoring, asyncio.get_running_loop().time() + 30)
        other = asyncio.create_task(subsequent_write())
        await asyncio.sleep(0)
        assert not next_write.is_set()
        release_restore.set()
        if cancel_mode == "asyncio":
            with pytest.raises(asyncio.CancelledError):
                await asyncio.wait_for(task, 5)
        else:
            await asyncio.wait_for(task, 5)
        await asyncio.wait_for(other, 5)
        assert state["equipment"] == "before"
        assert next_write.is_set()
        assert service._restore_exact_state.await_count == (2 if phase == "rollback" else 1)
    finally:
        release_restore.set()
        for pending in (task, other):
            if pending is not None and not pending.done():
                pending.cancel()
        await asyncio.gather(*(pending for pending in (task, other) if pending), return_exceptions=True)


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["timeout", "exception", "incomplete"])
async def test_cancelled_recovery_is_bounded_and_reports_failure(
    failure: str, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture,
) -> None:
    service = _equipment_service()
    service._capture_recovery_state = AsyncMock(return_value={})
    service._equip_local_unlocked = AsyncMock(side_effect=asyncio.CancelledError)

    async def restore(*args):
        if failure == "timeout":
            await asyncio.Event().wait()
        if failure == "exception":
            raise OSError("synthetic recovery failure")
        return False

    service._restore_exact_state = restore
    monkeypatch.setattr(exact_flow_module, "_CANCEL_ROLLBACK_TIMEOUT_SECONDS", 0.01)
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(service.equip_with_recovery("Alpha#0100", _loadout()), 1)

    assert "recovery incomplete" in caplog.text
    async with service._equip_lock:
        pass


@pytest.mark.asyncio
async def test_partial_batch_equip_stops_mods_and_rolls_back() -> None:
    """批量装备失败时**一个模组都不写**，并且整条回滚。

    这里钉的是 `_insert_armor_mod`（真写入）而不是 `_prepare_mod_operations`：
    后者从 2026-10-03 起在**搬运之前**多跑一趟只读预检（"注定失败就别写"），装备阶段失败时
    它当然已经被调用过了 —— 但那时它一分写入都没做。
    """
    service = _equipment_service()
    service._resolver.resolve_player = AsyncMock(return_value={
        "membership_id": "11", "membership_type": 3,
    })
    service._resolver.get_profile = AsyncMock(return_value={
        "characters": {"data": {"22": {"classType": 1}}},
    })
    service._transfer.transfer_item = AsyncMock(return_value=SimpleNamespace(success=True))
    service._transfer.equip_items = AsyncMock(return_value={"success": False})
    service._prepare_mod_operations = AsyncMock()
    service._insert_armor_mod = AsyncMock()
    service._apply_subclass_config = AsyncMock()
    service._capture_recovery_state = AsyncMock(return_value={})
    service._restore_exact_state = AsyncMock(return_value=True)

    result = await service.equip_with_recovery("Alpha#0100", _loadout())

    assert not result.success
    service._insert_armor_mod.assert_not_awaited()
    service._apply_subclass_config.assert_not_awaited()
    service._restore_exact_state.assert_awaited_once()


@pytest.mark.asyncio
async def test_build_candidate_is_player_bound_and_tamper_protected() -> None:
    service = _build_service()
    _, build = _exact_contract()
    service._candidates.register(build, "Alpha#0100")
    service._inventory.get_armor_snapshot = AsyncMock()
    service._equipment.equip_with_recovery = AsyncMock()

    other_player = await service.equip_build("Beta#0200", build, "hunter")
    assert other_player["code"] == "unknown_execution_id"

    tampered = build.model_copy(deep=True)
    tampered.items[0].item_instance_id = "different-instance"
    changed = await service.equip_build("Alpha#0100", tampered, "hunter")
    assert changed["code"] == "canonical_build_mismatch"
    service._inventory.get_armor_snapshot.assert_not_awaited()
    service._equipment.equip_with_recovery.assert_not_awaited()


@pytest.mark.asyncio
async def test_build_candidate_rejects_invalid_inventory_contracts() -> None:
    incomplete_service = _build_service()
    _, incomplete_build = _exact_contract()
    incomplete_build.execution_id = "candidate-incomplete"
    incomplete_build.items.pop()
    incomplete_service._candidates.register(
        incomplete_build, "Alpha#0100"
    )
    incomplete_service._inventory.get_armor_snapshot = AsyncMock()
    incomplete_service._equipment.equip_with_recovery = AsyncMock()

    incomplete = await incomplete_service.equip_build(
        "Alpha#0100", incomplete_build, "hunter"
    )
    assert incomplete["code"] == "invalid_item_count"
    incomplete_service._inventory.get_armor_snapshot.assert_not_awaited()
    incomplete_service._equipment.equip_with_recovery.assert_not_awaited()

    duplicate_service = _build_service()
    _, duplicate_build = _exact_contract()
    duplicate_build.execution_id = "candidate-duplicate"
    duplicate_build.items[1].item_instance_id = (
        duplicate_build.items[0].item_instance_id
    )
    duplicate_service._candidates.register(
        duplicate_build, "Alpha#0100"
    )
    duplicate_service._inventory.get_armor_snapshot = AsyncMock()
    duplicate_service._equipment.equip_with_recovery = AsyncMock()

    duplicate = await duplicate_service.equip_build(
        "Alpha#0100", duplicate_build, "hunter"
    )
    assert duplicate["code"] == "invalid_exact_items"
    duplicate_service._inventory.get_armor_snapshot.assert_not_awaited()

    slot_service = _build_service()
    _, duplicate_slot_build = _exact_contract()
    duplicate_slot_build.execution_id = "candidate-duplicate-slot"
    duplicate_slot_build.items[1].slot = duplicate_slot_build.items[0].slot
    slot_service._candidates.register(
        duplicate_slot_build, "Alpha#0100"
    )
    slot_service._inventory.get_armor_snapshot = AsyncMock()
    slot_service._equipment.equip_with_recovery = AsyncMock()

    duplicate_slot = await slot_service.equip_build(
        "Alpha#0100", duplicate_slot_build, "hunter"
    )
    assert duplicate_slot["code"] == "invalid_exact_items"
    slot_service._inventory.get_armor_snapshot.assert_not_awaited()

    service = _build_service()
    snapshot, build = _exact_contract()
    service._candidates.register(build, "Alpha#0100")
    snapshot.helmets[0].energy_capacity = 9
    service._inventory.get_armor_snapshot = AsyncMock(return_value=snapshot)
    service._equipment.equip_with_recovery = AsyncMock()

    result = await service.equip_build("Alpha#0100", build, "hunter")

    assert result["code"] == "stale_inventory_snapshot"
    service._equipment.equip_with_recovery.assert_not_awaited()

    missing_service = _build_service()
    missing_snapshot, missing_build = _exact_contract()
    missing_build.execution_id = "candidate-missing"
    missing_build.items[0].item_instance_id = "missing-instance"
    missing_service._candidates.register(missing_build, "Alpha#0100")
    missing_service._inventory.get_armor_snapshot = AsyncMock(
        return_value=missing_snapshot
    )
    missing_service._equipment.equip_with_recovery = AsyncMock()

    missing = await missing_service.equip_build(
        "Alpha#0100", missing_build, "hunter"
    )
    assert missing["code"] == "exact_item_missing"
    missing_service._equipment.equip_with_recovery.assert_not_awaited()

    mod_service = _build_service()
    _, invalid_mod_build = _exact_contract()
    invalid_mod_build.execution_id = "candidate-invalid-mod"
    invalid_mod_build.items[0].mods = [0]
    mod_service._candidates.register(invalid_mod_build, "Alpha#0100")
    mod_service._inventory.get_armor_snapshot = AsyncMock()
    mod_service._equipment.equip_with_recovery = AsyncMock()

    invalid_mod = await mod_service.equip_build(
        "Alpha#0100", invalid_mod_build, "hunter"
    )
    assert invalid_mod["code"] == "invalid_mod_hash"
    mod_service._inventory.get_armor_snapshot.assert_not_awaited()
    mod_service._equipment.equip_with_recovery.assert_not_awaited()


@pytest.mark.asyncio
async def test_build_candidate_is_one_time_and_score_path_is_disabled() -> None:
    service = _build_service()
    snapshot, build = _exact_contract()
    service._candidates.register(build, "Alpha#0100")
    service._inventory.get_armor_snapshot = AsyncMock(return_value=snapshot)
    service._equipment.equip_with_recovery = AsyncMock(return_value=LoadoutOperationResult(
        success=True,
        loadout_name="Exact",
        message="OK",
    ))

    first = await service.equip_build("Alpha#0100", build, "hunter")
    replay = await service.equip_build("Alpha#0100", build, "hunter")
    score = await service.equip_by_score(
        "Alpha#0100",
        BuildRequest(character_class="hunter"),
        "hunter",
        950.0,
    )

    assert first["success"] is True
    assert replay["code"] == "unknown_execution_id"
    assert score["code"] == "exact_build_required"


@pytest.mark.asyncio
async def test_build_candidate_expires(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    now = 1_000.0
    # 候选暂存的时钟与 TTL 都在 build_candidates 里（单一出处），换钟得打到那一边。
    monkeypatch.setattr(build_candidates_module.time, "monotonic", lambda: now)
    monkeypatch.setattr(build_candidates_module, "TTL_SECONDS", 10)
    service = _build_service()
    _, build = _exact_contract()
    service._candidates.register(build, "Alpha#0100")
    now = 1_010.0
    service._inventory.get_armor_snapshot = AsyncMock()

    result = await service.equip_build("Alpha#0100", build, "hunter")

    assert result["code"] == "expired_execution_id"
    service._inventory.get_armor_snapshot.assert_not_awaited()
