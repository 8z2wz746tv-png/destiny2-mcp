"""活动统计的唯一样式：上游 `statId` → 行式统计（数值 + 显示值 + 中文名 + 分组）。

为什么要有这个模块：以前 `activity_service` 手写 8 个键、只取 `displayValue`。真机抓下来
上游 `allPvE` 有 **65 项**（`allPvP` 66 项），我们实际只拿到 6 项，而且

- `precisionkills` 拼错了（上游是 `precisionKills`）→ 精准击杀静默消失；
- `activitiesWon` 在 PvE 里本来不存在 → 又少一项；
- `basic.value`（原始数值）与 `pga`（场均）被整个丢掉 → 想做"折合多少小时""同比"这类计算
  只能去解析 `"42d 11h"` 这种人读字符串。

三条规矩：

1. **全量**：上游给多少项就出多少项。没登记中文名的也给行（`name` 留空、`group="other"`），
   **不允许静默少一项**；
2. **行式**：每项 `{stat_id, upstream_id, name, group, value, display, unit?, per_game?}`，
   与武器属性 `{stat_hash, name, value, display}` 同一套写法；
3. **单一出处**：中文名/单位/分组只在这张表里；`tests/test_activity_stats.py` 拿真机抓到的
   键清单当夹具，表里拼错、或上游新增了没登记的项，测试会直接指出来。
"""

from __future__ import annotations

from typing import Any, Mapping

SCHEMA_VERSION = 1

# 分组顺序：常用的排前面（Agent 从前往后读就够了）
GROUP_ORDER: tuple[str, ...] = ("core", "activities", "objectives", "averages", "weapons", "other")
GROUP_LABELS_ZH: dict[str, str] = {
    "core": "核心",
    "activities": "活动类型",
    "objectives": "目标与事件",
    "averages": "距离与场均",
    "weapons": "武器击杀细分",
    "other": "其它",
}

# 武器击杀细分的后缀 → 中文（`weaponKillsAutoRifle` → 自动步枪击杀）
_WEAPON_KILL_NAMES: dict[str, str] = {
    "Ability": "技能",
    "AutoRifle": "自动步枪",
    "BeamRifle": "线性融合步枪",
    "Bow": "弓",
    "FusionRifle": "融合步枪",
    "Glaive": "长刃",
    "Grenade": "手雷",
    "GrenadeLauncher": "榴弹发射器",
    "HandCannon": "手炮",
    "MachineGun": "机枪",
    "Melee": "近战",
    "PulseRifle": "脉冲步枪",
    "Relic": "遗物",
    "RocketLauncher": "火箭筒",
    "ScoutRifle": "斥候步枪",
    "Shotgun": "霰弹枪",
    "SideArm": "手枪",
    "Sniper": "狙击步枪",
    "Submachinegun": "微型冲锋枪",
    "Super": "超能",
    "Sword": "刀剑",
    "TraceRifle": "追踪步枪",
}

# upstream_id → (中文名, 分组, 单位)；单位 "" 表示无单位，"seconds"/"ms" 表示可换算
_TABLE: dict[str, tuple[str, str, str]] = {
    # 核心
    "activitiesEntered": ("活动场次", "core", ""),
    "activitiesWon": ("活动胜场", "core", ""),
    "activitiesCleared": ("活动通关", "core", ""),
    "kills": ("击杀", "core", ""),
    "deaths": ("死亡", "core", ""),
    "assists": ("助攻", "core", ""),
    "killsDeathsAssists": ("击杀+助攻", "core", ""),
    "killsDeathsRatio": ("击杀/死亡比", "core", ""),
    "efficiency": ("效率", "core", ""),
    "precisionKills": ("精准击杀", "core", ""),
    "mostPrecisionKills": ("单场最多精准击杀", "core", ""),
    "bestSingleGameKills": ("单场最多击杀", "core", ""),
    "bestSingleGameScore": ("单场最高得分", "core", ""),
    "score": ("总得分", "core", ""),
    "teamScore": ("队伍得分", "core", ""),
    "secondsPlayed": ("游戏时长", "core", "seconds"),
    "totalActivityDurationSeconds": ("活动总时长", "core", "seconds"),
    "suicides": ("自杀", "core", ""),
    "opponentsDefeated": ("击败对手", "core", ""),
    "resurrectionsPerformed": ("救援次数", "core", ""),
    "resurrectionsReceived": ("被救次数", "core", ""),
    "longestKillSpree": ("最长连杀", "core", ""),
    "longestSingleLife": ("最长单次存活", "core", "seconds"),
    "averageLifespan": ("平均寿命", "core", "seconds"),
    "highestLightLevel": ("最高光等", "core", ""),
    "highestCharacterLevel": ("最高角色等级", "core", ""),
    "combatRating": ("战斗等级", "core", ""),
    "winLossRatio": ("胜负比", "core", ""),
    # 活动类型
    "adventuresCompleted": ("冒险完成", "activities", ""),
    "heroicPublicEventsCompleted": ("英雄公共事件完成", "activities", ""),
    "publicEventsCompleted": ("公共事件完成", "activities", ""),
    "fireTeamActivities": ("组队活动", "activities", ""),
    "fastestCompletionMs": ("最快通关", "activities", "ms"),
    "remainingTimeAfterQuitSeconds": ("退出时剩余时间", "activities", "seconds"),
    # 目标与事件
    "objectivesCompleted": ("目标完成", "objectives", ""),
    "orbsDropped": ("掉落光球", "objectives", ""),
    "orbsGathered": ("拾取光球", "objectives", ""),
    "allParticipantsCount": ("参与者人次", "objectives", ""),
    "allParticipantsScore": ("参与者总得分", "objectives", ""),
    "allParticipantsTimePlayed": ("参与者总时长", "objectives", "seconds"),
    # 距离与场均
    "averageKillDistance": ("平均击杀距离", "averages", ""),
    "averageDeathDistance": ("平均死亡距离", "averages", ""),
    "totalKillDistance": ("总击杀距离", "averages", ""),
    "totalDeathDistance": ("总死亡距离", "averages", ""),
    "longestKillDistance": ("最远击杀距离", "averages", ""),
    "averageScorePerKill": ("每次击杀平均得分", "averages", ""),
    "averageScorePerLife": ("每条命平均得分", "averages", ""),
    # 武器
    "weaponBestType": ("最常用武器类型", "weapons", ""),
}


