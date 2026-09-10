"""Regression coverage for execution, cache, startup and request boundaries."""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
import time
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import anyio
import pytest
from pydantic import ValidationError
from mcp import ClientSession

from destiny_mcp import config, server
from destiny_mcp.build.models import Armor, ArmorStats, BuildConstraints, BuildRequest, InventorySnapshot
from destiny_mcp.build.solver import solve
from destiny_mcp.build_contracts import BuildRecipe, CanonicalBuild, ExecutableBuild
from destiny_mcp.exceptions import AuthenticationError, BuildValidationError, ConfigError
from destiny_mcp.models import InventoryItem, Loadout, LoadoutItem
from destiny_mcp.services.account_action_lock import ReentrantAsyncLock, account_action_lock
from destiny_mcp.services.build_compute import BuildCompute
from destiny_mcp.services.build_service import BuildService
from destiny_mcp.services.loadout_service import LoadoutService
from destiny_mcp.services.profile_cache import ProfileCache
from destiny_mcp.services.transfer_service import TransferService
from destiny_mcp.tools.assistants import inventory_assistant, loadout_assistant, subclass_assistant
from destiny_mcp.tools.api_tools import raw_api_call


async def test_move_uses_exact_transfer_recovery(monkeypatch) -> None:
    client = MagicMock()
    client.transfer_item = AsyncMock(side_effect=[
        {"ErrorCode": 1}, {"ErrorCode": 1642, "Message": "Inventory full"}, {"ErrorCode": 1},
    ])
    manifest = MagicMock()
    manifest.search.return_value = [{"itemHash": 42, "name": "Weapon"}]
    resolver = SimpleNamespace(
        resolve_player=AsyncMock(return_value={"membership_id": "player", "membership_type": 3}),
        resolve_character_id=AsyncMock(return_value="warlock-id"),
    )
    item = InventoryItem(
        item_instance_id="instance", item_hash=42, name="Weapon",
        location="hunter", character_id="hunter-id",
    )
    service = TransferService(client, manifest, resolver)
    service._fetch_all_items = AsyncMock(return_value=[item])
    monkeypatch.setattr("destiny_mcp.services.transfer_service._TRANSFER_HOP_DELAY_SECONDS", 0)

    result = await service.move_item("player", "Weapon", "warlock")

    assert not result.success
    manifest.search.assert_called_once_with("Weapon", limit=0)
    assert "rolled back" in result.message
    assert [call.kwargs["character_id"] for call in client.transfer_item.await_args_list] == [
        "hunter-id", "warlock-id", "hunter-id",
    ]
    assert client.transfer_item.await_count == 3


def _profile_cache(lock):
    resolver = SimpleNamespace(
        resolve_player=AsyncMock(return_value={"membership_id": "id", "membership_type": 3}),
        get_profile=AsyncMock(return_value={"location": "vault"}),
    )
    return ProfileCache(resolver, MagicMock(), action_lock=lock), resolver


@pytest.mark.parametrize("cancelled", [False, True])
async def test_cache_invalidates_after_failed_or_cancelled_write(cancelled) -> None:
    lock = ReentrantAsyncLock()
    cache, resolver = _profile_cache(lock)
    assert await cache.get_profile("player") == {"location": "vault"}
    await cache.get_profile("player")
    assert resolver.get_profile.await_count == 1
    failure = asyncio.CancelledError if cancelled else RuntimeError
    with pytest.raises(failure):
        async with lock:
            resolver.get_profile.return_value = {"location": "hunter"}
            raise failure()
    assert await cache.get_profile("player") == {"location": "hunter"}
    assert resolver.get_profile.await_count == 2


async def test_inflight_profile_cannot_repopulate_invalidated_cache() -> None:
    lock = ReentrantAsyncLock()
    cache, resolver = _profile_cache(lock)
    started, release = asyncio.Event(), asyncio.Event()

    async def fetch(*args):
        started.set()
        await release.wait()
        return {"location": "old"}

    resolver.get_profile.side_effect = fetch
    pending = asyncio.create_task(cache.get_profile("player"))
    await started.wait()
    async with lock:
        pass
    release.set()
    await pending
    assert "player" not in cache._cache
    resolver.get_profile.side_effect = None
    resolver.get_profile.return_value = {"location": "new"}
    assert await cache.get_profile("player") == {"location": "new"}


async def test_explicit_fresh_read_bypasses_cache() -> None:
    cache, resolver = _profile_cache(ReentrantAsyncLock())
    await cache.get_profile("player")
    resolver.get_profile.return_value = {"location": "new"}
    assert await cache.get_profile("player", fresh=True) == {"location": "new"}
    assert resolver.get_profile.await_count == 2


async def test_local_loadout_save_delete_refreshes_cached_list(tmp_path) -> None:
    client = MagicMock()
    service = LoadoutService(client, MagicMock(), MagicMock(), tmp_path / "loadouts.json")
    service._fetch_native = AsyncMock(return_value=[])
    assert (await service.get_loadouts("player")).loadouts == []
    loadout = Loadout(id="test", name="Test", character="hunter", items=[])
    service._save_local([loadout])
    assert [item.id for item in (await service.get_loadouts("player")).loadouts] == ["test"]
    await service.delete_loadout("test")
    assert (await service.get_loadouts("player")).loadouts == []
    async with account_action_lock(client):
        pass
    assert service._get_cached("player") is None


