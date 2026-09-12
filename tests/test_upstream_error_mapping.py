"""上游 HTTP 错误必须变成领域错误，不能裸抛给 MCP 客户端（D1 回归）。

真机复现过两处：`pgcr` 传「数字但不存在」的活动 ID、`clan_leaderboards` 传不存在的
group_id —— 客户端拿到的是 `Error executing tool …: Notfound: (http_status: 404 …)`，
`isError=true`、没有 `ok`/`error.code`，因此分不清「这个 ID 查不到」和「服务坏了」。

这里钉三件事：
1. 404 → `UpstreamNotFoundError`（码 `upstream_not_found_error`），消息说明是「查不到」；
2. 503 / SystemDisabled → `BungieServiceUnavailableError`（码 `bungie_service_unavailable_error`）；
3. 其它 4xx → `APIError`（码 `a_p_i_error`），消息带 HTTP 状态且不误导成账号问题。
"""

from __future__ import annotations

import json
from http import HTTPStatus
from types import SimpleNamespace
from unittest.mock import AsyncMock

import aiobungie
import pytest

from destiny_mcp.bungie_client import _raise_bungie_error
from destiny_mcp.exceptions import (
    APIError,
    BungieServiceUnavailableError,
    DestinyMCPError,
    UpstreamNotFoundError,
)
from destiny_mcp.tools.assistants import activity_assistant

PGCR_NOT_FOUND = aiobungie.error.NotFound(
    error_code=1653,
    throttle_seconds=0,
    url="https://stats.bungie.net/Platform/Destiny2/Stats/PostGameCarnageReport/9999999999999/",
    body={"ErrorCode": 1653, "Message": "The activity you were looking for was not found."},
    headers=None,
    message="The activity you were looking for was not found.",
    error_status="DestinyPGCRNotFound",
    message_data={},
)


def test_upstream_404_becomes_a_domain_error():
    with pytest.raises(UpstreamNotFoundError) as info:
        _raise_bungie_error(PGCR_NOT_FOUND, "读取活动结算报告")

    message = str(info.value)
    assert "HTTP 404" in message
    assert "读取活动结算报告" in message
    assert "The activity you were looking for was not found." in message
    assert "不是服务故障" in message
    assert isinstance(info.value, DestinyMCPError)


def test_upstream_503_still_maps_to_service_unavailable():
    unavailable = aiobungie.HTTPError("Bungie is down", HTTPStatus.SERVICE_UNAVAILABLE)

    with pytest.raises(BungieServiceUnavailableError):
        _raise_bungie_error(unavailable, "读取活动历史")


def test_other_4xx_becomes_api_error_with_the_status():
    bad_request = aiobungie.HTTPError("Bad Request", HTTPStatus.BAD_REQUEST)

    with pytest.raises(APIError) as info:
        _raise_bungie_error(bad_request, "读取排行榜")

    assert "HTTP 400" in str(info.value)
    assert "账号问题" in str(info.value)  # 明说不是账号问题，避免把调用方引到账号上


def test_not_found_is_not_confused_with_api_error_codes():
    """两个码必须分得清：调用方要靠码决定「改 ID」还是「稍后重试」。"""
    assert type(UpstreamNotFoundError("x")).__name__ != APIError("x").__class__.__name__
    assert not isinstance(UpstreamNotFoundError("x"), BungieServiceUnavailableError)


@pytest.mark.parametrize(
    ("intent", "kwargs"),
    [
        ("pgcr", {"activity_id": "9999999999999"}),
        ("history", {}),
    ],
)
async def test_tool_layer_returns_an_envelope_when_upstream_says_not_found(intent, kwargs):
    """工具层拿到的必须是信封（ok=false + code），而不是原始异常。"""
    service = SimpleNamespace(
        get_pgcr=AsyncMock(side_effect=UpstreamNotFoundError("读取活动结算报告", "活动不存在。")),
        get_activity_history=AsyncMock(
            side_effect=UpstreamNotFoundError("读取活动历史", "该账号没有活动记录。")
        ),
    )
    ctx = SimpleNamespace(request_context=SimpleNamespace(lifespan_context={"activity_svc": service}))

    response = await activity_assistant(intent=intent, ctx=ctx, **kwargs)

    assert response["ok"] is False
    assert response["error"]["code"] == "upstream_not_found_error"
    assert response["error"]["recoverable"] is True


