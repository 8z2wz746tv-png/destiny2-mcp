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
async def test_historical_stats_reads_real_all_time_sections(dependencies) -> None:
    bungie, manifest, resolver = dependencies
    bungie.get_historical_stats.return_value = {
        "ErrorCode": 1,
        "Response": {
            "allPvE": {
                "allTime": {
                    "kills": {
                        "statId": "kills",
                        "basic": {"displayValue": "88,000"},
                    },
                    "secondsPlayed": {
                        "statId": "secondsPlayed",
                        "basic": {"displayValue": "32d 4h"},
                    },
                }
            },
            "allPvP": {
                "allTime": {
                    "killsDeathsRatio": {
                        "statId": "killsDeathsRatio",
                        "basic": {"displayValue": "1.42"},
                    }
                }
            },
        },
    }
    service = ActivityService(bungie, manifest, resolver)

    result = await service.get_historical_stats(PLAYER_NAME, character="hunter")

    assert result == {
        "pve": {"kills": "88,000", "secondsPlayed": "32d 4h"},
        "pvp": {"killsDeathsRatio": "1.42"},
    }


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
