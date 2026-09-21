"""周常轮换：替身测试 + 表格锚点回归（不碰真机）。

盯住四件事：

1. **锚点必须跟游戏内轮换页对得上**（用户提供的截图就是夹具）：
   上维挑战 6 周循环、异域任务 7 周循环、泉源每天 攻击/防御 交替；
2. **重置边界**：周二是每周重置、每天 17:00Z 换日（北京时间次日 01:00，所以截图上的
   日期对应前一天的 17:00Z）；
3. **官方 vs 自维护表不许混**：里程碑/组件 204 出来的标 `official`，表算出来的标 `schedule`
   并带 `verified_at`/`verified_against`；
4. **没核对过的不猜**：遗失区域只给候选；上游没给打击名就留空 + 说明；
   空词缀 hash 跳过、奖励数量 0 原样给。
"""

from __future__ import annotations

from datetime import datetime, timezone

from destiny_mcp.data.rotations import wellspring_upcoming
from typing import get_args

import pytest

from destiny_mcp.data import rotations as tables
from destiny_mcp.exceptions import APIError
from destiny_mcp.services.rotation_service import RotationService
from destiny_mcp.tools import _requests, _rotation_branches
from destiny_mcp.tools._param_contracts import PARAMETER_OWNERS

NOW = datetime(2026, 9, 21, 12, 0, tzinfo=timezone.utc)   # 本周内、当日 17:00Z 之前


def _utc(text: str) -> datetime:
    return datetime.fromisoformat(text.replace("Z", "+00:00"))


# ── 表与锚点 ─────────────────────────────────────────────────────────


def test_week_start_is_the_tuesday_reset() -> None:
    assert tables.week_stamp(tables.week_start(NOW)) == "2026-09-15T17:00:00Z"
    # 重置点前后各一秒要落在正确的周
    assert tables.week_stamp(tables.week_start(_utc("2026-09-15T16:59:59Z"))) == "2026-09-08T17:00:00Z"
    assert tables.week_stamp(tables.week_start(_utc("2026-09-15T17:00:01Z"))) == "2026-09-15T17:00:00Z"


def test_day_start_flips_at_17_utc() -> None:
    before = tables.day_start(_utc("2026-09-20T16:59:00Z"))
    after = tables.day_start(_utc("2026-09-20T17:01:00Z"))
    assert tables.week_stamp(before) == "2026-09-19T17:00:00Z"
    assert tables.week_stamp(after) == "2026-09-20T17:00:00Z"


def test_ascendant_challenge_matches_the_in_game_cycle() -> None:
    """夹具＝用户截图：8/12 衔尾蛇 → … → 9/16 辛梅里安卫戍营 → 9/23 衔尾蛇（6 周循环）。"""
    assert tables.ASCENDANT_CHALLENGE.name_at(tables.week_start(NOW)) == "辛梅里安卫戍营"
    assert tables.ASCENDANT_CHALLENGE.name_at(_utc("2026-09-22T17:00:00Z")) == "衔尾蛇"
    assert tables.ASCENDANT_CHALLENGE.name_at(_utc("2026-09-29T17:00:00Z")) == "失却神殿"
    assert tables.ASCENDANT_CHALLENGE.name_at(_utc("2026-08-11T17:00:00Z")) == "衔尾蛇"
    # 6 周之后回到同一项
    assert tables.ASCENDANT_CHALLENGE.name_at(_utc("2026-10-27T17:00:00Z")) == "辛梅里安卫戍营"


def test_exotic_mission_rotation_matches_the_in_game_cycle() -> None:
    """夹具＝用户截图：9/9 安可 → 9/16 凯尔之陨 → … → 10/21 苦命鸳鸯（7 周循环）。"""
    assert tables.EXOTIC_MISSION.name_at(_utc("2026-09-08T17:00:00Z")) == "安可"
    assert tables.EXOTIC_MISSION.name_at(tables.week_start(NOW)) == "凯尔之陨"
    assert tables.EXOTIC_MISSION.name_at(_utc("2026-09-22T17:00:00Z")) == "前兆"
    assert tables.EXOTIC_MISSION.name_at(_utc("2026-10-13T17:00:00Z")) == "//节点.超控.阿瓦隆//"
    assert tables.EXOTIC_MISSION.name_at(_utc("2026-10-20T17:00:00Z")) == "苦命鸳鸯"
    # 7 周之后回到同一项
    assert tables.EXOTIC_MISSION.name_at(_utc("2026-11-03T17:00:00Z")) == "凯尔之陨"


def test_wellspring_alternates_daily() -> None:
    """夹具＝用户截图的日列表：9/16 攻击 → 9/17 防御 → … → 9/22 攻击。"""
    assert tables.wellspring_mode(_utc("2026-09-15T17:00:00Z")) == "攻击"
    assert tables.wellspring_mode(_utc("2026-09-16T17:00:00Z")) == "防御"
    assert tables.wellspring_mode(_utc("2026-09-19T17:00:00Z")) == "攻击"
    assert tables.wellspring_mode(_utc("2026-09-20T17:00:00Z")) == "防御"


