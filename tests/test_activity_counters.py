"""游戏内生涯计数器（profile 组件 1100）：替身测试，不碰真机。

盯住的是四条真机踩过的坑：

1. **读取会抖动**：同一 URL 连续请求会出现整块 `metrics` 缺失的响应 —— 必须重试，
   而且重试后仍为空时要如实报 `unavailable`，**绝不能把空当成 0**；
2. **缺值不是 0**：`progress` 真的是 0 要留 0，字段缺失要给 `None`；
3. **名字来自 Manifest**（`DestinyMetricDefinition`），查不到就降级成 `#hash`，不崩；
4. **每个数字自带出处**：payload 必带 `source: "profile.metrics"` —— 统计接口那边
   对同一个生涯数字还有另一个值，混了就没法解释"为什么和游戏里不一样"。

真机核对（"熔炉生涯击败 = 124,495"）在 docs/reference/bungie_api.md 的「Metrics vs Stats」，
不进这个文件：单测用替身、任何机器能跑（docs/testing/TESTING_CORPUS.md 第一张表）。
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from destiny_mcp.exceptions import InvalidArgumentError
from destiny_mcp.services import activity_counters_service as counters_module
from destiny_mcp.services.activity_counters_service import ActivityCountersService
from destiny_mcp.services import profile_components
from destiny_mcp.tools import _counters_branches

PLAYER_NAME = "TestGuardian#1234"
MEMBERSHIP_ID = "4611686018000000001"
MEMBERSHIP_TYPE = 3

OPPONENTS_DEFEATED = 811894228
KILLS = 811894229
DELETED_CHARACTER_STAT = 811894230
TRIALS_OPPONENTS_DEFEATED = 2082314848
SEASON_OPPONENTS_DEFEATED = 2935221077
IRON_BANNER_OPPONENTS = 2161492053


def metric_entry(progress: Any = None, completion_value: Any = None, **extra: Any) -> dict:
    section: dict[str, Any] = {}
    if progress is not None or completion_value is not None:
        section = {"progress": progress, "completionValue": completion_value}
    section.update(extra)
    return {"invisible": False, "objectiveProgress": section}


def profile_with(metrics: dict | None) -> dict:
    """组件 1100 的响应形状：`Response.metrics.data.metrics`。"""
    return {"metrics": {"data": {"metrics": metrics}}}


def definitions() -> dict[int, dict]:
    return {
        OPPONENTS_DEFEATED: {
            "displayProperties": {
                "name": "Opponents Defeated",
                "description": (
                    "The total number of opponents defeated in Crucible matches. "
                    "Tracks from Season 1 onward."
                ),
            }
        },
        KILLS: {
            "displayProperties": {
                "name": "Kills",
                "description": "The total number of kills in Crucible matches.",
            }
        },
    }


def make_service(profiles: list[dict]) -> ActivityCountersService:
    """`profiles` 按顺序作为每次读取的返回值；用尽后用最后一条（不是 StopIteration）。"""
    resolver = AsyncMock()
    resolver.resolve_player.return_value = {
        "membership_id": MEMBERSHIP_ID,
        "membership_type": MEMBERSHIP_TYPE,
    }

    def next_profile(*_args: Any, **_kwargs: Any) -> dict:
        return profiles[min(resolver.get_profile.await_count - 1, len(profiles) - 1)]

    resolver.get_profile.side_effect = next_profile

    manifest = MagicMock()
    manifest.get_metric_definition.side_effect = lambda h: definitions().get(h)
    return ActivityCountersService(AsyncMock(), manifest, resolver)


async def test_parses_names_descriptions_and_progress() -> None:
    service = make_service([profile_with({
        str(OPPONENTS_DEFEATED): metric_entry(124495, 100),
        str(KILLS): metric_entry(78768, 100),
    })])

    result = await service.get_career_counters(PLAYER_NAME)

    by_hash = {counter["metric_hash"]: counter for counter in result["counters"]}
    assert by_hash[OPPONENTS_DEFEATED]["name"] == "Opponents Defeated"
    assert "Crucible matches" in by_hash[OPPONENTS_DEFEATED]["description"]
    assert by_hash[OPPONENTS_DEFEATED]["progress"] == 124495
    assert by_hash[OPPONENTS_DEFEATED]["completion_value"] == 100
    assert by_hash[OPPONENTS_DEFEATED]["name_resolved"] is True
    # 默认按数值降序：124,495 在前
    assert result["counters"][0]["metric_hash"] == OPPONENTS_DEFEATED
    assert (result["total"], result["returned"], result["truncated"]) == (2, 2, False)
    assert result["unavailable"] == ""
    assert result["components"] == "1100"
    # 请求的就是组件 1100（组件号的唯一出处是 profile_components）
    assert profile_components.METRICS == [1100]
    _player, _type, components = service._resolver.get_profile.await_args.args
    assert components == [1100]


async def test_empty_metrics_is_unavailable_not_zero(monkeypatch) -> None:
    """整块 metrics 缺失 → 重试也拿不到 → `unavailable`，且不把空当 0。"""
    monkeypatch.setattr(counters_module, "DELAY_SECONDS", 0)
    service = make_service([profile_with({})])

    result = await service.get_career_counters(PLAYER_NAME)

    assert result["counters"] == []
    assert result["total"] == 0
    assert "组件 1100" in result["unavailable"]
    assert "空不是 0" in result["unavailable"]
    # 重试真的发生了（这就是真机抖动要靠的那一步）
    assert service._resolver.get_profile.await_count == counters_module.ATTEMPTS


async def test_retries_after_upstream_jitter() -> None:
    """第一次空、第二次有数据：抖动必须被重试吃掉，且只读两次。"""
    service = make_service([
        profile_with({}),
        profile_with({str(OPPONENTS_DEFEATED): metric_entry(124495, 100)}),
    ])

    result = await service.get_career_counters(PLAYER_NAME, query="crucible")

    assert result["unavailable"] == ""
    assert result["counters"][0]["progress"] == 124495
    assert service._resolver.get_profile.await_count == 2


async def test_read_failure_is_retried_then_reported(monkeypatch) -> None:
    """上游抛错也算"这次没读到"：重试到用尽，然后如实报不可用（不编数字）。"""
    monkeypatch.setattr(counters_module, "DELAY_SECONDS", 0)
    service = make_service([])
    service._resolver.get_profile.side_effect = RuntimeError("upstream exploded")

    result = await service.get_career_counters(PLAYER_NAME)

    assert result["counters"] == []
    assert result["unavailable"]
    assert service._resolver.get_profile.await_count == counters_module.ATTEMPTS


async def test_missing_metric_definition_degrades_to_hash() -> None:
    """Manifest 里查不到定义：名字降级为 #hash，不崩，其余字段照常。"""
    service = make_service([profile_with({
        str(DELETED_CHARACTER_STAT): metric_entry(28242, 100),
    })])

    result = await service.get_career_counters(PLAYER_NAME)

    counter = result["counters"][0]
    assert counter["name"] == f"#{DELETED_CHARACTER_STAT}"
    assert counter["description"] == ""
    assert counter["name_resolved"] is False
    assert counter["progress"] == 28242
    assert any("DestinyMetricDefinition" in warning for warning in result["warnings"])