async def test_server_factories_do_not_leak_profiles_or_prompts() -> None:
    full = server.create_server("full")
    normal = server.create_server("normal")
    expert = server.create_server("expert")
    second_normal = server.create_server("normal")
    def names(instance):
        return {tool.name for tool in instance._tool_manager.list_tools()}
    assert len(names(normal)) == 8
    assert names(normal) == names(second_normal)
    assert "transfer_item" in names(full)
    assert "transfer_item" not in names(expert)
    assert names(normal) < names(expert) < names(full)
    assert len(await normal.list_prompts()) == len(await full.list_prompts())


async def test_readiness_tracks_lifespan(monkeypatch) -> None:
    @asynccontextmanager
    async def fake_lifespan(instance):
        yield {}

    monkeypatch.setattr(server, "app_lifespan", fake_lifespan)
    instance = server.create_server()
    health = next(route.endpoint for route in instance._custom_starlette_routes if route.path == "/health")
    assert (await health(None)).status_code == 503
    async with instance.settings.lifespan(instance):
        response = await health(None)
        assert response.status_code == 200
        assert json.loads(response.body)["status"] == "ok"
    assert (await health(None)).status_code == 503


@pytest.mark.parametrize("failure_stage", ["authentication", "manifest"])
async def test_failed_startup_closes_dependencies(monkeypatch, failure_stage) -> None:
    client, manifest = MagicMock(), MagicMock()
    client.start = AsyncMock()
    client.close = AsyncMock()
    manifest.ensure_loaded = AsyncMock()
    if failure_stage == "authentication":
        client.start.side_effect = AuthenticationError("test failure")
    else:
        manifest.ensure_loaded.side_effect = RuntimeError("test failure")
    monkeypatch.setattr(server, "BungieClient", lambda: client)
    monkeypatch.setattr(server, "ManifestManager", lambda: manifest)
    with pytest.raises((AuthenticationError, RuntimeError)):
        async with server.app_lifespan(server.create_server()):
            pytest.fail("startup must not yield a usable context")
    client.close.assert_awaited_once()
    manifest.close.assert_called_once()


def test_credentials_validation_is_explicit_and_redacted(monkeypatch) -> None:
    monkeypatch.setattr(config, "BUNGIE_CLIENT_ID", "not-a-valid-client")
    with pytest.raises(ConfigError) as exc:
        config.validate_credentials()
    assert "not-a-valid-client" not in str(exc.value)


def _executable_payload():
    return dict(
        class_type="hunter", snapshot_version="snapshot", execution_id="candidate",
        items=[LoadoutItem(
            item_instance_id=str(index), item_hash=index, name="Armor", slot=slot,
        ) for index, slot in enumerate(("helmet", "gauntlets", "chest", "legs", "class_item"), 1)],
    )


def test_build_recipe_is_not_an_executable_plan() -> None:
    recipe = BuildRecipe(class_type="hunter", exotic_hash=42)
    assert "execution_id" not in recipe.model_dump()
    with pytest.raises(ValidationError):
        ExecutableBuild.model_validate(recipe.model_dump())
    payload = _executable_payload()
    plan = ExecutableBuild(**payload)
    assert CanonicalBuild(**payload).model_dump() == plan.model_dump()
    payload["items"][0].item_instance_id = "2"
    with pytest.raises(ValidationError, match="unique"):
        ExecutableBuild(**payload)


@pytest.mark.parametrize("tool, kwargs", [
    (inventory_assistant, {"intent": "move"}),
    (inventory_assistant, {"intent": "move", "item_name": "Test", "destination": "vault", "equip": True}),
    (inventory_assistant, {"intent": "transfer", "item_instance_id": "1"}),
    (inventory_assistant, {"intent": "equip", "character": "hunter"}),
    (inventory_assistant, {"intent": "equip_items", "character": "hunter", "item_instance_ids": ["1", "1"]}),
    (inventory_assistant, {"intent": "lock", "item_instance_id": " "}),
    (loadout_assistant, {"intent": "save", "name": "Test"}),
    (loadout_assistant, {"intent": "delete"}),
    (loadout_assistant, {"intent": "clear_official", "character": "hunter", "slot_number": 21}),
    (loadout_assistant, {"intent": "update_official_identifiers", "character": "hunter"}),
    (subclass_assistant, {"intent": "modify", "character": "hunter"}),
    (subclass_assistant, {"intent": "equip_artifact_mod", "character": "hunter", "artifact_mod_hash": -1}),
])
async def test_invalid_write_requests_never_enter_services(tool, kwargs) -> None:
    result = await tool(**kwargs, confirmed=True, ctx=None)
    assert result["ok"] is False
    assert result["error"]["code"] == "invalid_arguments"


async def test_assistant_schemas_publish_intent_enums() -> None:
    for tool in await server.create_server().list_tools():
        assert tool.inputSchema["properties"]["intent"]["enum"]


