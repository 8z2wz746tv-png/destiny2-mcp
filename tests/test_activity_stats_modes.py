"""P3-2 守门：`stats` 的 `mode=` / `period=`（按模式与周期统计）。

真机结论（2026-09-18，只读，证据在 `docs/reference/bungie_api.md`）：

1. `modes=` **只在按角色的端点上生效** —— 账号级 `.../Account/{id}/Stats/` 传 `modes=84`
   与不传的响应一字不差。所以账号级 + 模式必须逐角色取、自己合，payload 标
   `aggregation="computed"`（三个档位没有一个是上游给的）。
2. `periodType` 没有 Season（只有 None/Daily/AllTime/Activity），传 3 会 500 →
   `period="season"/"act"` 如实报 unavailable，**不降级成生涯**、也不去试会 500 的参数。
3. `modes=9`（`ACTIVITY_MODES` 里那个错的 allpvp）会 500 —— 模式数值只从
   `data/activity_modes.MODE_TYPE` 取（模式词与数值的唯一出处）。

这一组数字用的是真机试炼那两个角色的实测值（现存猎人 473/562/155、已删角色 423/508/124），
所以"合并之后等于几"是可核对的，而不是随便编的。
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from destiny_mcp.data import activity_modes
from destiny_mcp.exceptions import InvalidArgumentError
from destiny_mcp.services.activity_service import ActivityService
from destiny_mcp.tools import _stats_branches


PLAYER_NAME = "TestGuardian#1234"
EXISTING_ID = "2305843009679355779"
DELETED_ID = "2305843009571926945"
TRIALS_GROUP = "trials_of_osiris"

# 真机试炼（allTime）实测：现存猎人与一条已删角色
EXISTING = {"kills": 473, "deaths": 562, "assists": 155, "activitiesEntered": 114,
            "activitiesWon": 50, "opponentsDefeated": 900, "score": 350,
            "longestKillSpree": 7, "bestSingleGameKills": 14}
DELETED = {"kills": 423, "deaths": 508, "assists": 124, "activitiesEntered": 101,
           "activitiesWon": 35, "opponentsDefeated": 574, "score": 294,
           "longestKillSpree": 8, "bestSingleGameKills": 12}


def _entry(value: float) -> dict:
    return {"basic": {"value": value, "displayValue": str(value)}}


def _section(values: dict) -> dict:
    return {key: _entry(value) for key, value in values.items()}


def _character(character_id: str, deleted: bool) -> dict:
    return {"characterId": character_id, "deleted": deleted, "results": {}}


@pytest.fixture
def service() -> ActivityService:
    bungie = AsyncMock()
    manifest = MagicMock()
    # 模式中文名走 Manifest（zh 优先）：替身按 modeType 给官方名。
    manifest.get_activity_mode_name.side_effect = lambda mode_type: {
        5: "熔炉竞技场", 19: "铁旗", 69: "多人竞技PvP", 84: "奥斯里斯试炼",
    }.get(mode_type, "")
    resolver = AsyncMock()
    resolver.resolve_player.return_value = {"membership_id": "4611686018000000001", "membership_type": 3}
    bungie.get_historical_stats_for_account.return_value = {
        "ErrorCode": 1,
        "Response": {
            "mergedAllCharacters": {"results": {}},
            "mergedDeletedCharacters": {"results": {}},
            "characters": [_character(EXISTING_ID, False), _character(DELETED_ID, True)],
        },
    }

    async def char_stats(mtype, mid, char_id, modes=None, period_type=None):
        values = EXISTING if char_id == EXISTING_ID else DELETED
        return {"ErrorCode": 1, "Response": {TRIALS_GROUP: {"allTime": _section(values)}}}

    bungie.get_historical_stats.side_effect = char_stats
    return ActivityService(bungie, manifest, resolver)


def _row(result: dict, stat_id: str) -> dict:
    for group in result["groups"]:
        for row in group["stats"]:
            if row["stat_id"] == stat_id:
                return row
    raise AssertionError(f"payload 里没有 {stat_id}")


@pytest.mark.asyncio
async def test_account_mode_stats_are_merged_per_character(service: ActivityService) -> None:
    """账号级 + mode=trials：逐角色取、按同一套语义合，三个档位都不是上游给的。"""
    result = await service.get_historical_stats(PLAYER_NAME, mode="trials")

    assert result["scope"] == "account"
    assert result["aggregation"] == "computed", "上游没有模式的合并视图，必须自报是自算的"
    assert result["mode"] == {
        "key": "trials", "label": "奥斯里斯试炼",
        "upstream_modes": 84, "upstream_group": TRIALS_GROUP,
    }
    assert result["period"] == {"key": "career", "label": "生涯", "upstream_period_type": 2}
    assert [group["key"] for group in result["groups"]] == ["trials"]
    assert result["groups"][0]["label"] == "奥斯里斯试炼"
    assert result["characters"] == {"existing": 1, "deleted": 1, "total": 2, "returned": 2}

    kills = _row(result, "kills")
    assert (kills["existing"], kills["deleted"], kills["account_total"]) == (473, 423, 896)
    assert kills["aggregate"] == "sum"
    # 比值按分量重算（真机同一批数字：473/562）
    kd = _row(result, "kills_deaths_ratio")
    assert kd["aggregate"] == "derived"
    assert kd["existing"] == pytest.approx(473 / 562, abs=1e-4)
    assert kd["account_total"] == pytest.approx(896 / 1070, abs=1e-4)
    wlr = _row(result, "win_loss_ratio")
    assert wlr["existing"] == pytest.approx(50 / (114 - 50), abs=1e-4)
    # 最多的取最大，不相加
    spree = _row(result, "longest_kill_spree")
    assert spree["aggregate"] == "max"
    assert (spree["existing"], spree["deleted"], spree["account_total"]) == (7, 8, 8)

    # 每个角色都带了 modes/periodType（且只用对照表里的 84，不是 ACTIVITY_MODES 的 9）
    calls = service._bungie.get_historical_stats.await_args_list
    assert [call.kwargs["modes"] for call in calls] == [84, 84]
    assert {call.kwargs["period_type"] for call in calls} == {2}
    assert all(call.kwargs["modes"] != 9 for call in calls), "modes=9 会 500"


@pytest.mark.asyncio
async def test_mode_word_accepts_the_chinese_label(service: ActivityService) -> None:
    """中文标签走同一张对照表（`奥斯里斯试炼` → trials/84），不另抄一份映射。"""
    result = await service.get_historical_stats(PLAYER_NAME, mode="奥斯里斯试炼")

    assert result["mode"]["key"] == "trials"
    assert result["mode"]["upstream_modes"] == activity_modes.MODE_TYPE["trials"]


@pytest.mark.asyncio
async def test_unknown_mode_word_is_an_error_not_an_empty_answer(service: ActivityService) -> None:
    """词表外的模式报错（"筛出来是空"和"你筛的词我不认识"是两件事）。

    用"猛攻"当例子：这个词**故意不在**词表里 —— Manifest 里没有 Onslaught 这个模式
    （86 叫 Offensive/攻势，无法确认就是猛攻），旧代码把它当 69（多人竞技PvP）返回，
    是错答。宁可报"不认识"。
    """
    with pytest.raises(InvalidArgumentError, match="不支持活动模式") as info:
        await service.get_historical_stats(PLAYER_NAME, mode="猛攻")

    assert "crucible" in str(info.value) and "trials" in str(info.value)
    service._bungie.get_historical_stats.assert_not_awaited()


@pytest.mark.asyncio
async def test_single_character_mode_stats_are_labelled(service: ActivityService) -> None:
    """给了 character 就只取那一个角色，并标 scope=character + 模式标签。"""
    resolver_profile = {
        "characters": {"data": {EXISTING_ID: {"classType": 1}}}
    }
    service._resolver.get_profile.return_value = resolver_profile

    result = await service.get_historical_stats(PLAYER_NAME, character="hunter", mode="trials")

    assert result["scope"] == "character"
    assert result["character"]["name"] == "Hunter"
    assert result["mode"]["key"] == "trials"
    assert result["aggregation"] if "aggregation" in result else True
    assert "aggregation" not in result, "单角色是上游给的值，不是我们自己合的"
    assert [group["key"] for group in result["groups"]] == ["trials"]
    assert _row(result, "kills")["value"] == 473


@pytest.mark.asyncio
async def test_one_bad_character_does_not_break_the_mode_payload(service: ActivityService) -> None:
    """个别角色读不到 → 用其余角色合并 + 如实标 unavailable，而不是整份失败。"""
    from destiny_mcp.exceptions import APIError

    async def char_stats(mtype, mid, char_id, modes=None, period_type=None):
        if char_id == DELETED_ID:
            raise APIError("读取历史统计", "Bungie 返回 HTTP 500")
        return {"ErrorCode": 1, "Response": {TRIALS_GROUP: {"allTime": _section(EXISTING)}}}

    service._bungie.get_historical_stats.side_effect = char_stats

    result = await service.get_historical_stats(PLAYER_NAME, mode="trials")

    assert result["characters"]["returned"] == 1
    assert "1 个角色" in result["unavailable"] and "500" in result["unavailable"]
    assert _row(result, "kills")["account_total"] == 473


@pytest.mark.asyncio
async def test_mode_with_no_data_says_empty_not_zero(service: ActivityService) -> None:
    """上游对这个模式返回空 → 空清单 + empty_reason（"没有数据"不是 0）。"""

    async def char_stats(mtype, mid, char_id, modes=None, period_type=None):
        return {"ErrorCode": 1, "Response": {}}

    service._bungie.get_historical_stats.side_effect = char_stats

    result = await service.get_historical_stats(PLAYER_NAME, mode="gambit")

    assert result["groups"] == []
    assert "没有数据" in result["empty_reason"] and "不是 0" in result["empty_reason"]
    assert result["mode"]["key"] == "gambit"


@pytest.mark.asyncio
async def test_all_characters_failing_is_a_failure(service: ActivityService) -> None:
    """全部角色都读不到时不许给空壳：那是失败，不是"这个模式没数据"。"""
    from destiny_mcp.exceptions import APIError

    async def char_stats(mtype, mid, char_id, modes=None, period_type=None):
        raise APIError("读取历史统计", "Bungie 返回 HTTP 500")

    service._bungie.get_historical_stats.side_effect = char_stats

    with pytest.raises(APIError, match="500"):
        await service.get_historical_stats(PLAYER_NAME, mode="trials")


@pytest.mark.asyncio
async def test_season_period_is_reported_unavailable_not_downgraded() -> None:
    """赛季周期：上游没有 → ok=false + unavailable + 指向 counters，**不降级成生涯**。"""
    for period in ("season", "act"):
        response = await _stats_branches.stats_response({}, PLAYER_NAME, None, "", period)

        assert response["ok"] is False
        assert response["error"]["code"] == "a_p_i_error"
        assert period in response["error"]["message"] and "unavailable" in response["error"]["message"]
        assert any("counters" in str(action) for action in response["next_actions"])
        assert any("没有降级" in warning for warning in response["warnings"])


@pytest.mark.asyncio
async def test_empty_character_scope_says_empty_not_zero() -> None:
    """单角色 + 模式但这个人没打过 → 空清单 + 说清"没有数据"，不是 0。"""
    class _Svc:
        async def get_historical_stats(self, *args, **kwargs):
            return {
                "schema_version": 2, "source": "GetHistoricalStats", "scope": "character",
                "character": {"name": "Hunter", "character_id": "c1"},
                "mode": {"key": "trials", "label": "奥斯里斯试炼", "upstream_modes": 84},
                "period": {"key": "career", "label": "生涯", "upstream_period_type": 2},
                "groups": [],
            }

    response = await _stats_branches.stats_response(
        {"activity_svc": _Svc()}, PLAYER_NAME, "hunter", "trials", ""
    )

    assert response["ok"] is True
    assert response["data"]["stats"]["groups"] == []
    assert any("没有数据" in warning and "不是 0" in warning for warning in response["warnings"])


@pytest.mark.asyncio
async def test_stats_period_type_only_knows_career() -> None:
    """`periodType` 映射的唯一出处：只有 career → 2（上游没有 Season，传 3 会 500）。"""
    from destiny_mcp import activity_stats
    from destiny_mcp.services.activity_service import _stats_period_type

    assert activity_stats.STATS_PERIOD_TYPES == {"career": 2}
    assert _stats_period_type("career") == 2
    assert _stats_period_type("") == 2
    for bad in ("season", "act", "daily"):
        with pytest.raises(InvalidArgumentError, match="counters"):
            _stats_period_type(bad)


@pytest.mark.asyncio
async def test_per_mode_stats_carry_the_in_game_counters(service: ActivityService) -> None:
    """按模式的生涯统计必须并列同模式的游戏内计数器（ADR-005 落到 mode 上）。

    真机（2026-09-18）：`stats(mode="trials")` 给击败 1,474 / 胜场 105，而游戏内计数器是
    10,696 / 826 —— 差 7 倍。以前按模式的调用**不附计数器、也没有任何提示**，
    用户只会看到那个小数；同一份报告还把它当成"试炼生涯"报了出去。
    """
    from destiny_mcp.tools import _stats_branches as branches

    counters_svc = AsyncMock()
    counters_svc.get_career_counters.return_value = {
        "counters": [
            {"metric_hash": 2082314848, "name": "已击败对手", "progress": 10696},
            {"metric_hash": 1365664208, "name": "胜场", "progress": 826},
            {"metric_hash": 999, "name": "无关计数器", "progress": 1},   # 不在配对表里，不许进载荷
        ],
        "unavailable": "",
    }
    svc = {"activity_svc": service, "activity_counters_svc": counters_svc}

    response = await branches.stats_response(svc, PLAYER_NAME, None, "trials", "career")

    assert response["ok"] is True
    assert [row["metric_hash"] for row in response["data"]["game_counters"]] == [2082314848, 1365664208]
    paired = [w for w in response["warnings"] if "不一样是正常的" in w]
    assert paired, response["warnings"]
    # 并排给两个数：游戏内 10,696 vs 统计接口 1,474（夹具值 900+574）
    assert "10696" in paired[0] and "1474" in paired[0], paired[0]
    # 读的就是 trials + career 那格
    kwargs = counters_svc.get_career_counters.await_args.args
    assert kwargs[3] == "trials" and kwargs[4] == "career"


@pytest.mark.asyncio
async def test_per_mode_stats_degrade_when_counters_are_unavailable(service: ActivityService) -> None:
    """计数器读不到只降级：统计结果照给，并把原因写出来（不许把主结果弄坏）。"""
    from destiny_mcp.tools import _stats_branches as branches

    counters_svc = AsyncMock()
    counters_svc.get_career_counters.side_effect = RuntimeError("上游抖动")
    svc = {"activity_svc": service, "activity_counters_svc": counters_svc}

    response = await branches.stats_response(svc, PLAYER_NAME, None, "trials", "career")

    assert response["ok"] is True
    assert "game_counters" not in response["data"]
    assert "上游抖动" in response["data"]["counters_unavailable"]
    assert any("没读到" in w for w in response["warnings"])


@pytest.mark.asyncio
async def test_stats_reads_both_sources_in_parallel(service: ActivityService) -> None:
    """统计接口与游戏内计数器**同时**去拿（真机实测 4.9s → 约 3s）。

    两边都故意睡 0.2 秒：串行 ≈ 0.4s，并发 ≈ 0.2s；断言 < 0.32s 把"真的并发"钉住。
    """
    import asyncio
    import time

    from destiny_mcp.tools import _stats_branches as branches

    original_stats = service.get_historical_stats

    async def slow_stats(*args, **kwargs):
        await asyncio.sleep(0.2)
        return await original_stats(*args, **kwargs)

    service.get_historical_stats = slow_stats  # type: ignore[method-assign]
    counters_svc = AsyncMock()

    async def slow_counters(*_args, **_kwargs):
        await asyncio.sleep(0.2)
        return {"counters": [], "unavailable": ""}

    counters_svc.get_career_counters.side_effect = slow_counters
    svc = {"activity_svc": service, "activity_counters_svc": counters_svc}

    started = time.monotonic()
    response = await branches.stats_response(svc, PLAYER_NAME, None, "", "career")
    elapsed = time.monotonic() - started

    assert response["ok"] is True
    assert elapsed < 0.32, f"看起来是串行：两个各 0.2s 的来源花了 {elapsed:.2f}s"
