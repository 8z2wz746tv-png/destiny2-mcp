"""活动分支的响应组装：`assistants.py` 只做分派，话术与降级判断落在这里。

现在只有 `counters` 一个分支（其余历史分支此前都在 `assistants.py` 里逐行返回）。
单独开文件是为了让"游戏内计数器"的话术与失败判断有一处可查：它是**另一个数据源**，
最容易被当成 `stats` 的另一种写法 —— 两者的数字本来就不一样（见
docs/reference/bungie_api.md「Metrics（组件 1100）vs Stats」）。

文件名跟 `_weapon_branches.py` / `_armor_branches.py` 同族（`_*_branches.py`），
`tests/test_tool_dispatch_contracts.py` 会把它算进"分派层"一起扫。

规矩与 `_weapon_branches.py` 一致：形状来自 `services/`，这里只说人话、只判成败。
"""

from __future__ import annotations

from typing import Any

from ..data import pvp_counters
from ..error_codes import ErrorCode
from ._responses import data_only, error_response, ok_response


def _scope_label(mode: str, period: str) -> str:
    """把过滤条件写成人话（没给就是不写，不编"全部"这种结论）。"""
    parts = []
    if mode:
        parts.append(f"模式={pvp_counters.MODE_LABELS_ZH.get(mode.strip().lower(), mode)}")
    if period:
        parts.append(f"周期={pvp_counters.PERIOD_LABELS_ZH.get(period.strip().lower(), period)}")
    return "，".join(parts)


async def counters_response(
    svc: dict[str, Any],
    player_name: str,
    query: str,
    count: int,
    mode: str = "",
    period: str = "",
) -> dict[str, Any]:
    """`activity_assistant(intent="counters")` 的响应。

    读不到时返回 `ok=false`：组件 1100 被上游抖动吞掉是**没有数据**，
    不是"有 0 条计数器"——这里不能报成功，否则就成了把空当 0。

    `mode`/`period` 的词表外取值由服务层抛 `invalid_argument_error`（不返回空清单）：
    "筛出来是空"和"你筛的词我不认识"必须分开。
    """
    result = await svc["activity_counters_svc"].get_career_counters(
        player_name, query, count, mode, period
    )
    if result["unavailable"]:
        return error_response(ErrorCode.API_ERROR, result["unavailable"])
    scope = _scope_label(mode, period)
    return ok_response(
        f"已读取游戏内生涯计数器 {result['returned']}/{result['total']} 条"
        f"（来源 profile.metrics{('，' + scope) if scope else ''}）。",
        data_only(result),
        warnings=[
            *result["warnings"],
            "这是游戏内生涯计数器（S1 起累计、含已删角色），"
            '与 intent="stats" 的统计接口口径不同，两个数字都出现时不要混用。',
            "重名计数器（如「已击败对手」：熔炉生涯 / 试炼生涯 / 熔炉本赛季）靠 mode + period "
            "区分，别把名字当成唯一标识。",
        ],
    )
