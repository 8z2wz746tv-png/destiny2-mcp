"""把自己写出来的库存变化写回候选基线（2026-10-06）。

写账号的流程**自己会改库存**（搬装备 / 装模组 / 换穿），而 `recheck_confirmed_build` 是拿
"求解那一刻的快照版本"比对的 —— 于是工具自己的写入会让**下一次重试必然**
`stale_inventory_snapshot`：真机那次半途失败之后，第三次带同一个 `execution_id` 重试被拒，
被迫整轮重解重确认（用户多等一轮模型往返 + 一次确认）。

它只改**候选暂存里那份拷贝**的 `snapshot_version`，不碰账号、也不放行任何东西：写前复检
（格满 / 金装冲突 / 实例核对）每次照样重跑，所以"别装错套"的安全网没被削弱。

（单独一个模块是因为 `build_service` 与 `build_execution_guard` 都贴着各自的行数上限。）
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from ..build.snapshot_version import snapshot_version

if TYPE_CHECKING:
    from ..build_contracts import CanonicalBuild

_NOTE = (
    "（注意：本次已经**改动过账号**（见上方 steps）—— 已按当前实况把候选的库存基线推进到最新，"
    "可以直接用**同一个 execution_id** 重试，不必重新求解重确认。）"
)


async def rebaseline_note(
    candidates: Any,
    inventory: Any,
    player_name: str,
    character: str,
    build: CanonicalBuild,
) -> str:
    """重读现场：基线变了就推进候选并返回一句说明；没变返回空串。"""
    try:
        snapshot = await inventory.get_armor_snapshot(player_name, character)
    except Exception:  # 读不到就照实说，不能让"读快照失败"把失败原因盖掉
        return "（重读快照失败，基线未更新；要重试请重新求解。）"
    current = snapshot_version(snapshot)
    if current == build.snapshot_version:
        return ""
    candidates.register(
        build.model_copy(update={"snapshot_version": current}), player_name
    )
    return _NOTE
