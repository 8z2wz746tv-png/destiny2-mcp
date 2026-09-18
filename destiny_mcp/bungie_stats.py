"""活动/战绩类端点的取数域：历史统计（按角色 / 账号级）。

为什么从 `bungie_client` 拆出来：客户端贴着体量上限，而"按模式与周期统计"必须给它加
`modes` / `periodType` 两个参数。拆法照 `manifest.py` + `manifest_*.py` 的老规矩 ——
`BungieClient` 留同名门面方法，域实现与注释落在这里；错误映射仍走 `bungie_errors`（唯一
翻译层），所以本模块只写 URL、参数和"为什么"。

三条真机结论（2026-09-18，只读，本机真账号）直接决定这里的写法：

1. **`periodType` 没有 Season**：取值只有 None=0 / Daily=1 / AllTime=2 / Activity=3。
   传 3（Activity）在按角色端点上直接 500 —— 所以"本赛季"永远不该由统计接口回答，
   只能由游戏内计数器（`profile.metrics`）回答 —— 所以本模块只接受 `AllTime`（=2），
   周期词到数值的映射在 `activity_stats.STATS_PERIOD_TYPES`（唯一出处）。
2. **`modes` 只在按角色的端点上生效**：账号级 `.../Account/{id}/Stats/` 传
   `modes=84` 与不传的响应**一字不差**（都是 allPvE/allPvP 合并视图）→ 按模式的账号级
   统计只能逐角色取、自己合（见 `services/activity_service._mode_stats`）。
3. **已删角色也能按角色取**：`characters[].deleted=true` 的 ID 拿去请求照样返回数据，
   所以按模式的账号级合计**能**把已删角色算进去（与 P1 三档口径一致）。

只有这一种模式取值来源：`data/activity_modes`（模式词与数值的唯一出处，取自本地 Manifest 的
`DestinyActivityModeDefinition.modeType`：熔炉 5 / 铁旗 19 / 竞技 69 / 智谋 63 / 试炼 84 /
突袭 4）。**别再自己写一张表** —— 手抄过的两张（`ACTIVITY_MODES` 的 allpvp=9、
`onslaught/猛攻`=69）都是错的，实测 `modes=9` 会 500、69 是多人竞技PvP。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import aiobungie

from .bungie_errors import _raise_bungie_error
from .logging_config import get_logger

if TYPE_CHECKING:  # 只为类型标注：运行时不 import，免得与 bungie_client 形成环
    from .bungie_client import BungieClient

logger = get_logger(__name__)


async def get_character_stats(
    client: BungieClient,
    membership_type: int,
    membership_id: str,
    character_id: str,
    *,
    modes: int | None = None,
    period_type: int | None = None,
) -> dict:
    """按角色的历史统计（可选按 `modes` / `periodType` 过滤）。

    `modes` 传的是 `DestinyActivityModeType` 数值（见模块 docstring），一次一个模式；
    传 `modes=9`（旧手写表里的 allpvp）会 500；词与数值只从 `data/activity_modes` 取。
    `period_type` 只该传 `AllTime`（2）：别的取值要么没有意义（0）、要么是按天/按场
    （1/3，后者实测 500）。周期词 → 数值的映射在 `activity_stats.STATS_PERIOD_TYPES`。

    Returns:
        上游响应（`aiobungie` 已经剥掉信封）：不带参数时是 `{allPvE: {...}, allPvP: {...}}`，
        带 `modes` 时是 `{<模式的组名>: {...}}`（如 `trials_of_osiris`）。
    """
    params: dict[str, str] = {}
    if modes is not None:
        params["modes"] = str(modes)
    if period_type is not None:
        params["periodType"] = str(period_type)
    logger.debug(
        "API call: GetHistoricalStats(mid=%s, char=%s, modes=%s, periodType=%s)",
        membership_id, character_id, modes, period_type,
    )
    try:
        result = await client.rest.static_request(
            "GET",
            f"Destiny2/{membership_type}/Account/{membership_id}/Character/{character_id}/Stats/",
            params=params or None,
        )
    except aiobungie.HTTPError as exc:
        _raise_bungie_error(exc, "读取历史统计")
    logger.debug("GetHistoricalStats returned")
    return result


async def get_account_stats(
    client: BungieClient,
    membership_type: int,
    membership_id: str,
) -> dict:
    """账号级历史统计：`mergedAllCharacters` / `mergedDeletedCharacters` / `characters[]`。

    真机实测（2026-09-17）：`mergedAllCharacters` **已含已删角色** ——
    8 条角色之和 = 78,864 = 现存 3 角色 50,622 + 已删 5 角色 28,242，而
    `mergedDeletedCharacters`（28,242）是其中那 5 条的明细。三档口径见
    `activity_stats` 的「三档并存」段。

    `modes`/`periodType` 在这里**没有意义**（上游静默忽略，实测），所以不接受这两个参数：
    要按模式就逐角色走 `get_character_stats`。
    """
    logger.debug("API call: GetHistoricalStatsForAccount(mid=%s)", membership_id)
    try:
        result = await client.rest.static_request(
            "GET",
            f"Destiny2/{membership_type}/Account/{membership_id}/Stats/",
        )
    except aiobungie.HTTPError as exc:
        _raise_bungie_error(exc, "读取账号级历史统计")
    logger.debug("GetHistoricalStatsForAccount returned")
    return result
