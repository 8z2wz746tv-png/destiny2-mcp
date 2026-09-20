"""Parity regressions for ActivityService Bungie response handling."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from destiny_mcp.exceptions import APIError, CharacterNotFoundError, ConfigError
from destiny_mcp.services.activity_service import ActivityService


PLAYER_NAME = "TestGuardian#1234"
MEMBERSHIP_ID = "4611686018000000001"
HUNTER_ID = "2305843009000000001"


@pytest.fixture
def dependencies() -> tuple[AsyncMock, MagicMock, AsyncMock]:
    bungie = AsyncMock()
    manifest = MagicMock()
    resolver = AsyncMock()
    resolver.resolve_player.return_value = {
        "membership_id": MEMBERSHIP_ID,
        "membership_type": 3,
    }
    resolver.get_profile.return_value = {
        "characters": {
            "data": {
                HUNTER_ID: {
                    "classType": 1,
                    "light": 1830,
                }
            }
        }
    }
    return bungie, manifest, resolver


@pytest.mark.asyncio
async def test_history_rejects_class_missing_from_account(dependencies) -> None:
    bungie, manifest, resolver = dependencies
    service = ActivityService(bungie, manifest, resolver)

    with pytest.raises(CharacterNotFoundError) as exc_info:
        await service.get_activity_history(PLAYER_NAME, character="titan")

    assert exc_info.value.character_name == "titan"
    assert exc_info.value.available == ["Hunter"]
    bungie.get_activity_history.assert_not_awaited()


@pytest.mark.asyncio
async def test_history_rejects_unknown_mode_instead_of_ignoring_it(dependencies) -> None:
    bungie, manifest, resolver = dependencies
    service = ActivityService(bungie, manifest, resolver)

    with pytest.raises(ConfigError, match="不支持活动模式"):
        await service.get_activity_history(PLAYER_NAME, mode="raidd")

    bungie.get_activity_history.assert_not_awaited()


@pytest.mark.asyncio
async def test_history_rejects_partial_character_failure(dependencies) -> None:
    bungie, manifest, resolver = dependencies
    resolver.get_profile.return_value["characters"]["data"]["second"] = {
        "classType": 2
    }
    bungie.get_activity_history.side_effect = [
        {"ErrorCode": 1, "Response": {"activities": []}},
        {"ErrorCode": 5, "Message": "temporary failure"},
    ]
    service = ActivityService(bungie, manifest, resolver)

    with pytest.raises(APIError, match="temporary failure"):
        await service.get_activity_history(PLAYER_NAME)


@pytest.mark.asyncio
async def test_single_character_stats_rejects_class_missing_from_account(
    dependencies,
) -> None:
    bungie, manifest, resolver = dependencies
    service = ActivityService(bungie, manifest, resolver)

    with pytest.raises(CharacterNotFoundError) as exc_info:
        await service.get_historical_stats(PLAYER_NAME, character="titan")

    assert exc_info.value.character_name == "titan"
    assert exc_info.value.available == ["Hunter"]
    bungie.get_historical_stats.assert_not_awaited()


@pytest.mark.asyncio
async def test_historical_stats_defaults_to_the_account_endpoint(dependencies) -> None:
    """P1：不传 character 时走账号级接口，**不再**拿第一个角色的数字冒充生涯。"""
    bungie, manifest, resolver = dependencies
    bungie.get_historical_stats_for_account.return_value = {
        "ErrorCode": 1,
        "Response": {
            "mergedAllCharacters": {"results": {"allPvP": {"allTime": {}}}},
            "mergedDeletedCharacters": {"results": {}},
            "characters": [],
        },
    }
    service = ActivityService(bungie, manifest, resolver)

    result = await service.get_historical_stats(PLAYER_NAME)

    assert result["scope"] == "account"
    assert result["source"] == "GetHistoricalStatsForAccount"
    assert result["characters"] == {"existing": 0, "deleted": 0, "total": 0}
    assert set(result["tiers"]) == {"existing", "deleted", "account_total"}
    bungie.get_historical_stats_for_account.assert_awaited_once()
    bungie.get_historical_stats.assert_not_awaited(), "默认不该碰单角色接口"


@pytest.mark.asyncio
async def test_historical_stats_reads_real_all_time_sections(dependencies) -> None:
    bungie, manifest, resolver = dependencies
    bungie.get_historical_stats.return_value = {
        "ErrorCode": 1,
        "Response": {
            "allPvE": {
                "allTime": {
                    "kills": {
                        "statId": "kills",
                        "basic": {"value": 88000, "displayValue": "88,000"},
                    },
                    "secondsPlayed": {
                        "statId": "secondsPlayed",
                        "basic": {"value": 2779200, "displayValue": "32d 4h"},
                    },
                }
            },
            "allPvP": {
                "allTime": {
                    "killsDeathsRatio": {
                        "statId": "killsDeathsRatio",
                        "basic": {"value": 1.42, "displayValue": "1.42"},
                    }
                }
            },
        },
    }
    service = ActivityService(bungie, manifest, resolver)

    result = await service.get_historical_stats(PLAYER_NAME, character="hunter")

    # 0.2.0 起是行式：全量给项、带原始数值与中文名（旧版是手写键 + 只有 displayValue 的字典）。
    # P1 起单角色路径必须自报家门：scope=character + 角色名，行里只有 value（这个角色自己的数）。
    assert result["schema_version"] == 2
    assert result["scope"] == "character"
    assert result["source"] == "GetHistoricalStats"
    assert result["character"]["name"] == "Hunter"
    groups = {group["key"]: group for group in result["groups"]}
    assert set(groups) == {"pve", "pvp"}
    pve = {row["stat_id"]: row for row in groups["pve"]["stats"]}
    assert pve["kills"]["value"] == 88000
    assert pve["kills"]["display"] == "88,000"
    assert pve["kills"]["name"] == "击杀"
    assert "account_total" not in pve["kills"], "单角色行不给三档，免得被当成生涯"
    assert pve["seconds_played"]["unit"] == "seconds"
    assert groups["pve"]["stat_count"] == 2
    pvp = {row["stat_id"]: row for row in groups["pvp"]["stats"]}
    assert pvp["kills_deaths_ratio"]["display"] == "1.42"


@pytest.mark.asyncio
async def test_weapon_history_includes_manifest_icon_url(dependencies) -> None:
    bungie, manifest, resolver = dependencies
    bungie.get_unique_weapon_history.return_value = {
        "ErrorCode": 1,
        "Response": {
            "weapons": [
                {
                    "referenceId": 999999999,
                    "values": {
                        "uniqueWeaponKills": {
                            "basic": {"value": 3025, "displayValue": "3025"}
                        }
                    },
                },
                {
                    "referenceId": 123,
                    "values": {
                        "uniqueWeaponKills": {
                            "basic": {"value": 1, "displayValue": "1"}
                        }
                    },
                },
            ]
        },
    }
    manifest.get_item_name.side_effect = lambda item_hash: f"Weapon {item_hash}"
    manifest.get_item_info.side_effect = lambda item_hash: (
        {"icon": "/common/destiny2_content/icons/gjallarhorn.jpg"}
        if item_hash == 999999999
        else {"icon": "not-a-destiny-icon"}
    )
    service = ActivityService(bungie, manifest, resolver)

    result = await service.get_unique_weapon_history(
        PLAYER_NAME,
        character="hunter",
    )

    assert result["weapons"][0]["icon_url"] == (
        "https://www.bungie.net/common/destiny2_content/icons/gjallarhorn.jpg"
    )
    assert result["weapons"][1]["icon_url"] == ""
    # P3b：上游 `GetUniqueWeaponHistory` 没有模式参数 → 必带 all_modes 与出处，
    # 话术直说"这不是 PvP 榜"（榜首通常是刷本用的枪）。
    assert result["scope"] == "all_modes"
    assert result["source"] == "GetUniqueWeaponHistory"
    assert "不是 PvP 榜" in result["message"]


@pytest.mark.asyncio
async def test_aggregate_activity_stats_reads_bungie_activity_stat_keys(
    dependencies,
) -> None:
    bungie, manifest, resolver = dependencies
    bungie.get_destiny_aggregate_activity_stats.return_value = {
        "ErrorCode": 1,
        "Response": {
            "activities": [
                {
                    "activityHash": 123,
                    "values": {
                        "activityCompletions": {
                            "basic": {"value": 42, "displayValue": "42"}
                        },
                        "activityKills": {
                            "basic": {"value": 9000, "displayValue": "9,000"}
                        },
                        "activitySecondsPlayed": {
                            "basic": {"value": 7380, "displayValue": "2h 3m"}
                        },
                    },
                }
            ]
        },
    }
    manifest.get_activity_name.return_value = "深岩墓室"
    service = ActivityService(bungie, manifest, resolver)

    result = await service.get_aggregate_activity_stats(
        PLAYER_NAME,
        character="hunter",
    )

    assert result["activities"][0]["kills"] == 9000
    assert result["activities"][0]["kills_display"] == "9,000"
    assert result["activities"][0]["seconds_played"] == 7380
    assert result["activities"][0]["seconds_played_display"] == "2h 3m"


@pytest.mark.asyncio
async def test_history_fetches_all_characters_in_parallel(dependencies) -> None:
    """三个角色的历史**同时**拉（真机 3.5s → 约 1.5s），拼装顺序不变。

    每个角色的调用故意睡 0.15 秒：串行 ≈ 0.45s，并发 ≈ 0.15s；断言 < 0.3s。
    同时钉住"顺序不变"：返回仍按角色的原顺序拼接。
    """
    import asyncio
    import time

    bungie, manifest, resolver = dependencies
    resolver.get_profile.return_value = {
        "characters": {"data": {
            "c1": {"classType": 1}, "c2": {"classType": 2}, "c3": {"classType": 0},
        }}
    }

    async def slow_history(mtype, mid, char_id, params=None):
        await asyncio.sleep(0.15)
        return {"ErrorCode": 1, "Response": {"activities": [{
            "period": "2026-09-01T00:00:00Z",
            "activityDetails": {"instanceId": f"i-{char_id}", "mode": 4, "referenceId": 1},
            "values": {},
        }]}}

    bungie.get_activity_history.side_effect = slow_history
    service = ActivityService(bungie, manifest, resolver)

    started = time.monotonic()
    rows = await service.get_activity_history(PLAYER_NAME, count=5)
    elapsed = time.monotonic() - started

    assert elapsed < 0.3, f"看起来是串行：3 个各 0.15s 的请求花了 {elapsed:.2f}s"
    assert [row["instance_id"] for row in rows] == ["i-c1", "i-c2", "i-c3"]
