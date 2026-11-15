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

P1 起多了一种行：**账号级三档行**（`existing` / `deleted` / `account_total`），见文件后半的
「三档并存」段。两种行的区别只有一句：单角色行给 `value`（这个角色自己的数），
账号级行给三个档位的数、**没有 `value`** —— 免得读的人把某一档当成生涯。形状差由 payload
顶层的 `scope` 标明。
"""

from __future__ import annotations

from typing import Any, Mapping, Sequence

from .data import pvp_counters

# 2：账号级三档行（`existing`/`deleted`/`account_total`）与 `scope`/`source` 标签落地，
# 单角色行不再冒充生涯数字（破坏性：`stats` 默认从"第一个角色"改成账号级）。
SCHEMA_VERSION = 2

# 周期词表与中文名的唯一出处是计数器那张对照表（career/season/act），
# 统计接口只是"只认其中 career 这一个"。
PERIOD_LABELS_ZH: dict[str, str] = pvp_counters.PERIOD_LABELS_ZH

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
    # 上游口径是 KDA 指数（(击杀+助攻/2)/死亡），不是"击杀+助攻"的合计：
    # 真机核对 1.5091 == (62734+16130/2)/46914，公式在 _derived_kda。
    "killsDeathsAssists": ("KDA", "core", ""),
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


def _row_sort_key(row: dict[str, Any]) -> tuple[int, int, str]:
    """分组顺序 → 核心项按数值降序 → 键名（单档行与三档行共用同一份排序）。"""
    return (
        GROUP_ORDER.index(row["group"]),
        -int(row.get("account_total") or row.get("value") or 0) if row["group"] == "core" else 0,
        row["upstream_id"],
    )


def stat_rows(section: Mapping[str, Any] | None) -> list[dict[str, Any]]:
    """一段上游统计（如 `allPvE.allTime`）→ 行列表，按分组与常用度排序。"""
    if not isinstance(section, Mapping):
        return []
    rows = [
        stat_row(str(key), value if isinstance(value, Mapping) else None)
        for key, value in section.items()
    ]
    rows.sort(key=_row_sort_key)
    return rows


def group_label(key: str) -> str:
    return {"pve": "PvE", "pvp": "PvP"}.get(key, key)


def filter_groups(payload: dict[str, Any], query: str) -> dict[str, Any]:
    """按名称筛统计行（中文名或上游 id 片段，大小写不敏感）；口径块原样保留。

    只动 `groups[].stats[]`，`scope` / `mode` / `period` / `tiers` 一个不改 ——
    筛的是"给你看哪几行"，不是"这次查的什么口径"。命中 0 行时把可用名称放进
    `query_hint`，让调用方如实说明"没匹配到"而不是回一个空壳当答案。
    """
    wanted = query.strip().casefold()
    if not wanted:
        return payload
    available = sorted({
        str(row.get("name") or "")
        for group in (payload.get("groups") or [])
        for row in (group.get("stats") or [])
    })
    groups = []
    for group in payload.get("groups") or []:
        rows = [
            row for row in (group.get("stats") or [])
            if wanted in str(row.get("name", "")).casefold()
            or wanted in str(row.get("stat_id", "")).casefold()
            or wanted in str(row.get("upstream_id", "")).casefold()
        ]
        if not rows:
            continue
        groups.append({**group, "stats": rows, "stat_count": len(rows)})
    filtered = {**payload, "groups": groups, "filtered_by": query}
    if not groups:
        filtered["query_hint"] = (
            f"没有任何统计项的名称包含 {query!r}（大小写不敏感）。"
            f"这个口径下可用的名称例如：{'、'.join(available[:8])}…去掉 query 就能拿到全部。"
        )
    return filtered


def stat_groups(
    sections: Mapping[str, Mapping[str, Any]],
    labels: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """`{"pve": allTimePvE, "pvp": allTimePvP}` → 统一的分组结构。

    按模式查询时组名是模式词（`trials`），中文标签由调用方传（`labels`）——
    词表在 `data/pvp_counters.py`，这里不另存一份。
    """
    groups = []
    for key, section in sections.items():
        rows = stat_rows(section)
        if not rows:
            continue
        groups.append(
            {
                "key": key,
                "label": (labels or {}).get(key) or group_label(key),
                "stat_count": len(rows),
                "stats": rows,
            }
        )
    return {"schema_version": SCHEMA_VERSION, "groups": groups}


# ── 三档并存：existing / deleted / account_total（P1）─────────────────────────
#
# 真机实测（2026-09-17，本机真账号）：账号级 `mergedAllCharacters` **已经包含**已删角色 ——
# 8 条角色的 `opponentsDefeated` 之和 = 78,864 = 现存 3 角色 50,622 + 已删 5 角色 28,242，
# 而 `mergedDeletedCharacters`（28,242）是**其中那 5 条的明细**。所以：
#
#     account_total = 78,864          （上游 mergedAllCharacters，含已删角色）
#     deleted       = 28,242          （上游 mergedDeletedCharacters，是明细、不是加数）
#     existing      = 50,622          （我们自己按下面的合并语义从现存角色重算）
#
# **account_total 不是 mergedAllCharacters + mergedDeletedCharacters**：早期计划里的
# 107,106 就是把 28,242 又加了一遍（等于把已删角色算两遍）。`TIER_LABELS_ZH` 会随 payload
# 一起给出去，让读的人自己看得见三个数各是什么。
TIERS: tuple[str, ...] = ("existing", "deleted", "account_total")
TIER_LABELS_ZH: dict[str, str] = {
    "existing": "现存角色（按 aggregate 的语义从每个角色的值重算）",
    "deleted": "已删角色明细（上游 mergedDeletedCharacters，是 account_total 的一部分）",
    "account_total": "账号级合计（上游 mergedAllCharacters，**已含已删角色**）",
}

# 统计接口能回答的周期 → 上游 `periodType`。实测（2026-09-18，只读）`DestinyStatsPeriodType`
# 只有 None=0 / Daily=1 / AllTime=2 / Activity=3：**没有 Season**，传 3（Activity）直接 500。
# 所以"本赛季/本篇章"这两档统计接口永远给不了 —— 只能由游戏内计数器回答
# （`activity_assistant(intent="counters", period="season")`）。周期中文名沿用
# `data/pvp_counters.PERIOD_LABELS_ZH`（同一套词，不另抄）。
STATS_PERIOD_TYPES: dict[str, int] = {"career": 2}
UNSUPPORTED_PERIOD_REASONS: dict[str, str] = {
    "season": "统计接口没有赛季周期（periodType 只有 None/Daily/AllTime/Activity，没有 Season；传 3 会 500）",
    "act": "统计接口没有篇章周期（同上：periodType 里没有这一档）",
}


def period_label(key: str, period_type: int | None = None) -> dict[str, Any]:
    """周期的自述块：`{key, label, upstream_period_type?}` —— 每个数字都要能自证口径。"""
    block: dict[str, Any] = {"key": key, "label": PERIOD_LABELS_ZH.get(key, key)}
    if period_type is not None:
        block["upstream_period_type"] = period_type
    return block


# payload 里的来源名：统计接口的账号级与按角色是两条路（P2 还要与 profile.metrics 并列）。
SOURCE_ACCOUNT = "GetHistoricalStatsForAccount"
SOURCE_CHARACTER = "GetHistoricalStats"
SCOPE_ACCOUNT = "account"
SCOPE_CHARACTER = "character"

# 一笔数字跨角色怎么合并。只有 sum/max/min 能机械合并；比值类（K/D、效率、胜率…）相加没有
# 意义，要么按公式重算（derived），要么给不出（none）——**不许**把比值加起来。
AGGREGATE_SUM = "sum"
AGGREGATE_MAX = "max"
AGGREGATE_MIN = "min"
AGGREGATE_DERIVED = "derived"
AGGREGATE_NONE = "none"

# 可加计数器
SUM_STATS: frozenset[str] = frozenset({
    "activitiesCleared", "activitiesEntered", "activitiesWon",
    "adventuresCompleted", "fireTeamActivities", "heroicPublicEventsCompleted",
    "publicEventsCompleted", "objectivesCompleted",
    "allParticipantsCount", "allParticipantsScore", "allParticipantsTimePlayed",
    "assists", "deaths", "kills", "opponentsDefeated", "precisionKills",
    "resurrectionsPerformed", "resurrectionsReceived", "suicides",
    "orbsDropped", "orbsGathered", "score", "teamScore",
    "secondsPlayed", "totalActivityDurationSeconds",
    "totalDeathDistance", "totalKillDistance",
    # 真机核对（2026-09-18）：账号级 5,372,422 = 现存 814,505 + 已删，是**可加**的；
    # 一开始按名字猜成"取最小"，被 scripts/verify_career_stats.py 的逐项断言当场抓出来。
    "remainingTimeAfterQuitSeconds",
})

# 取最大值（"最多/最长/最高"这类；真机核对：账号级的值等于 8 条角色里的最大值）
MAX_STATS: frozenset[str] = frozenset({
    "bestSingleGameKills", "bestSingleGameScore", "mostPrecisionKills",
    "longestKillSpree", "longestSingleLife", "longestKillDistance",
    "highestLightLevel", "highestCharacterLevel",
})

# 取最小值（"最快"这类；真机核对：账号级 38,300 = min(现存, 已删)，而 max 是 720,200）
MIN_STATS: frozenset[str] = frozenset({
    "fastestCompletionMs",
})

# 可加前缀：武器击杀细分的 23 项（`weaponKillsAutoRifle` …）都是计数器
SUM_STAT_PREFIXES: tuple[str, ...] = ("weaponKills",)


def _div(numerator: float | None, denominator: float | None) -> float | None:
    """比值：分母是 None 或 0 就给 None（**不编 0，也不编 inf**）。"""
    if numerator is None or not denominator:
        return None
    return numerator / denominator


def _derived_kd(parts: Mapping[str, float]) -> float | None:
    # 真机核对：账号级 killsDeathsRatio 1.3372127723067742 == 62734/46914
    return _div(parts.get("kills"), parts.get("deaths"))


def _derived_efficiency(parts: Mapping[str, float]) -> float | None:
    # 真机核对：1.6810333802276507 == (62734+16130)/46914
    return _div((parts.get("kills") or 0) + (parts.get("assists") or 0), parts.get("deaths"))


def _derived_win_loss(parts: Mapping[str, float]) -> float | None:
    # 真机核对：0.9868823786620026 == 2257/(4544-2257)
    entered = parts.get("activitiesEntered")
    if entered is None:
        return None
    return _div(parts.get("activitiesWon"), entered - (parts.get("activitiesWon") or 0))


def _derived_kda(parts: Mapping[str, float]) -> float | None:
    """KDA 指数 = (击杀 + 助攻/2) / 死亡。

    真机核对：账号级 1.5091230762672123 == (62734 + 16130/2)/46914，单角色那一档同样成立。
    上游 `killsDeathsAssists` **不是**"击杀+助攻"的合计（那个和是 78,864，容易被误读），
    所以 `_TABLE` 里的中文名也改成了 KDA。
    """
    return _div((parts.get("kills") or 0) + (parts.get("assists") or 0) / 2, parts.get("deaths"))


def _derived_score_per_kill(parts: Mapping[str, float]) -> float | None:
    # 真机核对：1.9001817196416617 == 119206/62734
    return _div(parts.get("score"), parts.get("kills"))


# 上游键 → (公式要用到的分量, 公式)。分量先按 sum 合并，再算比值。
DERIVED_STATS: dict[str, tuple[tuple[str, ...], Any]] = {
    "killsDeathsRatio": (("kills", "deaths"), _derived_kd),
    "efficiency": (("kills", "assists", "deaths"), _derived_efficiency),
    "winLossRatio": (("activitiesWon", "activitiesEntered"), _derived_win_loss),
    "killsDeathsAssists": (("kills", "assists", "deaths"), _derived_kda),
    "averageScorePerKill": (("score", "kills"), _derived_score_per_kill),
}


def aggregate_kind(upstream_id: str) -> str:
    """这一项跨角色怎么合并。表里没有的一律 `none`（**不猜**，也不默认按 sum）。"""
    if upstream_id in DERIVED_STATS:
        return AGGREGATE_DERIVED
    if upstream_id in SUM_STATS or upstream_id.startswith(SUM_STAT_PREFIXES):
        return AGGREGATE_SUM
    if upstream_id in MAX_STATS:
        return AGGREGATE_MAX
    if upstream_id in MIN_STATS:
        return AGGREGATE_MIN
    return AGGREGATE_NONE


def _value_of(entry: Any) -> int | float | None:
    basic = (entry or {}).get("basic") if isinstance(entry, Mapping) else None
    return _number((basic or {}).get("value"))


def _computed_entry(value: float, *, ratio: bool = False) -> dict[str, Any]:
    """自己合出来的值也照上游的形状给：`{basic: {value, displayValue}}`。

    数值与单角色行同一套规矩（`_number`：整数还原 int、小数留四位）—— 同一份 payload 里
    不该有两套精度。比值额外把显示值写成两位（`4.00`）：K/D 这类读两位就够。
    """
    number = _number(value)
    display = f"{value:.2f}" if ratio else str(number)
    return {"basic": {"value": number, "displayValue": display}}


def merge_sections(sections: Sequence[Mapping[str, Any] | None]) -> dict[str, Any]:
    """多段上游统计（每个角色一段）→ 一段按 `aggregate_kind` 合并的统计。

    用途只有两个：**现存角色那一档**（上游不直接给），以及按模式的统计（上游没有合并视图）。
    比值类先按分量求和再套公式；`none` 类的项**不出现在结果里** —— 调用方拿到的是"缺这项"，
    而不是一个编出来的数。
    """
    numeric: dict[str, list[float]] = {}
    for section in sections:
        if not isinstance(section, Mapping):
            continue
        for key, entry in section.items():
            value = _value_of(entry)
            if value is not None:
                numeric.setdefault(str(key), []).append(value)

    merged: dict[str, Any] = {}
    for key, values in numeric.items():
        kind = aggregate_kind(key)
        if kind == AGGREGATE_SUM:
            merged[key] = _computed_entry(sum(values))
        elif kind == AGGREGATE_MAX:
            merged[key] = _computed_entry(max(values))
        elif kind == AGGREGATE_MIN:
            merged[key] = _computed_entry(min(values))
    for key, (components, formula) in DERIVED_STATS.items():
        parts = {name: sum(numeric[name]) for name in components if name in numeric}
        if len(parts) != len(components):
            continue  # 分量不全就不算，宁可缺这一项
        value = formula(parts)
        if value is not None:
            merged[key] = _computed_entry(value, ratio=True)
    return merged


def tiered_row(
    upstream_id: str,
    account: Mapping[str, Any],
    deleted: Mapping[str, Any],
    existing: Mapping[str, Any],
) -> dict[str, Any]:
    """三档一行：`account_total` / `deleted` 来自上游，`existing` 是我们重算的。"""
    name, group, unit = _label(upstream_id)
    basic = ((account.get(upstream_id) or {}).get("basic") or {})
    row: dict[str, Any] = {
        "stat_id": _snake(upstream_id),
        "upstream_id": upstream_id,
        "name": name,
        "group": group,
        "aggregate": aggregate_kind(upstream_id),
        "existing": _value_of(existing.get(upstream_id)),
        "deleted": _value_of(deleted.get(upstream_id)),
        "account_total": _number(basic.get("value")),
        "display": str(basic.get("displayValue", "")),
    }
    if unit:
        row["unit"] = unit
    return row


def tiered_rows(
    account: Mapping[str, Any],
    deleted: Mapping[str, Any],
    existing: Mapping[str, Any],
) -> list[dict[str, Any]]:
    """三档并存的行式统计（账号级），按分组与账号级数值排序。

    行的集合由 `account`/`deleted` 两段**上游视图**决定：`existing` 只是给这些行补一档，
    它自己多算出来的项（上游账号级本来不发布的，如 PvE 的 `averageScorePerKill`）
    不出行 —— 否则会出现"account_total 是 null 却有 existing"的半截行。
    """
    keys = list(dict.fromkeys([*account, *deleted]))
    rows = [tiered_row(key, account, deleted, existing) for key in keys]
    rows.sort(key=_row_sort_key)
    return rows


def tiered_groups(
    tiers: Mapping[str, tuple[Mapping[str, Any], Mapping[str, Any], Mapping[str, Any]]],
    labels: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """`{"pvp": (account, deleted, existing)}` → 与 `stat_groups` 同形的分组结构。"""
    groups = []
    for key, (account, deleted, existing) in tiers.items():
        rows = tiered_rows(account, deleted, existing)
        if not rows:
            continue
        groups.append(
            {
                "key": key,
                "label": (labels or {}).get(key) or group_label(key),
                "stat_count": len(rows),
                "stats": rows,
            }
        )
    return {"schema_version": SCHEMA_VERSION, "groups": groups}


def empty_mode_payload(
    mode_key: str,
    mode_name: str,
    mode_type: int,
    period: str,
    period_type: int,
) -> dict[str, Any]:
    """这个模式一条可统计的对局都没有 → 空 groups + 说清"是没数据，不是 0"。

    与"没读到"分开：这里是**探到了、确实没有**（上游返回空对象），所以调用方拿到的是空清单
    加一句原因，而不是一个 0，也不是一次失败。
    """
    return {
        "schema_version": SCHEMA_VERSION,
        "source": SOURCE_CHARACTER,
        "scope": SCOPE_ACCOUNT,
        "aggregation": "computed",
        "groups": [],
        "tiers": TIER_LABELS_ZH,
        "mode": {
            "key": mode_key, "label": mode_name, "upstream_modes": mode_type, "upstream_group": "",
        },
        "period": period_label(period or "career", period_type),
        "characters": {"existing": 0, "deleted": 0, "total": 0, "returned": 0},
        "empty_reason": (
            f"这个账号在「{mode_name}」下没有可统计的对局：上游对这些角色都返回了空，"
            "这是**没有数据**，不是 0。"
        ),
    }
