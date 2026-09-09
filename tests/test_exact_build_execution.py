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
    BuildCandidate,
    BuildRecommendation,
    BuildRequest,
    BuildResult,
    InventorySnapshot,
)
from destiny_mcp.build_import.models import CanonicalBuild
from destiny_mcp.exceptions import BuildValidationError
from destiny_mcp.models import (
    ArmorStats,
    Loadout,
    LoadoutItem,
    LoadoutOperationResult,
)
from destiny_mcp.services import build_service as build_service_module
from destiny_mcp.services import loadout_equipment_service as equipment_module
from destiny_mcp.services.build_service import BuildService, _snapshot_version
from destiny_mcp.services.loadout_equipment_service import LoadoutEquipmentService
from destiny_mcp.server import build_optimizer
from destiny_mcp.tools import _build_confirmation
from destiny_mcp.tools.assistants import build_assistant
from destiny_mcp.tools.build_tools import recommend_build as legacy_recommend_build


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
        snapshot_version=_snapshot_version(snapshot),
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
async def test_full_recommendation_preserves_exact_build_contract() -> None:
    snapshot, canonical = _exact_contract()
    armor = [
        snapshot.helmets[0],
        snapshot.gauntlets[0],
        snapshot.chests[0],
        snapshot.legs[0],
        snapshot.class_items[0],
    ]
    result = BuildResult(
        score=950,
        completion_rate=1,
        build=BuildCandidate(items=armor),
        canonical_build=canonical,
    )
    build_service = SimpleNamespace(
        recommend_build=AsyncMock(
            return_value=BuildRecommendation(results=[result])
        )
    )

    response = await legacy_recommend_build(
        player_name="Alpha#0100",
        character="hunter",
        include_subclass_fragment=True,
        priority_stats=["melee", "weapons"],
        ctx=_tool_context({"build_svc": build_service}),
    )

    expected = canonical.model_dump(mode="json")
    request = build_service.recommend_build.await_args.args[1]
    assert request.include_subclass_fragment is True
    assert request.priority_stats == ["melee", "weapons"]
    assert response["data"]["builds"][0]["canonical_build"] == expected
    assert response["data"]["builds"][0]["armor"][0]["item_instance_id"]
    assert response["candidates"][0]["canonical_build"] == expected
    assert response["next_actions"][0]["arguments"] == {
        "character": "hunter",
        "canonical_build": expected,
    }
    assert "canonical_build" in build_optimizer()


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
async def test_exact_equipment_verifies_success() -> None:
    service = _equipment_service()
    service._capture_recovery_state = AsyncMock(return_value={})
    service._equip_local_unlocked = AsyncMock(return_value=LoadoutOperationResult(
        success=True,
        loadout_name="Exact",
        message="applied",
    ))
    service._verify_loadout = AsyncMock(return_value=True)
    service._restore_exact_state = AsyncMock()

    result = await service.equip_exact("Alpha#0100", _loadout())

    assert result.success is True
    assert result.steps[-1].action == "verify"
    service._restore_exact_state.assert_not_awaited()


@pytest.mark.asyncio
async def test_exact_equipment_rolls_back_failed_verification() -> None:
    service = _equipment_service()
    recovery = {"captured": True}
    service._capture_recovery_state = AsyncMock(return_value=recovery)
    service._equip_local_unlocked = AsyncMock(return_value=LoadoutOperationResult(
        success=True,
        loadout_name="Exact",
        message="applied",
    ))
    service._verify_loadout = AsyncMock(return_value=False)
    service._restore_exact_state = AsyncMock(return_value=True)

    result = await service.equip_exact("Alpha#0100", _loadout())

    assert result.success is False
    assert "已恢复" in result.message
    service._restore_exact_state.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("cancel_mode", ["asyncio", "mcp_scope"])
