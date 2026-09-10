"""Account-write tools must expose domain failures at the top level."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from destiny_mcp.models import ItemCandidate, LoadoutOperationResult, MoveItemResult
from destiny_mcp.tools._requests import WRITE_INTENTS
from destiny_mcp.tools.assistants import (
    SELF_GUARDED_WRITE_INTENTS,
    inventory_assistant,
    loadout_assistant,
    subclass_assistant,
)


_ACTIONS = [
    (inventory_assistant, "transfer_svc", "move", "move_item", {"item_name": "Test", "destination": "hunter"}),
    (inventory_assistant, "transfer_svc", "transfer", "transfer_item", {"item_instance_id": "1", "to_character": "hunter"}),
    (inventory_assistant, "transfer_svc", "equip", "equip_item", {"item_instance_id": "1", "character": "hunter"}),
    (inventory_assistant, "transfer_svc", "equip_many", "equip_items", {"item_instance_ids": ["1"], "character": "hunter"}),
    (inventory_assistant, "transfer_svc", "equip_items", "equip_items", {"item_instance_ids": ["1"], "character": "hunter"}),
    (inventory_assistant, "transfer_svc", "pull_postmaster", "pull_from_postmaster", {"item_instance_id": "1"}),
    (inventory_assistant, "transfer_svc", "lock", "set_item_lock_state", {"item_instance_id": "1"}),
    (inventory_assistant, "transfer_svc", "track_quest", "set_quest_tracked_state", {"item_instance_id": "1"}),
    (inventory_assistant, "transfer_svc", "quest_tracking", "set_quest_tracked_state", {"item_instance_id": "1"}),
    (loadout_assistant, "loadout_svc", "save", "save_loadout", {"name": "Test", "character": "hunter"}),
    (loadout_assistant, "loadout_svc", "delete", "delete_loadout", {"loadout_id": "local:1"}),
    (loadout_assistant, "loadout_svc", "equip_loadout", "equip_loadout", {"loadout_id": "local:1"}),
    (loadout_assistant, "loadout_svc", "snapshot_official", "snapshot_official_loadout", {"character": "hunter"}),
    (loadout_assistant, "loadout_svc", "update_official_identifiers", "update_official_loadout_identifiers", {"character": "hunter", "name_hash": 1}),
    (loadout_assistant, "loadout_svc", "clear_official", "clear_official_loadout", {"character": "hunter"}),
    (subclass_assistant, "subclass_svc", "modify", "modify_subclass", {"character": "hunter", "changes": {"super": "Test"}}),
    (subclass_assistant, "artifact_svc", "equip_artifact_mod", "equip_artifact_mod", {"artifact_mod_hash": 1, "character": "hunter"}),
]


def test_action_cases_cover_every_generically_guarded_write_intent() -> None:
    """写入清单新增 intent 时必须在这里补一条用例，否则测试失败。"""
    covered = {intent for _, _, intent, _, _ in _ACTIONS}

    assert covered == set(WRITE_INTENTS) - SELF_GUARDED_WRITE_INTENTS


@pytest.mark.asyncio
@pytest.mark.parametrize("tool, service_name, intent, method, kwargs", _ACTIONS)
@pytest.mark.parametrize("success", [True, False])
async def test_write_assistants_propagate_service_status(
    tool, service_name, intent, method, kwargs, success,
) -> None:
    payload = {"success": success, "message": "domain result", "steps": []}
    result = LoadoutOperationResult(**payload) if service_name == "loadout_svc" else payload
    action = AsyncMock(return_value=result)
    ctx = SimpleNamespace(request_context=SimpleNamespace(lifespan_context={
        service_name: SimpleNamespace(**{method: action}),
    }))

    confirmation = await tool(intent=intent, confirmed=False, ctx=ctx, **kwargs)
    assert not confirmation["ok"]
    action.assert_not_awaited()

    response = await tool(intent=intent, confirmed=True, ctx=ctx, **kwargs)
    assert response["ok"] is success
    assert response["data"]["result"]["success"] is success
    if not success:
        assert response["error"]["message"] == "domain result"
        assert response["error"]["code"] == f"{intent}_failed"
        assert "summary" not in response
    action.assert_awaited_once()


@pytest.mark.asyncio
async def test_failed_move_preserves_disambiguation_candidates() -> None:
    result = MoveItemResult(
        success=False,
        needs_disambiguation=True,
        message="Choose an item",
        question="Which instance?",
        candidates=[ItemCandidate(item_instance_id="123", name="Test")],
    )
    ctx = SimpleNamespace(request_context=SimpleNamespace(lifespan_context={
        "transfer_svc": SimpleNamespace(move_item=AsyncMock(return_value=result)),
    }))

    response = await inventory_assistant(
        intent="move", item_name="Test", destination="hunter", confirmed=True, ctx=ctx,
    )

    assert not response["ok"]
    assert response["candidates"][0]["item_instance_id"] == "123"
    assert response["data"]["result"]["question"] == "Which instance?"
