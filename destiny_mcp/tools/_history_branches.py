"""活动历史的两个出口：`history`（最近几场）与 `pgcr`（单场结算）。

从 `assistants.py` 抽出来的原因和别的 `_*_branches.py` 一样：**那边贴着体量上限
（`tests/test_module_size_ratchet.py`），加一个 intent 就得先腾出位置**。
`_counters_branches.py` 的注释里早就写着"其余历史分支此前都在 `assistants.py` 里逐行返回" ——
这里就是那句话的下半句。

话术一个字都没改：这两个分支本来就只是"取数 + 包一层信封"，抽出来只是换个住址。

规矩同其他分支：形状来自 `services/`，这里只说人话、只判成败。
"""

from __future__ import annotations

from typing import Any

from ._responses import ok_response

#: 本模块认领的 intent（`assistants.py` 用它分派，别再写第二份字符串）
INTENTS: tuple[str, ...] = ("history", "pgcr")


async def history_response(
    svc: dict[str, Any],
    intent: str,
    player_name: str,
    character: str,
    mode: str,
    count: int,
    activity_id: str,
) -> dict[str, Any]:
    """`history` 与 `pgcr` 的分派。

    `pgcr` 只看活动 ID（与玩家无关，所以 player_name 在参数归属表里对它不生效）；
    `history` 才按玩家/角色/模式/条数取。
    """
    if intent == "pgcr":
        return ok_response("已读取活动结算。", {"pgcr": await svc["activity_svc"].get_pgcr(activity_id)})
    return ok_response(
        "已读取活动历史。",
        {"activities": await svc["activity_svc"].get_activity_history(player_name, character, mode, count)},
    )
