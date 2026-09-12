"""大响应必须能被限流，并且**自证**是否被截断（D4 回归）。

真机实测（修复前）：`inventory get(location="vault")` 1260 件 ≈ 511 KB ≈ 13–15 万 tokens；
`loadout get` 20 套 × 完整模板 ≈ 227 KB。两个 intent 既不认 limit，响应里也没有
total/truncated —— 调用方既没法少要一点、也察觉不到自己只看到一部分。

修复后的约定：
- `inventory_assistant(intent="get"/"list")`：默认 100 件，可传 limit/offset，
  响应带 total_items/returned_items/truncated/next_offset；
- `loadout_assistant(intent="list"/"get")`：默认 5 套，可传 limit/offset，
  响应带 total_loadouts/returned_loadouts/truncated/next_offset；
- 被截断时必须给 warning 说清"这是截断，不是全部"。
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from destiny_mcp.tools.assistants import inventory_assistant, loadout_assistant

INVENTORY_DEFAULT = 100
LOADOUT_DEFAULT = 5


def _inventory_svc(items: int = 250) -> SimpleNamespace:
    async def get_inventory(
        player_name: str,
        location: str,
        *,
        item_type=None,
        armor_slot=None,
        rarity=None,
        limit=None,
        offset=0,
    ) -> dict:
        everything = [{"name": f"物品{i}"} for i in range(items)]
        window = everything[offset : offset + limit] if limit and limit > 0 else everything[offset:]
        truncated = offset + len(window) < items
        return {
            "location": location or "vault",
            "items": window,
            "total_items": items,
            "returned_items": len(window),
            "truncated": truncated,
            "next_offset": (offset + len(window)) if truncated else None,
        }

    return SimpleNamespace(get_inventory=get_inventory)


def _ctx(**services: object) -> SimpleNamespace:
    return SimpleNamespace(request_context=SimpleNamespace(lifespan_context=services))


async def test_inventory_get_is_bounded_by_default_and_says_so() -> None:
    response = await inventory_assistant(
        intent="get", location="vault", ctx=_ctx(inventory_svc=_inventory_svc(), starside_svc=None)
    )

    inventory = response["data"]["inventory"]
    assert response["ok"] is True
    assert inventory["returned_items"] == INVENTORY_DEFAULT
    assert inventory["total_items"] == 250
    assert inventory["truncated"] is True
    assert inventory["next_offset"] == INVENTORY_DEFAULT
    assert any("只返回了" in warning for warning in response["warnings"]), response["warnings"]


async def test_inventory_get_respects_explicit_limit_and_offset() -> None:
    response = await inventory_assistant(
        intent="get",
        location="vault",
        limit=10,
        offset=240,
        ctx=_ctx(inventory_svc=_inventory_svc(), starside_svc=None),
    )

    inventory = response["data"]["inventory"]
    assert inventory["returned_items"] == 10
    assert inventory["truncated"] is False
    assert inventory["next_offset"] is None
    assert response["warnings"] == []


async def test_inventory_get_without_truncation_has_no_warning() -> None:
    response = await inventory_assistant(
        intent="get", location="vault", ctx=_ctx(inventory_svc=_inventory_svc(items=3), starside_svc=None)
    )

    assert response["data"]["inventory"]["truncated"] is False
    assert response["warnings"] == []


async def test_loadout_list_is_bounded_by_default_and_says_so() -> None:
    service = SimpleNamespace(get_loadouts=AsyncMock(return_value={
        "player_name": "Tester#1234",
        "loadouts": [{"id": f"local:{i}"} for i in range(LOADOUT_DEFAULT)],
        "scope": "account_saved_loadouts",
        "loadout_format": "destiny2_build_template_v1",
        "total_loadouts": 20,
        "returned_loadouts": LOADOUT_DEFAULT,
        "truncated": True,
        "next_offset": LOADOUT_DEFAULT,
    }))
    response = await loadout_assistant(intent="list", ctx=_ctx(loadout_svc=service))

    data = response["data"]
    assert data["returned_loadouts"] == LOADOUT_DEFAULT
    assert data["total_loadouts"] == 20
    assert data["truncated"] is True
    assert data["next_offset"] == LOADOUT_DEFAULT
    assert any("只返回了" in warning for warning in response["warnings"])
    # 默认上限必须真的传给服务层（否则"认领了 limit"是假的）
    assert service.get_loadouts.await_args.args[2] == LOADOUT_DEFAULT


async def test_loadout_get_passes_explicit_paging_to_the_service() -> None:
    service = SimpleNamespace(get_loadouts=AsyncMock(return_value={
        "player_name": "Tester#1234", "loadouts": [], "scope": "s",
        "loadout_format": "f", "total_loadouts": 0, "returned_loadouts": 0,
        "truncated": False, "next_offset": None,
    }))
    await loadout_assistant(intent="get", limit=2, offset=4, ctx=_ctx(loadout_svc=service))

    assert service.get_loadouts.await_args.args[2:] == (2, 4)


@pytest.mark.parametrize("intent", ["get", "list"])
async def test_zero_or_negative_limit_falls_back_to_the_default(intent) -> None:
    """项目约定：0/负数 = 没指定（回落到该 intent 的默认上限），不是"全部"。"""
    inventory = _inventory_svc(items=250)
    response = await inventory_assistant(
        intent=intent, limit=0, ctx=_ctx(inventory_svc=inventory, starside_svc=None)
    )
    assert response["data"]["inventory"]["returned_items"] == INVENTORY_DEFAULT