def test_weekly_rotations_declare_how_they_were_verified() -> None:
    for rotation in tables.WEEKLY_ROTATIONS:
        assert rotation.verified_at and rotation.verified_against
        assert rotation.anchor_index < len(rotation.candidates)


def test_lost_sector_table_is_the_expert_playlist() -> None:
    """专家列表实测自游戏内截图：27 个地点、按目的地分组；被排除的 4 个要写清理由。"""
    assert tables.LOST_SECTOR_ANCHORED is False
    assert len(tables.LOST_SECTOR_LOCATIONS) == tables.LOST_SECTOR_TOTAL == 27
    assert "空坦克" not in tables.LOST_SECTOR_LOCATIONS, "只有传说/大师、没有专家变体"
    assert not [n for n in tables.LOST_SECTOR_LOCATIONS if n.startswith("消息")], "那不是遗失区域"
    assert tables.LOST_SECTOR_GROUPS[0][0] == "欧洲无人区"


# ── 服务：官方那半 ───────────────────────────────────────────────────
MILESTONES = {
    "3881495763": {"milestoneHash": 3881495763, "order": 8000,
                   "startDate": "2026-09-15T17:00:00Z", "endDate": "2026-09-22T17:00:00Z",
                   "activities": [{"activityHash": 3881495763}]},
    "999": {"milestoneHash": 999, "order": 3000,
            "startDate": "2026-09-15T17:00:00Z", "endDate": "2026-09-22T17:00:00Z",
            "activities": []},                      # 净化/公会那种没有活动的，不该出现在答案里
}
MILESTONE_NAMES = {3881495763: "玻璃拱顶", 999: "周常公会记忆水晶"}
# 活动定义：(名字, 活动类型名)。类型名走 Manifest（实测这一批全是「突袭」，照实给）
ACTIVITY_DEFS = {
    3881495763: ("玻璃拱顶: 标准", "突袭"),
    101: ("切除: 宗师", "剧情"),
    102: ("日落: 大师", "日落"),
    103: ("智谋", "智谋"),
}
CHARACTER_ACTIVITIES = {
    "char1": {"availableActivities": [
        # 有名字的宗师：名字里带打击名 + 难度
        {"activityHash": 101, "recommendedLight": 200, "isCompleted": True,
         "modifierHashes": [0, 201, 202],
         "visibleRewards": [{"rewardItems": [
             {"itemQuantity": {"itemHash": 301, "quantity": 1}},
             {"itemQuantity": {"itemHash": 302, "quantity": 0}},   # 占位 0 要原样给
         ]}]},
        # 上游只给难度的日落：打击名留空
        {"activityHash": 102, "modifierHashes": [201], "visibleRewards": []},
        {"activityHash": 103},                                     # 无关活动，应被忽略
    ]},
    "char2": {"availableActivities": []},
}
MODIFIER_NAMES = {201: "团灭", 202: "勇士敌人", 0: ""}
ITEM_NAMES = {301: "故我在", 302: "上维碎片（普通）"}


class FakeManifest:
    def get_definition(self, table: str, hash_id: int) -> dict | None:
        if table == "DestinyMilestoneDefinition":
            name = MILESTONE_NAMES.get(hash_id)
            return {"displayProperties": {"name": name}} if name else None
        if table == "DestinyActivityDefinition":
            entry = ACTIVITY_DEFS.get(hash_id)
            if not entry:
                return None
            name, type_name = entry
            return {"displayProperties": {"name": name},
                    "activityTypeHash": abs(hash(type_name)) % 1_000_000}
        if table == "DestinyActivityTypeDefinition":
            for _name, type_name in ACTIVITY_DEFS.values():
                if abs(hash(type_name)) % 1_000_000 == hash_id:
                    return {"displayProperties": {"name": type_name}}
            return None
        if table == "DestinyActivityModifierDefinition":
            return {"displayProperties": {"name": MODIFIER_NAMES.get(hash_id, "")}}
        return None

    def get_item_definition(self, item_hash: int) -> dict | None:
        name = ITEM_NAMES.get(item_hash)
        return {"displayProperties": {"name": name}} if name else None


class FakeBungie:
    async def fetch_milestones(self) -> dict:
        return MILESTONES


class FakeResolver:
    def __init__(self, profile: dict | None = None) -> None:
        self._profile = profile if profile is not None else {"characterActivities": {"data": CHARACTER_ACTIVITIES}}

    async def resolve_player(self, player_name: str) -> dict:
        return {"membership_id": "1", "membership_type": 3, "display_name": player_name or "T#1"}

    async def get_profile(self, mid: str, mtype: int, components: list[int]) -> dict:
        return self._profile


def service(profile: dict | None = None) -> RotationService:
    return RotationService(FakeBungie(), FakeManifest(), FakeResolver(profile))  # type: ignore[arg-type]