async def test_missing_progress_stays_none_and_zero_stays_zero() -> None:
    service = make_service([profile_with({
        str(OPPONENTS_DEFEATED): metric_entry(0, 100),
        str(KILLS): {"invisible": False, "objectiveProgress": {"completionValue": 100}},
    })])

    result = await service.get_career_counters(PLAYER_NAME)
    by_hash = {counter["metric_hash"]: counter for counter in result["counters"]}

    assert by_hash[OPPONENTS_DEFEATED]["progress"] == 0
    assert by_hash[KILLS]["progress"] is None
    assert by_hash[KILLS]["completion_value"] == 100
    assert any("progress" in warning for warning in result["warnings"])


async def test_query_filters_and_count_truncates(monkeypatch) -> None:
    monkeypatch.setattr(counters_module, "DELAY_SECONDS", 0)
    service = make_service([profile_with({
        str(OPPONENTS_DEFEATED): metric_entry(124495, 100),
        str(KILLS): metric_entry(78768, 100),
        str(DELETED_CHARACTER_STAT): metric_entry(28242, 100),
    })])

    everything = await service.get_career_counters(PLAYER_NAME)
    only_kills = await service.get_career_counters(PLAYER_NAME, query="KILLS")
    limited = await service.get_career_counters(PLAYER_NAME, limit=1)

    assert everything["total"] == 3
    # 名字大小写不敏感
    assert [counter["metric_hash"] for counter in only_kills["counters"]] == [KILLS]
    assert limited["returned"] == 1
    assert limited["truncated"] is True
    assert limited["filter"] == {"query": "", "mode": "", "period": "", "limit": 1}


