"""`activity_assistant(intent="stats")` 的响应组装：P1 三档 + P2 两个来源并列。

为什么单独一个文件：三条规矩都得写在一处才看得懂 ——

1. **三档不混**：账号级统计给 `existing` / `deleted` / `account_total`，数字形状与来源在
   `activity_stats` 的「三档并存」段；这里只负责把三档讲成人话。
2. **两个来源并列**（P2）：同一个"熔炉生涯击败"，游戏内计数器（`profile.metrics`）与统计
   接口（`GetHistoricalStatsForAccount`）**各给一个数、各带 source**，差值写进 warnings。
   计数器是可选数据：读不到只降级成 warning + `counters_unavailable`，**不影响统计接口的
   结果**，也绝不把"没读到"写成 0。
3. **不给结论**：这里只报数字与差值，不解释"你为什么少打了多少" —— 那个差拆不出来。

计数器口径与筛选词都在 `data/pvp_counters.py`（P3-1 的对照表），这里不另抄一张。
"""

from __future__ import annotations

import asyncio

from typing import Any

from .. import activity_stats
from ..data import activity_modes
from ..error_codes import ErrorCode
from ..logging_config import get_logger
from ._responses import error_response, ok_response

logger = get_logger(__name__)

# 计数器与统计接口里"同一件事"的配对：两侧都叫"熔炉生涯击败"，真机实测
# 124,495（计数器，S1 起累计）vs 78,864（统计接口账号级）。配对是**人工确认的实测事实**
# （不是从上游字段推出来的），所以只有这一条；其余计数器没有可对照的统计项。
# 计数器自己的 mode/period 分类仍在 `data/pvp_counters.py`，这里只是指出"谁和谁是一回事"。
# 模式 → {计数器 hash: (统计接口里对应的组, 行 id)}。**按模式分开**：
# 按模式调用时统计接口的分组 key 就是模式词（实测 `mode=trials` → 组 `trials`），
# 而默认的账号级调用分组是 pve/pvp —— 同一个"击败对手"在两处的组名不同，所以表要区分。
#
# 配上的意义是"把两个来源并排给出来"，**不是让它们相等**：真机 `mode=trials` 统计接口
# 击败 1,474 / 胜场 105，游戏内计数器 10,696 / 826 —— 差 7 倍，口径不同、都真实
# （ADR-005：生涯数字以游戏内计数器为准）。以前按模式调用**不带**计数器、也没有任何提示，
# 用户问"我试炼生涯多少杀"只会看到 1,474，比游戏里小 7 倍。
COUNTER_STAT_PAIRS: dict[str, dict[int, tuple[str, str]]] = {
    "crucible": {811894228: ("pvp", "opponents_defeated")},
    "trials": {
        2082314848: ("trials", "opponents_defeated"),
        1365664208: ("trials", "activities_won"),
    },
}

# 默认（账号级、不分模式）那一次读的格子：熔炉 + 生涯。
_COUNTER_MODE = "crucible"
_COUNTER_PERIOD = "career"


def empty_scope_warning(scope_label: str) -> str:
    """空清单不许被读成 0：上游没返回任何统计项时，说清这是"没有数据"。"""
    return (
        f"上游在这个口径下（{scope_label.lstrip('，') or '全模式、账号级'}）没有返回任何统计项："
        "这是**没有数据**，不是 0；换个模式/角色再问，或确认这个角色打过这个模式。"
    )


def _mode_period_label(result: dict[str, Any]) -> str:
    """把口径标签写成人话：模式=奥斯里斯试炼，周期=生涯。没给就不写（不编"全部"）。"""
    parts = []
    mode = result.get("mode") or {}
    if mode.get("label"):
        parts.append(f"模式={mode['label']}")
    period = result.get("period") or {}
    if period.get("label"):
        parts.append(f"周期={period['label']}")
    return ("，" + "，".join(parts)) if parts else ""


def _account_question(result: dict[str, Any]) -> str:
    counts = result.get("characters") or {}
    return (
        f"已读取生涯统计（账号级三档：现存 {counts.get('existing', 0)} 个角色 / "
        f"已删 {counts.get('deleted', 0)} 个；account_total 已含已删角色，"
        "不是 existing + deleted 之外的第三个数）。"
    )


def _counter_rows(
    counters: list[dict[str, Any]], mode_key: str = _COUNTER_MODE
) -> list[dict[str, Any]]:
    """`game_counters` 只给能和统计接口对上的计数器（配对表说了算，不另挑）。"""
    pairs = COUNTER_STAT_PAIRS.get(mode_key) or {}
    return [row for row in counters if row.get("metric_hash") in pairs]


def _stat_total(result: dict[str, Any], group: str, stat_id: str) -> Any:
    for block in result.get("groups") or []:
        if block.get("key") != group:
            continue
        for row in block.get("stats") or []:
            if row.get("stat_id") == stat_id:
                return row.get("account_total")
    return None