@pytest.mark.parametrize("phase", ["apply", "verify", "rollback"])
async def test_cancelled_equipment_recovers_before_releasing_account_lock(
    cancel_mode: str, phase: str,
) -> None:
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
        return LoadoutOperationResult(success=True)

    async def verify(*args):
        if phase == "verify":
            interrupted.set()
            await asyncio.Event().wait()
        return False

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
    service._verify_loadout = verify
    service._restore_exact_state = AsyncMock(side_effect=restore)

    async def run():
        with anyio.CancelScope() as scope:
            scopes.append(scope)
            await service.equip_exact("Alpha#0100", _loadout())

    async def subsequent_write():
        async with service._equip_lock:
            assert state["equipment"] == "before"
            next_write.set()

    task = asyncio.create_task(run())
    other = None
    try:
        await asyncio.wait_for(interrupted.wait(), 1)
        if cancel_mode == "asyncio":
            task.cancel()
        else:
            scopes[0].cancel()
        await asyncio.wait_for(restoring.wait(), 1)
        other = asyncio.create_task(subsequent_write())
        await asyncio.sleep(0)
        assert not next_write.is_set()
        release_restore.set()
        if cancel_mode == "asyncio":
            with pytest.raises(asyncio.CancelledError):
                await asyncio.wait_for(task, 1)
        else:
            await asyncio.wait_for(task, 1)
        await asyncio.wait_for(other, 1)
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
    monkeypatch.setattr(equipment_module, "_CANCEL_ROLLBACK_TIMEOUT_SECONDS", 0.01)
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(service.equip_exact("Alpha#0100", _loadout()), 1)

    assert "recovery incomplete" in caplog.text
    async with service._equip_lock:
        pass


@pytest.mark.asyncio
async def test_partial_batch_equip_stops_mods_and_rolls_back() -> None:
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
    service._apply_subclass_config = AsyncMock()
    service._capture_recovery_state = AsyncMock(return_value={})
    service._restore_exact_state = AsyncMock(return_value=True)

    result = await service.equip_exact("Alpha#0100", _loadout())

    assert not result.success
    service._prepare_mod_operations.assert_not_awaited()
    service._apply_subclass_config.assert_not_awaited()
    service._restore_exact_state.assert_awaited_once()


@pytest.mark.asyncio
async def test_build_candidate_is_player_bound_and_tamper_protected() -> None:
    service = _build_service()
    _, build = _exact_contract()
    service._register_build_candidate(build, "Alpha#0100")
    service._inventory.get_armor_snapshot = AsyncMock()
    service._equipment.equip_exact = AsyncMock()

    other_player = await service.equip_build("Beta#0200", build, "hunter")
    assert other_player["code"] == "unknown_execution_id"

    tampered = build.model_copy(deep=True)
    tampered.items[0].item_instance_id = "different-instance"
    changed = await service.equip_build("Alpha#0100", tampered, "hunter")
    assert changed["code"] == "canonical_build_mismatch"
    service._inventory.get_armor_snapshot.assert_not_awaited()
    service._equipment.equip_exact.assert_not_awaited()