async def test_mode_and_period_filter_by_the_table(monkeypatch) -> None:
    """`mode=`/`period=` 是这张对照表的用途本身：三条重名的「已击败对手」要能分开。"""
    monkeypatch.setattr(counters_module, "DELAY_SECONDS", 0)
    service = make_service([profile_with({
        str(OPPONENTS_DEFEATED): metric_entry(124495, 100),        # 熔炉 / 生涯
        str(TRIALS_OPPONENTS_DEFEATED): metric_entry(10696, 100),  # 试炼 / 生涯
        str(SEASON_OPPONENTS_DEFEATED): metric_entry(3522, 100),   # 熔炉 / 本赛季
        str(IRON_BANNER_OPPONENTS): metric_entry(1737, 100),       # 铁旗 / 本赛季
    })])
    manifest_names = {
        OPPONENTS_DEFEATED: "已击败对手",
        TRIALS_OPPONENTS_DEFEATED: "已击败对手",
        SEASON_OPPONENTS_DEFEATED: "已击败对手",
        IRON_BANNER_OPPONENTS: "已击败铁旗对手数",
    }
    service._manifest.get_metric_definition.side_effect = lambda h: (
        {"displayProperties": {"name": manifest_names[h], "description": ""}}
        if h in manifest_names else None
    )

    trials = await service.get_career_counters(PLAYER_NAME, mode="trials")
    banner = await service.get_career_counters(PLAYER_NAME, mode="iron_banner")
    season = await service.get_career_counters(PLAYER_NAME, period="season")

    assert [(row["metric_hash"], row["progress"]) for row in trials["counters"]] == [
        (TRIALS_OPPONENTS_DEFEATED, 10696)
    ]
    assert [(row["metric_hash"], row["progress"]) for row in banner["counters"]] == [
        (IRON_BANNER_OPPONENTS, 1737)
    ]
    assert {row["metric_hash"] for row in season["counters"]} == {
        SEASON_OPPONENTS_DEFEATED, IRON_BANNER_OPPONENTS,
    }
    # 过滤条件与词表都进返回值：调用方要能自证"筛的是哪一批"。
    assert trials["filter"]["mode"] == "trials"
    assert trials["labels"]["modes"]["trials"] == "奥斯里斯试炼"
    assert all(row["mode_label"] and row["label_zh"] for row in trials["counters"])


async def test_mode_filter_rejects_unknown_words(monkeypatch) -> None:
    """词表外的 mode 报错（不是"0 条"）：日落本来就不在对照表里，说清词表比给空清单有用。"""
    monkeypatch.setattr(counters_module, "DELAY_SECONDS", 0)
    service = make_service([profile_with({str(OPPONENTS_DEFEATED): metric_entry(1, 100)})])

    with pytest.raises(InvalidArgumentError) as excinfo:
        await service.get_career_counters(PLAYER_NAME, mode="日落")

    assert "crucible" in str(excinfo.value)


async def test_every_payload_row_carries_its_source() -> None:
    """payload 必带 source：同一个生涯数字在统计接口那边还有另一个值。"""
    service = make_service([profile_with({
        str(OPPONENTS_DEFEATED): metric_entry(124495, 100),
    })])

    result = await service.get_career_counters(PLAYER_NAME)

    assert result["counters"]
    assert {counter["source"] for counter in result["counters"]} == {"profile.metrics"}


async def test_tool_branch_reports_failure_instead_of_success(monkeypatch) -> None:
    """工具层：读不到 → ok=false（不是"0 条计数器"的成功信封）。"""
    monkeypatch.setattr(counters_module, "DELAY_SECONDS", 0)
    service = make_service([profile_with({})])

    response = await _counters_branches.counters_response(
        {"activity_counters_svc": service}, PLAYER_NAME, "", 20
    )

    assert response["ok"] is False
    assert response["error"]["code"] == "a_p_i_error"
    assert "组件 1100" in response["error"]["message"]


async def test_tool_branch_returns_rows_with_source_and_scope_warning() -> None:
    service = make_service([profile_with({
        str(OPPONENTS_DEFEATED): metric_entry(124495, 100),
    })])

    response = await _counters_branches.counters_response(
        {"activity_counters_svc": service}, PLAYER_NAME, "", 20
    )

    assert response["ok"] is True
    assert response["data"]["counters"][0]["progress"] == 124495
    assert response["data"]["counters"][0]["source"] == "profile.metrics"
    assert any("stats" in warning for warning in response["warnings"])
