"""纯 PvP 武器榜（`intent="pvp_weapons"`）的守门测试。

钉住四件容易悄悄错的事：

1. **只统计自己那一行** —— PGCR 的 `extended.weapons` 是按玩家分行的，
   取 `entries[0]` 会把别人的枪算到你头上（第一版真机探针就这么错过）；
2. **窗口必须标出来** —— 这是"最近 N 场"，不是生涯（上游给不了生涯口径，见
   `docs/plans/PVP_WEAPON_BOARD_PLAN.md`）；
3. **PGCR 落盘缓存** —— 结算不可变，第二次问同一批场次不该再打上游；
4. **失败要如实** —— 单场失败记进 `matches_failed`，一场都没有就报错，不返回空榜单当答案。

上游响应形状取自 2026-09-18 真机实测（`uniqueWeaponKills` / `uniqueWeaponPrecisionKills`）。
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from destiny_mcp.exceptions import APIError, InvalidArgumentError
from destiny_mcp.data import activity_modes
from destiny_mcp.exceptions import CharacterNotFoundError
from destiny_mcp.services import pvp_weapon_service as app
from destiny_mcp.services.pgcr_cache import PgcrCache
from destiny_mcp.services.pvp_weapon_service import PvpWeaponService

PLAYER = "TestGuardian#1234"
MID = "4611686018000000001"
WARLOCK_ID = "2305843009000000001"
HUNTER_ID = "2305843009000000002"

# 两把枪：A 是"别人的"（击杀更多），B 是自己的。哈希随便取，名字由替身 Manifest 给。
OTHER_WEAPON = 111111111
MY_WEAPON = 222222222


def _history(instance_id: str, period: str, mode: int = 73) -> dict:
    return {
        "period": period,
        "activityDetails": {"instanceId": instance_id, "mode": mode, "referenceId": 1},
        "values": {},
    }


def _pgcr(entries: list[dict]) -> dict:
    return {"ErrorCode": 1, "Response": {"entries": entries}}


def _entry(character_id: str, weapons: list[tuple[int, int, int]]) -> dict:
    return {
        "characterId": character_id,
        "player": {"destinyUserInfo": {"bungieGlobalDisplayName": "TestGuardian"}},
        "extended": {
            "weapons": [
                {
                    "referenceId": item_hash,
                    "values": {
                        "uniqueWeaponKills": {"basic": {"value": float(kills)}},
                        "uniqueWeaponPrecisionKills": {"basic": {"value": float(precision)}},
                    },
                }
                for item_hash, kills, precision in weapons
            ]
        },
    }


def _manifest() -> MagicMock:
    manifest = MagicMock()
    names = {MY_WEAPON: "我的枪", OTHER_WEAPON: "别人的枪"}
    manifest.get_item_name.side_effect = lambda h: names.get(h, f"#{h}")
    manifest.get_item_info.side_effect = lambda h: {"icon": "/common/x.png"} if h in names else None
    # 模式名走 Manifest（词表已合一）：替身按 modeType 给官方中文名。
    manifest.get_activity_mode_name.side_effect = lambda mode_type: {
        5: "熔炉竞技场", 19: "铁旗", 43: "铁旗占领模式", 63: "智谋",
        69: "多人竞技PvP", 73: "占领模式：快速游戏",
        84: "奥斯里斯试炼",
    }.get(mode_type, "")
    return manifest


@pytest.fixture
def service(tmp_path: Path) -> PvpWeaponService:
    bungie = AsyncMock()
    resolver = AsyncMock()
    resolver.resolve_player.return_value = {"membership_id": MID, "membership_type": 3}
    resolver.get_profile.return_value = {
        "characters": {"data": {WARLOCK_ID: {"classType": 2}}}
    }
    bungie.get_activity_history.return_value = {
        "ErrorCode": 1,
        "Response": {
            "activities": [
                _history("1001", "2026-08-26T18:06:35Z"),
                _history("1002", "2026-08-26T17:00:00Z", mode=43),
            ]
        },
    }
    return PvpWeaponService(bungie, _manifest(), resolver, cache=PgcrCache(tmp_path))


@pytest.mark.asyncio
async def test_only_my_own_row_is_counted(service: PvpWeaponService) -> None:
    """PGCR 里别人的武器击杀再多，也不能进我的榜。"""
    service._bungie.get_pgcr.side_effect = lambda instance_id: _pgcr([
        _entry("9999999999999999999", [(OTHER_WEAPON, 40, 40)]),   # 别人（排在第一行）
        _entry(WARLOCK_ID, [(MY_WEAPON, 7, 3)]),                  # 我
    ])

    result = await service.get_pvp_weapon_board(PLAYER, mode="pvp", matches=10)

    weapons = {row["item_hash"]: row for row in result["weapons"]}
    assert set(weapons) == {MY_WEAPON}, weapons
    assert weapons[MY_WEAPON]["kills"] == 14        # 两场 × 7
    assert weapons[MY_WEAPON]["precision_kills"] == 6
    assert weapons[MY_WEAPON]["matches_with_kills"] == 2


@pytest.mark.asyncio
async def test_window_and_scope_are_always_reported(service: PvpWeaponService) -> None:
    """"最近 N 场"必须自带窗口与口径：它是窗口，不是生涯。"""
    service._bungie.get_pgcr.side_effect = lambda instance_id: _pgcr([
        _entry(WARLOCK_ID, [(MY_WEAPON, 5, 1)]),
    ])

    result = await service.get_pvp_weapon_board(PLAYER, mode="pvp", matches=10)

    assert result["scope"] == "pvp_recent"
    assert result["source"] == "pgcr_aggregation"
    window = result["window"]
    assert window["matches_analyzed"] == 2 and window["matches_requested"] == 10
    assert window["oldest"] == "2026-08-26T17:00:00Z"
    assert window["newest"] == "2026-08-26T18:06:35Z"
    assert window["matches_failed"] == 0
    # 子模式的官方名（伞形过滤、回报具体模式）
    assert {row["name"] for row in result["mode_tally"]} == {
        "占领模式：快速游戏", "铁旗占领模式",
    }
    assert any("最近" in warning and "不是生涯" in warning for warning in result["warnings"])
    assert result["mode_group"]["is_pvp_only"] is True


@pytest.mark.asyncio
async def test_second_call_hits_the_pgcr_cache(service: PvpWeaponService) -> None:
    """结算不可变：第二次问同一批场次不再打上游（首次 2 场，第二次 0 场）。"""
    service._bungie.get_pgcr.side_effect = lambda instance_id: _pgcr([
        _entry(WARLOCK_ID, [(MY_WEAPON, 5, 1)]),
    ])

    await service.get_pvp_weapon_board(PLAYER, mode="pvp", matches=10)
    assert service._bungie.get_pgcr.await_count == 2

    await service.get_pvp_weapon_board(PLAYER, mode="pvp", matches=10)
    assert service._bungie.get_pgcr.await_count == 2, "第二次应该全部命中缓存"


@pytest.mark.asyncio
async def test_failed_match_is_counted_not_hidden(service: PvpWeaponService) -> None:
    """单场失败：如实记进 matches_failed + warning，其余场次照算。"""
    def pgcr(instance_id: str) -> dict:
        if instance_id == "1001":
            raise APIError("读取对局结算", "上游 500")
        return _pgcr([_entry(WARLOCK_ID, [(MY_WEAPON, 3, 1)])])

    service._bungie.get_pgcr.side_effect = pgcr

    result = await service.get_pvp_weapon_board(PLAYER, mode="pvp", matches=10)

    assert result["window"]["matches_analyzed"] == 1
    assert result["window"]["matches_failed"] == 1
    failed = result["failed_matches"]
    assert failed["total"] == 1 and failed["returned"] == 1 and failed["truncated"] is False
    assert failed["items"][0]["instance_id"] == "1001"
    assert any("没取到" in warning for warning in result["warnings"])
    assert result["weapons"][0]["kills"] == 3


@pytest.mark.asyncio
async def test_no_matches_at_all_is_an_error_not_an_empty_board(
    service: PvpWeaponService,
) -> None:
    """"一场都没有"必须是失败：返回空榜单会让人以为"我最近一把 PvP 没打"。"""
    service._bungie.get_activity_history.return_value = {
        "ErrorCode": 1, "Response": {"activities": []},
    }

    with pytest.raises(APIError) as info:
        await service.get_pvp_weapon_board(PLAYER, mode="trials")

    assert "奥斯里斯试炼" in str(info.value)
    service._bungie.get_pgcr.assert_not_awaited()


@pytest.mark.asyncio
async def test_out_of_vocabulary_mode_is_rejected(service: PvpWeaponService) -> None:
    with pytest.raises(InvalidArgumentError, match="pvp_weapons 不支持 mode"):
        await service.get_pvp_weapon_board(PLAYER, mode="猛攻")
    service._bungie.get_activity_history.assert_not_awaited()


@pytest.mark.asyncio
async def test_matches_is_clamped_and_history_uses_the_singular_mode_parameter(
    service: PvpWeaponService,
) -> None:
    """`matches` 上限 100；历史必须用单数 `mode=`（复数会被上游静默忽略）。"""
    service._bungie.get_pgcr.side_effect = lambda instance_id: _pgcr([
        _entry(WARLOCK_ID, [(MY_WEAPON, 1, 0)]),
    ])

    result = await service.get_pvp_weapon_board(PLAYER, mode="gambit", matches=999)

    # 调用方要的值原样保留，实际用多少场另有一个字段，并且带 warning（以前 requested 被改写）。
    assert result["window"]["matches_requested"] == 999
    assert result["window"]["matches_planned"] == 100
    assert any("上限 100 场" in warning for warning in result["warnings"])
    params = service._bungie.get_activity_history.await_args.kwargs["params"]
    assert params["mode"] == "63" and "modes" not in params
    # 智谋是 PvPvE（category=3）：榜单要如实标出来，不能冒充纯 PvP。
    assert result["mode_group"]["is_pvp_only"] is False


def test_cache_ignores_corrupt_files(tmp_path: Path) -> None:
    """缓存坏掉只等于没缓存，不许让查询失败。"""
    cache = PgcrCache(tmp_path)
    cache.path_for("1001").write_text("{ 不是 JSON", encoding="utf-8")
    assert cache.get("1001") is None

    cache.put("1002", {"entries": []})
    assert cache.get("1002") == {"entries": []}
    assert json.loads(cache.path_for("1002").read_text(encoding="utf-8")) == {"entries": []}


@pytest.mark.asyncio
async def test_pve_mode_word_is_rejected(service: PvpWeaponService) -> None:
    """PvE 模式词必须报错。

    真机踩过：`mode="raid"` 被接受，回包却自称 `scope="pvp_recent"`，
    把一场 392 杀的突袭列成"纯 PvP 武器榜" —— 贴错标签比报错糟得多。
    """
    with pytest.raises(InvalidArgumentError) as info:
        await service.get_pvp_weapon_board(PLAYER, mode="raid")

    message = str(info.value)
    assert "crucible" in message and "trials" in message
    assert "raid" not in message.split("可取")[1], "PvE 模式词不该出现在可用词表里"
    service._bungie.get_activity_history.assert_not_awaited()


@pytest.mark.asyncio
async def test_gambit_is_allowed_but_marked_pvpve(service: PvpWeaponService) -> None:
    """智谋是 PvPvE：可以查，但要如实标 `is_pvp_only=false`。"""
    service._bungie.get_pgcr.side_effect = lambda instance_id: _pgcr([
        _entry(WARLOCK_ID, [(MY_WEAPON, 4, 1)]),
    ])

    result = await service.get_pvp_weapon_board(PLAYER, mode="gambit", matches=2)

    assert result["mode_group"]["key"] == "gambit"
    assert result["mode_group"]["is_pvp_only"] is False
    assert set(result["mode_group"]["available_modes"]) == {
        "crucible", "iron_banner", "competitive", "trials", "gambit",
    }


@pytest.mark.asyncio
async def test_available_modes_matches_what_is_accepted(service: PvpWeaponService) -> None:
    """载荷里列出的可用词必须就是真正认的词（文档承诺 5 个，代码曾认 14 个）。"""
    service._bungie.get_pgcr.side_effect = lambda instance_id: _pgcr([
        _entry(WARLOCK_ID, [(MY_WEAPON, 1, 0)]),
    ])

    result = await service.get_pvp_weapon_board(PLAYER, mode="pvp", matches=1)

    accepted = set(result["mode_group"]["available_modes"])
    for word in accepted:
        assert activity_modes.resolve(word) is not None, word
    assert accepted == set(app.BOARD_KEYS)


@pytest.mark.asyncio
async def test_row_without_character_id_falls_back_to_membership(
    service: PvpWeaponService,
) -> None:
    """上游偶尔不给 `characterId`：退一步按 membershipId 找，不能整场丢击杀。"""
    service._bungie.get_pgcr.side_effect = lambda instance_id: _pgcr([
        {   # 没有 characterId，只有 membership
            "player": {"destinyUserInfo": {"membershipId": MID}},
            "extended": {"weapons": [{
                "referenceId": MY_WEAPON,
                "values": {"uniqueWeaponKills": {"basic": {"value": 6.0}}},
            }]},
        },
    ])

    result = await service.get_pvp_weapon_board(PLAYER, mode="pvp", matches=2)

    assert result["weapons"][0]["kills"] == 12          # 两场 × 6
    assert result["window"]["matches_without_your_row"] == 0
    assert not any("找不到你的那一行" in warning for warning in result["warnings"])


@pytest.mark.asyncio
async def test_match_without_your_row_is_reported_not_silent(
    service: PvpWeaponService,
) -> None:
    """两条路都找不到你那一行：如实计数 + warning，不许呈现成"这场没杀到人"。"""
    service._bungie.get_pgcr.side_effect = lambda instance_id: _pgcr([
        {"player": {"destinyUserInfo": {"membershipId": "9999999999999999999"}},
         "extended": {"weapons": [{"referenceId": OTHER_WEAPON,
                                   "values": {"uniqueWeaponKills": {"basic": {"value": 9.0}}}}]}},
    ])

    result = await service.get_pvp_weapon_board(PLAYER, mode="pvp", matches=2)

    assert result["window"]["matches_without_your_row"] == 2
    assert result["weapons"] == []
    assert any("找不到你的那一行" in warning for warning in result["warnings"])


@pytest.mark.asyncio
async def test_missing_mode_names_are_reported(service: PvpWeaponService) -> None:
    """Manifest 取不到模式名时：降级成"模式<号>"但必须留痕（否则界面突然全是模式43）。"""
    service._manifest.get_activity_mode_name.side_effect = lambda mode_type: ""
    service._bungie.get_pgcr.side_effect = lambda instance_id: _pgcr([
        _entry(WARLOCK_ID, [(MY_WEAPON, 2, 0)]),
    ])

    result = await service.get_pvp_weapon_board(PLAYER, mode="pvp", matches=1)

    assert result["mode_tally"][0]["name"] == "模式73"
    assert any("取不到名字" in warning for warning in result["warnings"])


@pytest.mark.asyncio
async def test_characters_and_class_names_are_labelled(service: PvpWeaponService) -> None:
    """职业名与角色归属要落在载荷里（classType 2 = Warlock）。"""
    service._bungie.get_pgcr.side_effect = lambda instance_id: _pgcr([
        _entry(WARLOCK_ID, [(MY_WEAPON, 3, 1)]),
    ])

    result = await service.get_pvp_weapon_board(PLAYER, mode="pvp", matches=2)

    assert result["characters"] == [{
        "character_id": WARLOCK_ID, "class": "Warlock",
        "history_count": 2, "page_full": False,
    }]
    assert result["weapons"][0]["characters"] == ["Warlock"]


@pytest.mark.asyncio
async def test_character_filter_narrows_and_reports_missing_class(
    service: PvpWeaponService,
) -> None:
    """`character=` 只查那个角色；账号里没有这个职业要报错（不是静默查全部）。"""
    service._bungie.get_pgcr.side_effect = lambda instance_id: _pgcr([
        _entry(WARLOCK_ID, [(MY_WEAPON, 1, 0)]),
    ])

    result = await service.get_pvp_weapon_board(PLAYER, character="warlock", mode="pvp", matches=1)
    assert [row["class"] for row in result["characters"]] == ["Warlock"]

    with pytest.raises(CharacterNotFoundError):
        await service.get_pvp_weapon_board(PLAYER, character="titan", mode="pvp", matches=1)


@pytest.mark.asyncio
async def test_cache_rotation_keeps_the_newest(service: PvpWeaponService) -> None:
    """缓存按条数轮换：只保留最新 N 条（默认 2000，这里缩小验证机制）。"""
    cache = service._cache
    for index in range(5):
        cache.put(f"90{index}", {"entries": []})

    assert cache.prune(keep=2) == 3
    assert len(list(cache.directory.glob("*.json"))) == 2


@pytest.mark.asyncio
async def test_failed_matches_self_reports_completeness(service: PvpWeaponService) -> None:
    """失败场次详情按仓库惯例自证全量：`len(items)` 不是总数，`total` 才是。

    13 场里 12 场失败、1 场成功 —— 样本截到 10 条，但 `total` 必须说 12。
    """
    activities = [_history(f"20{index:02d}", f"2026-08-2{index % 9}T10:00:00Z") for index in range(13)]
    service._bungie.get_activity_history.return_value = {
        "ErrorCode": 1, "Response": {"activities": activities},
    }

    def pgcr(instance_id: str) -> dict:
        if instance_id != "2000":
            raise APIError("读取对局结算", "上游 500")
        return _pgcr([_entry(WARLOCK_ID, [(MY_WEAPON, 5, 2)])])

    service._bungie.get_pgcr.side_effect = pgcr

    result = await service.get_pvp_weapon_board(PLAYER, mode="pvp", matches=13)

    failed = result["failed_matches"]
    assert failed["total"] == 12 and failed["returned"] == 10 and failed["truncated"] is True
    assert len(failed["items"]) == 10 == failed["returned"]
    assert failed["total"] == result["window"]["matches_failed"]
    assert result["window"]["matches_analyzed"] == 1