def _snake(upstream_id: str) -> str:
    """`secondsPlayed` → `seconds_played`（机械转换，稳定可预测）。"""
    out: list[str] = []
    for index, char in enumerate(upstream_id):
        if char.isupper() and index and not upstream_id[index - 1].isupper():
            out.append("_")
        out.append(char.lower())
    return "".join(out)


def _label(upstream_id: str) -> tuple[str, str, str]:
    known = _TABLE.get(upstream_id)
    if known is not None:
        return known
    if upstream_id.startswith("weaponKills"):
        suffix = upstream_id[len("weaponKills"):]
        name = _WEAPON_KILL_NAMES.get(suffix)
        if name:
            return f"{name}击杀", "weapons", ""
    # 没登记：照样出行，名字留空（不编、也不丢）
    return "", "other", ""


def _number(raw: Any) -> int | float | None:
    """原始数值：整数就还原成 int（3698.0 → 3698），没有就是 None（不编 0）。"""
    if isinstance(raw, bool) or not isinstance(raw, (int, float)):
        return None
    return int(raw) if float(raw).is_integer() else round(float(raw), 4)


def stat_row(upstream_id: str, value: Mapping[str, Any] | None) -> dict[str, Any]:
    """一条上游统计 → 一行。`value` 就是上游那个 `{statId, basic, pga}` 字典。"""
    name, group, unit = _label(upstream_id)
    basic = (value or {}).get("basic") or {}
    per_game = (value or {}).get("pga") or {}
    row: dict[str, Any] = {
        "stat_id": _snake(upstream_id),
        "upstream_id": upstream_id,
        "name": name,
        "group": group,
        "value": _number(basic.get("value")),
        "display": str(basic.get("displayValue", "")),
    }
    if unit:
        row["unit"] = unit
    per_game_value = _number(per_game.get("value"))
    if per_game_value is not None:
        row["per_game"] = {
            "value": per_game_value,
            "display": str(per_game.get("displayValue", "")),
        }
    return row


def stat_rows(section: Mapping[str, Any] | None) -> list[dict[str, Any]]:
    """一段上游统计（如 `allPvE.allTime`）→ 行列表，按分组与常用度排序。"""
    if not isinstance(section, Mapping):
        return []
    rows = [
        stat_row(str(key), value if isinstance(value, Mapping) else None)
        for key, value in section.items()
    ]
    rows.sort(
        key=lambda row: (
            GROUP_ORDER.index(row["group"]),
            -int(row["value"] or 0) if row["group"] == "core" else 0,
            row["upstream_id"],
        )
    )
    return rows


def stat_groups(sections: Mapping[str, Mapping[str, Any]]) -> dict[str, Any]:
    """`{"pve": allTimePvE, "pvp": allTimePvP}` → 统一的分组结构。"""
    groups = []
    for key, section in sections.items():
        rows = stat_rows(section)
        if not rows:
            continue
        groups.append(
            {
                "key": key,
                "label": "PvE" if key == "pve" else "PvP" if key == "pvp" else key,
                "stat_count": len(rows),
                "stats": rows,
            }
        )
    return {"schema_version": SCHEMA_VERSION, "groups": groups}
