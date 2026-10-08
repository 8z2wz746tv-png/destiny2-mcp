"""Bungie 物品 `state` 位（组件里的 `state` 整数）——**唯一出处**。

现在只用到 bit0（游戏内锁定）。以前这里有两处内联写法（`bool(int(raw.get("state") or 0) & 1)`），
抄第二遍就是第二个出处 —— ADR-029 的「锁定的绝不腾」两边都要读它，所以收到这里。
"""

from __future__ import annotations

#: `DestinyItemState.Locked`
_LOCKED_BIT = 1


def is_locked(state: object) -> bool:
    """这一件在游戏里锁了吗？读不到（缺字段/不是数字）当**没锁** —— 缺值不拿"锁了"当默认。"""
    try:
        return bool(int(state) & _LOCKED_BIT)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return False
