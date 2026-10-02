"""活动分支的响应组装：`assistants.py` 只做分派，话术与降级判断落在这里。

文件名与 `_counters_branches.py` / `_weapon_branches.py` 同族（`_*_branches.py`），
`tests/test_tool_dispatch_contracts.py` 会把它算进"分派层"一起扫。

规矩同其他分支：形状来自 `services/`，这里只说人话、只判成败。

这个分支最容易说错的三件事，都在下面的 warnings 里写死了：

1. **数字是游戏内计数器，不是我们刚算的** —— 完成数来自组件 1100，和"逐场 PGCR 数出来"不是一回事；
2. **导师是人数**（一次带 3 个新人记 +3），所以能大于完成场次；
3. **哪些列没有**（全程数、最短用时、排名、DayOne 编号）由 `data.unavailable` 逐条说明，
   调用方**不要**用"没有这个字段"推断出"你是 0"。
"""

from __future__ import annotations

from typing import Any

from ..data import raids
from ..error_codes import ErrorCode
from ._responses import data_only, error_response, ok_response

#: 本分支认领的 intent（`assistants.py` 用它分派，别再写第二份字符串）
INTENTS: tuple[str, ...] = ("raid_report", "raid_scan")


def _count(value: Any) -> int:
    """把服务返回的计数取成整数，取不到给 0。

    **分支层不能对服务返回值直接做算术或索引**：`tests/test_ignored_parameters.py`
    用替身跑每一条分派，替身对任何属性都返回自己（`_Reply`），
    `_Reply * 0.88` 会直接 TypeError。这里的 0 只影响话术里的估算，不碰数据。
    """
    return value if isinstance(value, int) and not isinstance(value, bool) else 0


def _unavailable_field(result: dict[str, Any], field: str) -> str:
    for item in result.get("unavailable") or []:
        if item.get("field") == field:
            return str(item.get("reason") or "")
    return ""


async def raid_response(
    svc: dict[str, Any],
    intent: str,
    player_name: str,
    mode: str,
    query: str,
    count: int,
) -> dict[str, Any]:
    """两个 intent 的入口：`raid_report` 看表，`raid_scan` 扫一块。"""
    if intent == "raid_scan":
        return await raid_scan_response(svc, player_name, query, count)
    return await raid_report_response(svc, player_name, mode)


async def raid_scan_response(
    svc: dict[str, Any],
    player_name: str,
    activity: str,
    count: int,
) -> dict[str, Any]:
    """`raid_scan`：按副本扫一块 PGCR。

    为什么是"一块"而不是"扫到底"：实测 PGCR **0.88 秒/场，并发 4/8/12 都是 1.1~1.3 场/秒**
    （瓶颈在上游单场延迟，加并发没用）—— 深岩墓室 336 场要 4.5 分钟、全量 2095 场要 26 分钟。
    一次调用干不完，所以按 `count` 分块、可重复调用续扫，回执里带 `pending` 告诉你还剩多少。
    """
    result = await svc["raid_report_svc"].scan(player_name, activity, count)
    done, total, pending = (_count(result.get("scanned")), _count(result.get("total")),
                            _count(result.get("pending")))
    activity_name = result.get("activity", activity) or activity
    summary = (
        f"已扫描「{activity_name}」{done} 场（共 {total} 场，还剩 {pending} 场）。"
        if pending else
        f"「{activity_name}」{total} 场已全部扫描完（本次 {done} 场）。"
    )
    return ok_response(summary, data_only(result), warnings=[
        *([result["unavailable"]] if result.get("unavailable") else []),
        f"PGCR 实测 0.88 秒/场，这一块花了约 {done * 0.88:.0f} 秒；"
        f"还剩 {pending} 场，再调一次同样的 intent 继续（会接着上次的位置扫）。",
        "这两列（全程次数/最短用时）只认 `activityWasStartedFromBeginning` 为真的场次，"
        "还要你自己完成 —— 口径与 raid.report 一致，与 RaidHub 默认（把坏窗口的场次算检查点）不同。",
    ])


async def raid_report_response(
    svc: dict[str, Any],
    player_name: str,
    mode: str,
) -> dict[str, Any]:
    """`activity_assistant(intent="raid_report")` 的响应。

    `mode` 选口径：`raid`（默认）/ `dungeon`；词表外的取值由服务层报
    `invalid_argument_error` —— "筛出来是空"和"你筛的词我不认识"要分开。
    """
    kind = (mode or "raid").strip().lower()
    result = await svc["raid_report_svc"].get_report(player_name, kind)

    # 组件 1100 读不到：这是**没有数据**，不是"一行都没有"。不能报成功。
    counters_reason = _unavailable_field(result, "counters")
    if counters_reason:
        return error_response(ErrorCode.API_ERROR, counters_reason)

    # mode 合法性由服务层判；这里 .get + 兜底（替身测试会喂哨兵值，直接索引会崩）。
    label = raids.KIND_LABELS_ZH.get(result.get("kind"), kind)
    rows = result["rows"]
    with_completions = sum(1 for row in rows if row.get("completions"))
    return ok_response(
        f"已读取{label}报表 {len(rows)} 个副本"
        f"（其中 {with_completions} 个有完成数；来源 profile.metrics 组件 1100）。",
        data_only(result),
        warnings=[
            *result.get("warnings", []),
            "「全程次数」默认口径 = 全程开局（含字段坏掉的那段窗口，同 raid.report）且你自己完成；"
            "同一行的 `full_clears_strict` 是只认上游明确标记的严格口径 —— **两个数不同是正常的**，"
            "第三方站点的数字复刻不出来（那段窗口 Bungie 明说永不修、不追溯）。",
            "表里的数字全部来自**游戏内官方计数器**（profile 组件 1100），"
            "不是逐场结算推出来的：同一个副本，「官方完成数」与「自己数 PGCR」会不一样，"
            "以官方计数器为准（真机：深岩墓室 229）。",
            "「担任导师次数」的单位是**人数**不是次数"
            "（带领完成首次该副本的守护者总人数），一次带 3 个新人记 +3，"
            "所以它大于完成场次是正常的，不是数据错误。",
            f"没有的列写在 unavailable 里（{len(result.get('unavailable') or [])} 条），"
            "逐条说明为什么做不了；`null` 是「没这项/没读到」，**不是 0**。",
        ],
    )