async def test_featured_rows_are_official_and_skip_activity_less_milestones() -> None:
    result = await service().rotations(limit=50)
    raids = [row for row in result["rows"] if row["kind"] == "raid"]
    names = [row["name"] for row in raids]

    assert names == ["玻璃拱顶"], "没有活动的里程碑（公会记忆水晶）不该出现在轮换表里"
    assert raids[0]["source"] == "official" and raids[0]["activity_type"] == "突袭"
    assert raids[0]["week_of"] == "2026-09-15T17:00:00Z"
    assert result["counts"]["official"] >= 1


async def test_nightfall_row_carries_modifiers_rewards_and_skips_empty_hash() -> None:
    result = await service().rotations(limit=50)
    row = next(r for r in result["rows"] if r["kind"] == "nightfall" and r["strike_known"])

    assert (row["name"], row["difficulty"]) == ("切除", "宗师")
    assert row["modifiers"] == ["团灭", "勇士敌人"], "空 hash 不产出空名字"
    assert [(item["name"], item["quantity"]) for item in row["rewards"]] == [
        ("故我在", 1), ("上维碎片（普通）", 0),
    ], "数量 0 是占位，原样给"
    assert row["source"] == "official"


async def test_nightfall_without_strike_name_keeps_the_difficulty_only() -> None:
    result = await service().rotations(limit=50)
    row = next(r for r in result["rows"] if r["kind"] == "nightfall" and not r["strike_known"])

    assert row["name"] == "" and row["difficulty"] == "大师"
    assert row["upstream_name"] == "日落: 大师"


async def test_nightfall_requires_the_character_activities_component() -> None:
    with pytest.raises(APIError):
        await service({"characterActivities": {"data": {}}}).rotations()


async def test_schedule_rows_carry_verified_metadata() -> None:
    result = await service().rotations(limit=50)
    ascendant = [r for r in result["rows"] if r["kind"] == "ascendant_challenge"]

    assert [r["name"] for r in ascendant] == ["辛梅里安卫戍营", "衔尾蛇"], "本周 + 下周"
    for row in ascendant:
        assert row["source"] == "schedule"
        assert row["verified_at"] == "2026-09-21" and row["verified_against"]
    wellspring = [r for r in result["rows"] if r["kind"] == "wellspring"]
    # 泉源按天在「攻击/防御」之间交替，所以**不能把"今天是哪个"写死** —— 这条断言以前写死成
    # 「防御, 攻击」，2026-09-22 一过零点就红了（判据本身没问题，是断言错了）。
    # 期望值从同一个出处现算：`wellspring_upcoming` 与响应走的是同一个函数。
    expected = [f"泉源：{mode}" for _, mode in wellspring_upcoming(datetime.now(timezone.utc))]
    assert [r["name"] for r in wellspring] == expected, "今天 + 明天"
    assert wellspring[0]["variants"] == ["标准", "专家", "大师"]


async def test_lost_sector_block_is_honest_about_the_missing_anchor() -> None:
    result = await service().rotations(limit=50)

    assert result["lost_sector"]["anchored"] is False
    assert result["lost_sector"]["expert_always_available"] is True
    assert len(result["lost_sector"]["candidates"]) == 27
    assert len(result["lost_sector"]["groups"]) == 9
    assert "核对" in result["lost_sector"]["how_to_anchor"]
    assert not [row for row in result["rows"] if row["kind"] == "lost_sector"], "没锚点就不给行"


# ── 话术与载荷 ───────────────────────────────────────────────────────


async def test_payload_separates_official_from_schedule() -> None:
    payload = _rotation_branches.rotations_payload(await service().rotations(limit=50))
    text = " ".join(payload["warnings"])

    assert payload["ok"] is True
    assert "official" in text and "schedule" in text, "口径必须分开说"
    assert "遗失区域" in text and "不给" in text
    assert payload["data"]["counts"]["official"] + payload["data"]["counts"]["schedule"] == \
        payload["data"]["counts"]["total"]
    assert payload["summary"].startswith("特色突袭/地牢 玻璃拱顶")
    assert "夜幕/宗师 切除（宗师）" in payload["summary"]
    assert "上维挑战 辛梅里安卫戍营" in payload["summary"]


async def test_payload_flags_uninterpolated_variables() -> None:
    """词缀里出现 `{var:...}` 时只提醒、不插值——这里直接注入一条带变量的词缀来验。"""
    svc = service()
    original = _rotation_branches.rotations_payload

    result = await svc.rotations(limit=50)
    result["rows"][1]["modifiers"] = ["过充追踪步枪 {var:1027206613}%"]
    payload = original(result)

    assert any("{var:" in warning for warning in payload["warnings"])
    assert payload["data"]["rows"][1]["modifiers"] == ["过充追踪步枪 {var:1027206613}%"]


def test_intent_and_parameter_ownership() -> None:
    assert set(_requests.WORLD_ROTATION_INTENTS) <= set(get_args(_requests.WorldIntent))
    for parameter in ("player_name", "limit"):
        owned = PARAMETER_OWNERS[("world_assistant", parameter)].intents
        assert set(_requests.WORLD_ROTATION_INTENTS) <= owned, parameter
    assert not set(_requests.WORLD_ROTATION_INTENTS) & PARAMETER_OWNERS[
        ("world_assistant", "vendor_name")
    ].intents