@pytest.mark.asyncio
async def test_build_candidate_rejects_invalid_inventory_contracts() -> None:
    incomplete_service = _build_service()
    _, incomplete_build = _exact_contract()
    incomplete_build.execution_id = "candidate-incomplete"
    incomplete_build.items.pop()
    incomplete_service._register_build_candidate(
        incomplete_build, "Alpha#0100"
    )
    incomplete_service._inventory.get_armor_snapshot = AsyncMock()
    incomplete_service._equipment.equip_exact = AsyncMock()

    incomplete = await incomplete_service.equip_build(
        "Alpha#0100", incomplete_build, "hunter"
    )
    assert incomplete["code"] == "invalid_item_count"
    incomplete_service._inventory.get_armor_snapshot.assert_not_awaited()
    incomplete_service._equipment.equip_exact.assert_not_awaited()

    duplicate_service = _build_service()
    _, duplicate_build = _exact_contract()
    duplicate_build.execution_id = "candidate-duplicate"
    duplicate_build.items[1].item_instance_id = (
        duplicate_build.items[0].item_instance_id
    )
    duplicate_service._register_build_candidate(
        duplicate_build, "Alpha#0100"
    )
    duplicate_service._inventory.get_armor_snapshot = AsyncMock()
    duplicate_service._equipment.equip_exact = AsyncMock()

    duplicate = await duplicate_service.equip_build(
        "Alpha#0100", duplicate_build, "hunter"
    )
    assert duplicate["code"] == "invalid_exact_items"
    duplicate_service._inventory.get_armor_snapshot.assert_not_awaited()

    slot_service = _build_service()
    _, duplicate_slot_build = _exact_contract()
    duplicate_slot_build.execution_id = "candidate-duplicate-slot"
    duplicate_slot_build.items[1].slot = duplicate_slot_build.items[0].slot
    slot_service._register_build_candidate(
        duplicate_slot_build, "Alpha#0100"
    )
    slot_service._inventory.get_armor_snapshot = AsyncMock()
    slot_service._equipment.equip_exact = AsyncMock()

    duplicate_slot = await slot_service.equip_build(
        "Alpha#0100", duplicate_slot_build, "hunter"
    )
    assert duplicate_slot["code"] == "invalid_exact_items"
    slot_service._inventory.get_armor_snapshot.assert_not_awaited()

    service = _build_service()
    snapshot, build = _exact_contract()
    service._register_build_candidate(build, "Alpha#0100")
    snapshot.helmets[0].energy_capacity = 9
    service._inventory.get_armor_snapshot = AsyncMock(return_value=snapshot)
    service._equipment.equip_exact = AsyncMock()

    result = await service.equip_build("Alpha#0100", build, "hunter")

    assert result["code"] == "stale_inventory_snapshot"
    service._equipment.equip_exact.assert_not_awaited()

    missing_service = _build_service()
    missing_snapshot, missing_build = _exact_contract()
    missing_build.execution_id = "candidate-missing"
    missing_build.items[0].item_instance_id = "missing-instance"
    missing_service._register_build_candidate(missing_build, "Alpha#0100")
    missing_service._inventory.get_armor_snapshot = AsyncMock(
        return_value=missing_snapshot
    )
    missing_service._equipment.equip_exact = AsyncMock()

    missing = await missing_service.equip_build(
        "Alpha#0100", missing_build, "hunter"
    )
    assert missing["code"] == "exact_item_missing"
    missing_service._equipment.equip_exact.assert_not_awaited()

    mod_service = _build_service()
    _, invalid_mod_build = _exact_contract()
    invalid_mod_build.execution_id = "candidate-invalid-mod"
    invalid_mod_build.items[0].mods = [0]
    mod_service._register_build_candidate(invalid_mod_build, "Alpha#0100")
    mod_service._inventory.get_armor_snapshot = AsyncMock()
    mod_service._equipment.equip_exact = AsyncMock()

    invalid_mod = await mod_service.equip_build(
        "Alpha#0100", invalid_mod_build, "hunter"
    )
    assert invalid_mod["code"] == "invalid_mod_hash"
    mod_service._inventory.get_armor_snapshot.assert_not_awaited()
    mod_service._equipment.equip_exact.assert_not_awaited()


@pytest.mark.asyncio
async def test_build_candidate_is_one_time_and_score_path_is_disabled() -> None:
    service = _build_service()
    snapshot, build = _exact_contract()
    service._register_build_candidate(build, "Alpha#0100")
    service._inventory.get_armor_snapshot = AsyncMock(return_value=snapshot)
    service._equipment.equip_exact = AsyncMock(return_value=LoadoutOperationResult(
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
    monkeypatch.setattr(build_service_module.time, "monotonic", lambda: now)
    monkeypatch.setattr(build_service_module, "_BUILD_CANDIDATE_TTL_SECONDS", 10)
    service = _build_service()
    _, build = _exact_contract()
    service._register_build_candidate(build, "Alpha#0100")
    now = 1_010.0
    service._inventory.get_armor_snapshot = AsyncMock()

    result = await service.equip_build("Alpha#0100", build, "hunter")

    assert result["code"] == "expired_execution_id"
    service._inventory.get_armor_snapshot.assert_not_awaited()