def test_no_call_site_re_raises_a_raw_upstream_error():
    """结构性回归：不允许再出现「helper + raise」这种半包写法。"""
    import pathlib
    import re

    source = pathlib.Path("destiny_mcp/bungie_client.py").read_text(encoding="utf-8")
    half_wrapped = re.findall(
        r"except aiobungie\.HTTPError as exc:\s*\n\s*_raise_bungie_unavailable\([^)]*\)\s*\n\s*raise\b",
        source,
    )
    assert half_wrapped == [], (
        "这些位置只挡了 503，其它 4xx 会裸抛；应改用 _raise_bungie_error：\n"
        + "\n".join(half_wrapped)
    )

# ── D2：模糊找人上游失效时，不能说成「没这个人」 ──────────────────────────


async def test_fuzzy_player_search_reports_upstream_failure_instead_of_empty():
    from destiny_mcp.services.player_service import PlayerService

    async def boom(_prefix: str, page: int = 0) -> dict:
        raise aiobungie.HTTPError("SearchGlobalName 500", HTTPStatus.INTERNAL_SERVER_ERROR)

    service = PlayerService(  # type: ignore[arg-type]
        SimpleNamespace(search_users=boom), SimpleNamespace(), SimpleNamespace()
    )

    with pytest.raises(APIError) as info:
        await service.find_players("husky")

    message = str(info.value)
    assert "用户搜索接口" in message and "500" in message
    assert "完整 Bungie 名" in message


async def test_fuzzy_player_search_tool_returns_envelope_with_next_action():
    service = SimpleNamespace(
        find_players=AsyncMock(
            side_effect=APIError("模糊搜索玩家", "Bungie 的 User/SearchUsers 接口不可用（HTTP 405）。")
        )
    )
    from destiny_mcp.tools.assistants import player_assistant

    ctx = SimpleNamespace(request_context=SimpleNamespace(lifespan_context={"player_svc": service}))
    response = await player_assistant(intent="find", name_prefix="husky", ctx=ctx)

    assert response["ok"] is False
    assert response["error"]["code"] == "a_p_i_error"
    assert response["next_actions"], "必须给出下一步（改用完整名精确查找）"
    assert "search" in json.dumps(response["next_actions"], ensure_ascii=False)


async def test_fuzzy_player_search_genuinely_empty_still_succeeds_with_warning():
    """真·空结果（上游正常但没候选）仍是 ok=true，但要提醒别当成「不存在」。"""
    from destiny_mcp.tools.assistants import player_assistant

    service = SimpleNamespace(find_players=AsyncMock(return_value={
        "players": [], "page": 0, "has_more": False, "candidate_count": 0,
    }))
    ctx = SimpleNamespace(request_context=SimpleNamespace(lifespan_context={"player_svc": service}))
    response = await player_assistant(intent="find", name_prefix="zzz", ctx=ctx)

    assert response["ok"] is True
    assert response["data"]["players"] == []
    assert any("没匹配到" in w or "不存在" in w for w in response["warnings"])

# ── worker：子进程起不来时必须是干净错误，且 `-m` 有可用入口 ──────────────


async def test_build_worker_failure_becomes_a_clean_validation_error(monkeypatch):
    """anyio worker 起不来时不能裸抛 BrokenWorkerProcess（真机：`-m destiny_mcp.server`）。"""
    from anyio._core._exceptions import BrokenWorkerProcess

    from destiny_mcp.exceptions import BuildValidationError
    from destiny_mcp.services.build_compute import BuildCompute
    from destiny_mcp.services import build_compute as module

    async def broken(*_args, **_kwargs):
        raise BrokenWorkerProcess("Error during worker process initialization")

    monkeypatch.setattr(module.to_process, "run_sync", broken)
    compute = BuildCompute(timeout_seconds=5)

    with pytest.raises(BuildValidationError) as info:
        await compute.run(lambda: 1)

    message = str(info.value)
    assert "worker" in message
    assert "python -m destiny_mcp" in message
    assert "destiny-mcp" in message  # 控制台脚本那条路也点出来


