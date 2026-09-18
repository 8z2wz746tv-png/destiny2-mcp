"""P1 三档并存的守门：`existing` / `deleted` / `account_total` 各是什么、怎么合并。

数字用的是**真机实测**（2026-09-17，本机真账号，只读）：

| 档 | 熔炉生涯击败 | 来源 |
| --- | --- | --- |
| account_total | 78,864 | 上游 `mergedAllCharacters`（**已含已删角色**） |
| deleted | 28,242 | 上游 `mergedDeletedCharacters`（其中已删那 5 条的明细） |
| existing | 50,622 | 我们自己从 3 条现存角色重算 |

**account_total 就是 existing + deleted**：本项目早期计划里的 107,106 是把
`mergedDeletedCharacters` 又加了一遍（等于把已删角色算两遍）。这条测试专门钉死这个错。

另外钉住三件容易做错的事：

1. 比值类（K/D、效率、胜率、KDA）跨角色**不能相加**，只能按公式从分量重算（`derived`）；
2. "最多/最长/最高"类取最大值（`max`），不是求和；
3. 比不出的（均值、枚举）给 `null`（`none`），**不编 0**。
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from destiny_mcp import activity_stats as stats
from destiny_mcp.services.activity_service import ActivityService


PLAYER_NAME = "TestGuardian#1234"

# 真机 8 条角色的 allPvP.opponentsDefeated（3 现存 + 5 已删）
EXISTING_OPPONENTS = [22279, 19479, 8864]
DELETED_OPPONENTS = [14457, 5004, 105, 8225, 451]
ACCOUNT_TOTAL = 78864
DELETED_TOTAL = 28242


def _entry(value: float, display: str | None = None) -> dict:
    return {"basic": {"value": value, "displayValue": display if display is not None else str(value)}}


def _character(character_id: str, deleted: bool, values: dict) -> dict:
    return {
        "characterId": character_id,
        "deleted": deleted,
        "results": {"allPvP": {"allTime": {key: _entry(value) for key, value in values.items()}}},
    }


@pytest.fixture
def service() -> ActivityService:
    bungie = AsyncMock()
    manifest = MagicMock()
    resolver = AsyncMock()
    resolver.resolve_player.return_value = {"membership_id": "4611686018000000001", "membership_type": 3}
    return ActivityService(bungie, manifest, resolver)


def _real_account_fixture(extra_per_character: dict | None = None) -> dict:
    """真机形状的账号级响应：8 条角色 + 两个合并块。"""
    extra = extra_per_character or {}
    characters = []
    for index, opponents in enumerate(EXISTING_OPPONENTS):
        values = {"opponentsDefeated": opponents, **{k: v[index] for k, v in extra.items()}}
        characters.append(_character(f"existing-{index}", False, values))
    for index, opponents in enumerate(DELETED_OPPONENTS):
        values = {"opponentsDefeated": opponents, **{k: v[len(EXISTING_OPPONENTS) + index] for k, v in extra.items()}}
        characters.append(_character(f"deleted-{index}", True, values))
    return {
        "ErrorCode": 1,
        "Response": {
            "mergedAllCharacters": {"results": {"allPvP": {"allTime": {"opponentsDefeated": _entry(ACCOUNT_TOTAL, "78864")}}}},
            "mergedDeletedCharacters": {"results": {"allPvP": {"allTime": {"opponentsDefeated": _entry(DELETED_TOTAL)}}}},
            "characters": characters,
        },
    }


def _row(result: dict, stat_id: str) -> dict:
    for group in result["groups"]:
        for row in group["stats"]:
            if row["stat_id"] == stat_id:
                return row
    raise AssertionError(f"payload 里没有 {stat_id}")


@pytest.mark.asyncio
async def test_three_tiers_are_the_real_numbers(service: ActivityService) -> None:
    """50,622 / 28,242 / 78,864，且 account_total == existing + deleted。"""
    service._bungie.get_historical_stats_for_account.return_value = _real_account_fixture()

    result = await service.get_historical_stats(PLAYER_NAME)

    row = _row(result, "opponents_defeated")
    assert row["existing"] == sum(EXISTING_OPPONENTS) == 50622
    assert row["deleted"] == DELETED_TOTAL == 28242
    assert row["account_total"] == ACCOUNT_TOTAL == 78864
    assert row["account_total"] == row["existing"] + row["deleted"], "已删角色不能被算两遍"
    assert row["aggregate"] == stats.AGGREGATE_SUM
    assert row["name"] == "击败对手" and row["display"] == "78864"
    # 三档的名字随 payload 给出去，读的人不用猜哪个数是哪一档
    assert set(result["tiers"]) == set(stats.TIERS)
    assert result["characters"] == {"existing": 3, "deleted": 5, "total": 8}
    assert result["scope"] == "account" and result["source"] == "GetHistoricalStatsForAccount"


@pytest.mark.asyncio
async def test_max_stats_take_the_maximum_not_the_sum(service: ActivityService) -> None:
    """`longestKillSpree`/`bestSingleGameKills` 取最大值：加起来会变成一个不存在的纪录。"""
    per_character = {"longestKillSpree": [21, 9, 4, 20, 11, 3, 2, 1],
                     "bestSingleGameKills": [41, 30, 12, 80, 22, 7, 5, 3]}
    fixture = _real_account_fixture(per_character)
    pvp = fixture["Response"]["mergedAllCharacters"]["results"]["allPvP"]["allTime"]
    pvp["longestKillSpree"] = _entry(21)
    pvp["bestSingleGameKills"] = _entry(80)
    deleted = fixture["Response"]["mergedDeletedCharacters"]["results"]["allPvP"]["allTime"]
    deleted["longestKillSpree"] = _entry(20)
    deleted["bestSingleGameKills"] = _entry(80)
    service._bungie.get_historical_stats_for_account.return_value = fixture

    result = await service.get_historical_stats(PLAYER_NAME)

    spree = _row(result, "longest_kill_spree")
    assert spree["aggregate"] == stats.AGGREGATE_MAX
    assert spree["existing"] == 21, "现存角色里最长的是 21，不是 34"
    assert spree["deleted"] == 20
    assert spree["account_total"] == 21
    best = _row(result, "best_single_game_kills")
    assert best["aggregate"] == stats.AGGREGATE_MAX
    assert best["existing"] == 41
    assert best["account_total"] == 80


@pytest.mark.asyncio
async def test_ratio_stats_are_derived_not_summed(service: ActivityService) -> None:
    """K/D、效率、胜率、KDA 跨角色按分量重算（真机核对的公式），不是把比值加起来。"""
    kills = [100, 60, 40, 200, 30, 10, 5, 2]
    deaths = [80, 40, 20, 150, 25, 8, 4, 1]
    assists = [20, 10, 5, 60, 6, 2, 1, 0]
    won = [5, 3, 2, 10, 1, 1, 0, 0]
    entered = [8, 5, 3, 20, 2, 2, 1, 0]
    score = [1000, 600, 400, 2000, 300, 100, 50, 20]
    per_character = {"kills": kills, "deaths": deaths, "assists": assists,
                     "activitiesWon": won, "activitiesEntered": entered, "score": score}
    fixture = _real_account_fixture(per_character)
    merged = fixture["Response"]["mergedAllCharacters"]["results"]["allPvP"]["allTime"]
    for key, values in per_character.items():
        merged[key] = _entry(sum(values))
    # 账号级那一档是上游自己的合并值（比值的显示值也由它给），照真机形状放一份：
    # 行的集合由上游两段视图决定，所以上游发布过的比值键都要在
    merged["killsDeathsRatio"] = _entry(1.3372127723067742, "1.34")
    merged["efficiency"] = _entry(1.6810333802276507, "1.68")
    merged["winLossRatio"] = _entry(0.9868823786620026, "0.99")
    merged["killsDeathsAssists"] = _entry(1.5091230762672123, "1.51")
    merged["averageScorePerKill"] = _entry(1.9001817196416617, "1.90")
    service._bungie.get_historical_stats_for_account.return_value = fixture

    result = await service.get_historical_stats(PLAYER_NAME)

    existing_kills = sum(kills[:3])
    existing_deaths = sum(deaths[:3])
    existing_assists = sum(assists[:3])
    # 数值与单角色行同一套精度（_number：小数四位），所以断言按四位比
    kd = _row(result, "kills_deaths_ratio")
    assert kd["aggregate"] == stats.AGGREGATE_DERIVED
    assert kd["existing"] == pytest.approx(existing_kills / existing_deaths, abs=1e-4)
    assert kd["existing"] != pytest.approx(sum([100 / 80, 60 / 40, 40 / 20]), abs=1e-4), "比值不能相加"
    assert kd["account_total"] == pytest.approx(1.3372, abs=1e-4), "账号级照抄上游（四位精度）"
    assert kd["display"] == "1.34", "显示值用上游给的"
    assert _row(result, "efficiency")["existing"] == pytest.approx(
        (existing_kills + existing_assists) / existing_deaths, abs=1e-4
    )
    assert _row(result, "win_loss_ratio")["existing"] == pytest.approx(
        sum(won[:3]) / (sum(entered[:3]) - sum(won[:3])), abs=1e-4
    )
    assert _row(result, "kills_deaths_assists")["account_total"] == pytest.approx(1.5091, abs=1e-4)
    assert _row(result, "kills_deaths_assists")["existing"] == pytest.approx(
        (existing_kills + existing_assists / 2) / existing_deaths, abs=1e-4
    )
    assert _row(result, "average_score_per_kill")["existing"] == pytest.approx(
        sum(score[:3]) / existing_kills, abs=1e-4
    )


@pytest.mark.asyncio
async def test_unmergeable_stats_give_null_not_zero(service: ActivityService) -> None:
    """均值/枚举类没有可用的合并语义 → `none` + `existing: null`（缺值不编 0）。"""
    fixture = _real_account_fixture()
    merged = fixture["Response"]["mergedAllCharacters"]["results"]["allPvP"]["allTime"]
    merged["averageLifespan"] = _entry(45.69, "45.69")
    merged["weaponBestType"] = _entry(8)
    deleted = fixture["Response"]["mergedDeletedCharacters"]["results"]["allPvP"]["allTime"]
    deleted["averageLifespan"] = _entry(45.65)
    for character in fixture["Response"]["characters"]:
        character["results"]["allPvP"]["allTime"]["averageLifespan"] = _entry(30 + int(character["deleted"]))
    service._bungie.get_historical_stats_for_account.return_value = fixture

    result = await service.get_historical_stats(PLAYER_NAME)

    lifespan = _row(result, "average_lifespan")
    assert lifespan["aggregate"] == stats.AGGREGATE_NONE
    assert lifespan["existing"] is None, "均值合不出来就给 null，不是 0"
    assert lifespan["deleted"] == pytest.approx(45.65, abs=1e-4), "上游给的档照给"
    assert lifespan["account_total"] == pytest.approx(45.69, abs=1e-4)


def test_merge_sections_does_not_sum_ratios_or_invent_missing() -> None:
    sections = [
        {"kills": _entry(10), "deaths": _entry(5), "killsDeathsRatio": _entry(2.0), "averageLifespan": _entry(30)},
        {"kills": _entry(30), "deaths": _entry(5), "killsDeathsRatio": _entry(6.0), "averageLifespan": _entry(40)},
    ]

    merged = stats.merge_sections(sections)

    assert merged["kills"]["basic"]["value"] == 40
    assert merged["killsDeathsRatio"]["basic"]["value"] == pytest.approx(4.0), "40/10，不是 2+6"
    assert merged["killsDeathsRatio"]["basic"]["displayValue"] == "4.00", "比值显示留两位"
    assert "averageLifespan" not in merged, "合不出来的项就不出现，不编一个数"
    assert stats.aggregate_kind("averageLifespan") == stats.AGGREGATE_NONE
    assert stats.aggregate_kind("weaponKillsHandCannon") == stats.AGGREGATE_SUM
    assert stats.aggregate_kind("someBrandNewStat") == stats.AGGREGATE_NONE, "没登记的不许默认求和"


def test_every_fixture_stat_key_is_classified_on_purpose() -> None:
    """上游真机键清单里的每一项都要么有明确语义、要么明确是 `none` —— 不许"漏着默认"。"""
    import json
    from pathlib import Path

    fixture = json.loads(
        (Path(__file__).parent / "baselines" / "activity_stat_keys.json").read_text(encoding="utf-8")
    )
    keys = set(fixture["allPvE"]) | set(fixture["allPvP"])
    none_keys = {key for key in keys if stats.aggregate_kind(key) == stats.AGGREGATE_NONE}
    assert none_keys == {
        "averageDeathDistance", "averageKillDistance", "averageLifespan",
        "averageScorePerLife", "combatRating", "weaponBestType",
    }, "`none` 是逐项决定的，不是「剩下的都算 none」；改动要在这里显式体现"


@pytest.mark.asyncio
async def test_account_stats_pair_the_in_game_counter_and_explain_the_gap(
    service: ActivityService,
) -> None:
    """账号级那条路也要并列游戏内计数器，并写清差多少（真机 124,495 vs 78,864，差 45,631）。

    这条补的是**覆盖盲区**：`_paired_warnings` 里的配对表遍历以前从没被跑到过
    （测试替身里没有 counters service → 走"读不到"的早退分支），于是 2026-09-18 把
    配对表按模式分层时，账号级这条路直接抛 `ValueError: not enough values to unpack`
    —— 全量单测没红，是真机语料抓出来的。
    """
    from destiny_mcp.tools import _stats_branches as branches

    service._bungie.get_historical_stats_for_account.return_value = _real_account_fixture()
    counters_svc = AsyncMock()
    counters_svc.get_career_counters.return_value = {
        "counters": [
            {"metric_hash": 811894228, "name": "已击败对手", "progress": 124495},
            {"metric_hash": 3157801630, "name": "金色勋章", "progress": 241},   # 不在配对表里
        ],
        "unavailable": "",
    }
    svc = {"activity_svc": service, "activity_counters_svc": counters_svc}

    response = await branches.stats_response(svc, PLAYER_NAME, None, "", "career")

    assert response["ok"] is True
    assert [row["metric_hash"] for row in response["data"]["game_counters"]] == [811894228]
    gap = [w for w in response["warnings"] if "差" in w]
    assert gap and "124495" in gap[0] and "78864" in gap[0] and "45631" in gap[0], gap