def _paired_warnings(
    result: dict[str, Any],
    counters: list[dict[str, Any]],
    unavailable: str,
    counter_scope: str,
) -> list[str]:
    """P2：两个来源都给之外，还要把**差异原因**写清楚（不然两个数看着像打架）。"""
    warnings = [
        "生涯数字有两个来源，口径不同、都不许当成对方：游戏内计数器（source=profile.metrics，"
        "自 S1 起累计、含统计接口已不再列举的旧角色）与统计接口（"
        f"source={result.get('source')}）。",
    ]
    if counter_scope:
        warnings.append(counter_scope)
    if unavailable:
        warnings.append(
            f"这次没读到游戏内计数器（{unavailable}）—— 统计接口的数字不受影响；"
            '"没读到"不等于"没有计数"。'
        )
        return warnings
    # 配对表按模式分层（账号级那次读的是 `_COUNTER_MODE` 那一层）。
    for metric_hash, (group, stat_id) in (COUNTER_STAT_PAIRS.get(_COUNTER_MODE) or {}).items():
        counter = next((row for row in counters if row.get("metric_hash") == metric_hash), None)
        total = _stat_total(result, group, stat_id)
        if counter is None or not isinstance(counter.get("progress"), int) or not isinstance(total, int):
            continue
        difference = counter["progress"] - total
        warnings.append(
            f"同一个「{counter.get('label_zh') or counter.get('name')}」：游戏计数器 "
            f"{counter['progress']}（profile.metrics，哈希 {metric_hash}）、统计接口账号级 "
            f"{total}，差 {difference}。这个差不是我们能拆出来的部分 —— 计数器从 S1 起累计，"
            "含统计接口已不再列举的旧角色；两个数都给，别相加、也别互相纠正。"
        )
    return warnings


async def _read_counters(
    svc: dict[str, Any], player_name: str, mode_key: str = _COUNTER_MODE
) -> tuple[list[dict], str]:
    """读某个模式的**生涯**计数器。读不到就返回 `([], 原因)`，**不抛**（统计是主结果）。

    配对表里没有这个模式就不读（返回空 + 空原因）：宁可不说，也不塞一堆无关计数器进 payload。
    """
    pairs = COUNTER_STAT_PAIRS.get(mode_key) or {}
    if not pairs:
        return [], ""
    try:
        payload = await svc["activity_counters_svc"].get_career_counters(
            player_name, "", max(len(pairs) * 4, 10), mode_key, _COUNTER_PERIOD
        )
    except Exception as exc:  # noqa: BLE001 - 可选数据不许弄坏主结果，但要留痕
        logger.warning("生涯统计附带读计数器失败：%s", exc)
        return [], f"读取计数器时出错：{exc}"
    if payload.get("unavailable"):
        return [], payload["unavailable"]
    return _counter_rows(payload.get("counters") or [], mode_key), ""


def _paired_mode_warning(
    result: dict[str, Any], counters: list[dict[str, Any]], mode_key: str
) -> str:
    """把"游戏内计数器 vs 统计接口"两个数并排写出来（不解释成谁对谁错）。"""
    pairs = COUNTER_STAT_PAIRS.get(mode_key) or {}
    group = (result.get("mode") or {}).get("key") or mode_key
    parts = []
    for row in counters:
        upstream = _stat_total(result, group, pairs[row["metric_hash"]][1])
        # 统计接口没给这一项时写"没给"，不写 `None` —— 那读起来像个数字（缺值不编）。
        upstream_text = "没给这一项" if upstream is None else str(upstream)
        parts.append(
            f"{row['name']}：游戏内计数器 {row['progress']}"
            f"（profile.metrics，哈希 {row['metric_hash']}）、统计接口 {upstream_text}"
        )
    return (
        "同一件事有两个来源、数字**不一样是正常的**（生涯数字以游戏内计数器为准，见 ADR-005）："
        + "；".join(parts)
        + "。两个数都给，别相加、也别互相纠正。"
    )