async def test_compute_uses_worker_and_keeps_loop_responsive() -> None:
    compute = BuildCompute(timeout_seconds=5)
    assert await compute.run(os.getpid) != os.getpid()
    pending = asyncio.create_task(compute.run(time.sleep, 0.15))
    await asyncio.sleep(0.02)
    assert not pending.done()
    await pending
    result = await compute.run(solve, InventorySnapshot(), BuildConstraints())
    assert result.sets == []


async def test_compute_timeout_terminates_worker_and_releases_capacity() -> None:
    compute = BuildCompute(timeout_seconds=2)
    previous_pid = await compute.run(os.getpid)
    compute._timeout_seconds = 0.05
    with pytest.raises(BuildValidationError, match="timed out"):
        await compute.run(time.sleep, 10)
    compute._timeout_seconds = 5
    assert await compute.run(os.getpid) != previous_pid


async def test_compute_cancellation_terminates_worker() -> None:
    compute = BuildCompute(timeout_seconds=5)
    previous_pid = await compute.run(os.getpid)
    with anyio.move_on_after(0.05) as scope:
        await compute.run(time.sleep, 10)
    assert scope.cancel_called
    assert await compute.run(os.getpid) != previous_pid


@pytest.mark.parametrize("character", ["hunter", "猎人"])
async def test_real_worker_returns_executable_build_and_farm_analysis(character) -> None:
    snapshot = InventorySnapshot(**{
        slot: [Armor(
            item_instance_id=str(index), item_hash=index, name="Armor", slot=slot,
            stats=ArmorStats(weapons=10), energy_capacity=10,
        )]
        for index, slot in enumerate(("helmets", "gauntlets", "chests", "legs", "class_items"), 1)
    })
    service = BuildService(MagicMock(), MagicMock(), MagicMock())
    service._inventory.get_armor_snapshot = AsyncMock(return_value=snapshot)
    results = await service.find_build("player", BuildRequest(character_class=character))
    assert results
    candidate = results[0].canonical_build
    assert candidate.class_type == "hunter"
    assert len(candidate.items) == 5
    assert candidate.execution_id in service._build_candidates
    ExecutableBuild.model_validate(candidate.model_dump())
    analysis = await service.infer_required_armor(
        "player", BuildRequest(character_class=character), baseline="inventory",
        replacement_slot="helmet", max_replacements=1,
    )
    assert analysis is not None


def test_import_without_credentials_does_not_create_runtime_directories(tmp_path) -> None:
    env = os.environ.copy()
    env.update({
        "BUNGIE_API_KEY": "", "BUNGIE_CLIENT_ID": "", "BUNGIE_CLIENT_SECRET": "",
        "PYTHON_DOTENV_DISABLED": "1",
        "DESTINY_TOKEN_PATH": str(tmp_path / "tokens"),
        "DESTINY_MANIFEST_PATH": str(tmp_path / "manifest"),
    })
    result = subprocess.run(
        [sys.executable, "-c", "from destiny_mcp.server import create_server; assert len(create_server()._tool_manager.list_tools()) == 8"],
        env=env, capture_output=True, text=True, timeout=10,
    )
    assert result.returncode == 0, result.stderr
    assert not (tmp_path / "tokens").exists()
    assert not (tmp_path / "manifest").exists()


async def test_raw_post_uses_shared_lock_and_invalidates_cache() -> None:
    client = MagicMock()
    client.get_access_token = AsyncMock(return_value="dummy")
    client.rest.static_request = AsyncMock(return_value={"ok": True})
    lock = account_action_lock(client)
    cache, resolver = _profile_cache(lock)
    await cache.get_profile("player")
    ctx = SimpleNamespace(request_context=SimpleNamespace(lifespan_context={"bungie": client}))
    async with lock:
        pending = asyncio.create_task(raw_api_call(method="POST", path="Destiny2/Actions/Test/", ctx=ctx))
        await asyncio.sleep(0)
        client.rest.static_request.assert_not_awaited()
    assert await pending == {"ok": True}
    await cache.get_profile("player")
    assert resolver.get_profile.await_count == 2


async def test_mcp_protocol_calls_are_audited(monkeypatch) -> None:
    @asynccontextmanager
    async def fake_lifespan(instance):
        yield {}

    monkeypatch.setattr(server, "app_lifespan", fake_lifespan)
    instance = server.create_server()
    instance._audit = MagicMock()

    @instance.tool()
    async def boundary_probe() -> dict:
        return {"ok": True}

    to_server, server_receive = anyio.create_memory_object_stream(1)
    to_client, client_receive = anyio.create_memory_object_stream(1)
    async with anyio.create_task_group() as group:
        group.start_soon(
            instance._mcp_server.run, server_receive, to_client,
            instance._mcp_server.create_initialization_options(),
        )
        async with ClientSession(client_receive, to_server) as session:
            await session.initialize()
            result = await session.call_tool("boundary_probe", {})
            assert not result.isError
        group.cancel_scope.cancel()
    instance._audit.log.assert_called_once()
    assert instance._audit.log.call_args.args[0] == "boundary_probe"