def test_package_main_entry_is_spawn_safe():
    """`python -m destiny_mcp` 的主模块必须只用绝对导入，否则子进程重跑会 ImportError。"""
    import pathlib

    source = pathlib.Path("destiny_mcp/__main__.py").read_text(encoding="utf-8")
    assert "from destiny_mcp.server import main" in source
    assert "if __name__ == \"__main__\":" in source
    assert not any(
        line.startswith(("from .", "import ."))
        for line in source.splitlines()
        if not line.strip().startswith("#")
    ), "带有相对导入的话，worker 子进程 runpy 重跑本文件会失败"

# ── 模糊找人：换到官方现行端点后的形状映射（别再调废弃路由） ─────────────


NEW_SHAPE = {
    "searchResults": [
        {
            "bungieGlobalDisplayName": "Husky",
            "bungieGlobalDisplayNameCode": 210,
            "destinyMemberships": [
                {"membershipId": "4611686018468673478", "membershipType": 3,
                 "crossSaveOverride": 3, "isPublic": False},
            ],
        },
        {
            "bungieGlobalDisplayName": "无账号的人",
            "bungieGlobalDisplayNameCode": 1,
            "destinyMemberships": [],
        },
    ],
    "page": 0,
    "hasMore": True,
}


def test_identity_of_maps_the_current_bungie_search_shape():
    from destiny_mcp.services.player_service import _identity_of

    membership_id, membership_type, display_name = _identity_of(NEW_SHAPE["searchResults"][0])

    assert membership_id == "4611686018468673478"
    assert membership_type == 3
    assert display_name == "Husky#210"  # 名字 + 数字码，才能拿去做 intent=search

    # 没有 Destiny 账号的候选：给空 id，由调用方跳过，而不是崩
    assert _identity_of(NEW_SHAPE["searchResults"][1]) == ("", 0, "无账号的人#1")


def test_identity_of_prefers_the_cross_save_membership():
    from destiny_mcp.services.player_service import _identity_of

    candidate = {
        "bungieGlobalDisplayName": "Multi",
        "bungieGlobalDisplayNameCode": 7,
        "destinyMemberships": [
            {"membershipId": "steam-id", "membershipType": 3, "crossSaveOverride": 1},
            {"membershipId": "xbox-id", "membershipType": 1, "crossSaveOverride": 1},
        ],
    }

    assert _identity_of(candidate)[0] == "xbox-id"


async def test_search_users_calls_the_current_endpoint_not_the_obsolete_one():
    """回归：`User/SearchUsers/` 已被 Bungie 删除（405），必须走 GlobalName 路由。"""
    from destiny_mcp.bungie_client import BungieClient

    calls: list[tuple[str, str, dict]] = []

    class _Rest:
        async def static_request(self, method, path, **kwargs):
            calls.append((method, path, kwargs))
            return NEW_SHAPE

    client = object.__new__(BungieClient)
    client._rest = _Rest()  # type: ignore[attr-defined]  # rest 是只读 property

    result = await client.search_users("husky", page=2)

    assert result is NEW_SHAPE
    method, path, kwargs = calls[0]
    assert method == "POST"
    assert path == "User/Search/GlobalName/2/"
    assert kwargs["json"] == {"displayNamePrefix": "husky"}


async def test_find_players_returns_pagination_and_skips_accountless_candidates():
    from destiny_mcp.services.player_service import PlayerService

    async def search_users(prefix: str, page: int = 0) -> dict:
        return NEW_SHAPE

    class _Resolver:
        async def get_profile(self, membership_id, membership_type, components):
            return {"characters": {"data": {}}, "profileRecords": {"data": {}}}

    service = PlayerService(  # type: ignore[arg-type]
        SimpleNamespace(search_users=search_users), SimpleNamespace(), _Resolver()  # type: ignore[arg-type]
    )

    payload = await service.find_players("husky")

    assert payload["has_more"] is True
    assert payload["page"] == 0
    assert [p["display_name"] for p in payload["players"]] == ["Husky#210"]