async def stats_response(
    svc: dict[str, Any],
    player_name: str,
    character: str | None = None,
    mode: str = "",
    period: str = "",
    query: str = "",
) -> dict[str, Any]:
    """`activity_assistant(intent="stats")` 的响应（P1 三档 + P2 双来源 + P3-2 模式/周期）。

    `query`：按**统计项名称**（中文名或上游 id 片段）筛行，大小写不敏感。
    为什么要它：一次账号级 `mode=crucible` 有 60 行 ≈ 30 KB，而"我熔炉击败多少"只需要一行；
    筛完只回那一行，模型要读的 token 直接降一个数量级。筛不到就**如实说没匹配到**
    （列一下可用的名称供改词），不回一个空壳当答案。
    """
    # 上游没有赛季/篇章周期：**先如实报取不到**，别拿生涯冒充，也别去试会 500 的 periodType=3。
    if period.strip().lower() in activity_stats.UNSUPPORTED_PERIOD_REASONS:
        wanted = period.strip().lower()
        return error_response(
            ErrorCode.API_ERROR,
            f"period={wanted!r} 取不到（unavailable）："
            f"{activity_stats.UNSUPPORTED_PERIOD_REASONS[wanted]}。"
            f"统计接口只回答 career（生涯）；赛季数字只能由游戏内计数器回答。",
            next_actions=[
                f'要{wanted}的 PvP 数字用 intent="counters" + period="{wanted}"'
                "（赛季/篇章计数器是游戏内那一套，与统计接口不是一回事）。"
            ],
            warnings=[
                "这里没有降级成生涯：拿生涯数字冒充赛季，比如实报「取不到」更糟。"
            ],
        )

    # 统计接口与游戏内计数器**互不依赖** → 同时去拿（真机实测 4.9s → 约 3s）。
    # 计数器是账号级的，单角色路径本来就不附它，所以那种情况不预取。
    stats_coro = svc["activity_svc"].get_historical_stats(
        player_name, character, mode, period, query
    )
    prefetched: tuple[list[dict], str] = ([], "")
    if character:
        result = await stats_coro
    else:
        # 计数器要按"这次问的模式"取：`mode` 报得出来就用它，否则按默认（熔炉生涯）。
        # 词表外的 `mode` 不预取 —— 那种情况统计调用自己会报明确的错，省一次无谓请求。
        hinted = activity_modes.resolve(mode) if mode else _COUNTER_MODE
        if mode and hinted is None:
            result = await stats_coro
        else:
            result, prefetched = await asyncio.gather(
                stats_coro, _read_counters(svc, player_name, hinted or _COUNTER_MODE)
            )
    data: dict[str, Any] = {"stats": result}
    # 筛选发生在服务层（`activity_stats.filter_groups`）——判断逻辑不落工具层；
    # 这里只把"没匹配到"的说明接进 warnings。
    query_hint = str(result.get("query_hint") or "")

    if result.get("scope") != activity_stats.SCOPE_ACCOUNT:
        name = (result.get("character") or {}).get("name") or character or "该角色"
        scope_label = _mode_period_label(result)
        warnings = [
            *([query_hint] if query_hint else []),
            f"这是 {name} 一个角色的数字（scope=character、source=GetHistoricalStats），"
            "不是账号生涯；要账号级三档就不传 character。",
            "游戏内计数器是账号级的（不按角色拆），要那个数用 intent=\"counters\"。",
        ]
        if not result.get("groups"):
            warnings.append(empty_scope_warning(scope_label))
        return ok_response(
            f"已读取生涯统计（单角色：{name}，scope=character{scope_label}）。",
            data,
            warnings=warnings,
        )

    # 按模式的账号级合计是我们自己合的（上游的账号级端点会忽略 modes），
    # 计数器对照的是"全模式账号级"，所以这两件事不能混在一份 payload 里。
    if result.get("mode"):
        mode_key = str((result.get("mode") or {}).get("key") or "")
        warnings = [
            *([query_hint] if query_hint else []),
            "这是按模式的账号级合计（scope=account、aggregation=computed）："
            "逐角色取上游按角色统计后按同一套语义合并（可加相加 / 最多取最大 / 比值重算），"
            "上游**没有**这个模式的合并视图，所以这三个档位不是上游给的。",
            *([result["unavailable"]] if result.get("unavailable") else []),
            *([result["empty_reason"]] if result.get("empty_reason") else []),
            "游戏内计数器（intent=\"counters\"）按 mode/period 另有一份口径，两者不要相加。",
        ]
        # 同模式的游戏内计数器一并给出来（配对表有才读）：按模式的生涯数字最容易被读成
        # "游戏里那个数"，而它其实小得多（真机 trials：1,474 vs 10,696）。
        mode_counters, mode_unavailable = prefetched
        if mode_counters:
            data["game_counters"] = mode_counters
            warnings.append(_paired_mode_warning(result, mode_counters, mode_key))
        if mode_unavailable:
            data["counters_unavailable"] = mode_unavailable
            warnings.append(
                f"同模式的游戏内计数器这次没读到（{mode_unavailable}）；统计接口的结果不受影响。"
            )
        return ok_response(
            f"已读取按模式的生涯统计{_mode_period_label(result)}"
            f"（账号级三档，由 {result.get('characters', {}).get('returned', 0)} 个角色合并而来）。",
            data,
            warnings=warnings,
        )

    counters, unavailable = prefetched
    if counters:
        data["game_counters"] = counters
    if unavailable:
        data["counters_unavailable"] = unavailable

    warnings = _paired_warnings(result, counters, unavailable, "")
    if not result.get("groups"):
        warnings.append(empty_scope_warning(""))
    if result.get("unavailable"):
        # 账号级读的是上游合并视图，不该出现"部分角色失败"；带出来只是为了不吞掉它。
        warnings.append(result["unavailable"])
    if result.get("empty_reason"):
        warnings.append(result["empty_reason"])
    if query_hint:
        warnings.append(query_hint)

    return ok_response(
        _account_question(result),
        data,
        warnings=warnings,
        next_actions=[
            '要赛季数字（上游统计接口没有赛季周期）用 intent="counters" + period="season"。',
            "要单个角色用 intent=\"stats\" + character=hunter/warlock/titan（会标 scope=character）。",
            '要看某个模式（试炼/铁旗/竞技/智谋）用 intent="stats" + mode="trials" 之类。',
        ],
    )
