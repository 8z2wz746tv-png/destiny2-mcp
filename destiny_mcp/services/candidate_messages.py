"""候选 ID → 响应：取回签发的那份方案，以及四个失败状态的错误码与话术。

判据只有一处（`build_candidates.BuildCandidateStore.resolve()` 返回状态字符串），
话术也只有一处（这里）。以前这四句在**两处**各写一份 —— 只给 ID 的只读入口
（`build_candidates.describe_candidate`）与 `equip_build` 的确认执行 —— 措辞已经开始漂。

为什么值得单独一个模块：`consumed`（"用过了，重解才能再装"）与 `expired`
（"这次确认超时了，重新确认一次"）给调用方的**下一步完全不同**，混成一句就会指错路
（真机 2026-10-06：被拦下的执行把候选烧了却报 `unknown`，调用方去重解而不是重试）。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ..error_codes import ErrorCode

if TYPE_CHECKING:  # 只为类型标注，运行时不 import，免得与暂存模块互相依赖
    from .build_candidates import BuildCandidateStore

#: 状态 → (错误码, 给玩家看的一句话)。四个状态**没有合并项**：合并了就分不出下一步。
CANDIDATE_FAILURES: dict[str, tuple[str, str]] = {
    "expired": (
        ErrorCode.EXPIRED_EXECUTION_ID,
        "该配装候选已过期，请重新求解并确认。",
    ),
    "consumed": (
        ErrorCode.USED_EXECUTION_ID,
        "该配装候选已经用过了（一次确认只能执行一次），要再装一次请重新求解并确认。",
    ),
    # 别人的候选与不存在的候选回**同一句话**：不泄露"这个 ID 存在，只是不属于你"。
    "unknown": (
        ErrorCode.UNKNOWN_EXECUTION_ID,
        "该配装候选已失效或不属于当前玩家，请重新求解并确认。",
    ),
    "player_mismatch": (
        ErrorCode.UNKNOWN_EXECUTION_ID,
        "该配装候选已失效或不属于当前玩家，请重新求解并确认。",
    ),
}


def candidate_failure(status: str) -> dict | None:
    """状态 → 失败响应；`ok`（以及任何未知状态）给 `None`，由调用方走正常路径。"""
    entry = CANDIDATE_FAILURES.get(status)
    if entry is None:
        return None
    code, message = entry
    return {"success": False, "code": code, "message": message}


def describe_candidate(
    store: "BuildCandidateStore", player_name: str, execution_id: str
) -> dict:
    """按候选 ID 取回签发的那份方案（只读、不焚烧），转成工具层要的 dict。"""
    build, status = store.resolve(execution_id, player_name)
    failure = candidate_failure(status)
    if failure is not None:
        return failure
    return {"success": True, "build": build.model_dump(mode="json")}
