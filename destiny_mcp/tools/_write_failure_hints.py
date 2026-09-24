"""写入失败后的下一步话术（从 `_responses` 拆出）。

为什么单独一个模块：这张表是**话术**，跟着上游原文的关键词走，会随实测不断加条目；
放在信封模块里会把"形状"和"措辞"混在一处（`_responses.py` 因此涨到 213 行，撞了体量闸）。
信封只负责形状，措辞按域分开放 —— 这条与 `_*_branches.py` 的分法一致。
"""

from __future__ import annotations

from typing import Any


# 写入失败时，游戏给的原因往往配得上一句「那就这么做」。这里按原因的关键词补
# next_actions —— 以前失败分支只有 candidates（多数是空的），调用方拿不到下一步，
# 只能自己想到「先换下来再搬」。
_WRITE_FAILURE_HINTS: tuple[tuple[tuple[str, ...], str], ...] = (
    (
        ("UniqueEquipRestricted", "只能装备一件", "一件异域"),
        "同类的异域只能穿一件（异域**武器**一件 + 异域**护甲**一件，两类互不冲突）：目标与已经"
        "穿着的那件同属一类，先从该角色背包里挑一件**非异域的同部位**装备穿上去顶下它，再装目标"
        '（`inventory_assistant` 的 `intent="get"`：武器用 `item_type="武器"`，护甲用 `armor_slot=…`）。',
    ),
    (
        ("equipped item", "CannotPerformActionOnEquippedItem", "已装备"),
        "目标正装备在身上：先用 intent=\"equip\" 把同槽位的另一件换上（或 equip 到别的角色），再对它执行 transfer/move。",
    ),
    (
        ("not found in the character's inventory", "ItemNotFound", "不在该角色身上"),
        "`EquipItem` 只接受**在该角色身上**的实例：仓库或别的角色身上的要先搬过来"
        '（`intent="move"`，destination 传角色名；他背包满了会撞 NoRoomInDestination），'
        "或者直接换用他背包里已有的那件。",
    ),
    (
        ("No space", "空间不足", "InventoryFull", "NoRoomInDestination"),
        "目标位置空间不足：先清出位置，或换一个目标角色/仓库。",
    ),
)


def write_failure_hints(payload: dict[str, Any]) -> list[str]:
    """从写入失败的 payload 里挑出可用的下一步建议（挑不到就返回空）。"""
    haystack = f"{payload.get('code', '')} {payload.get('message', '')}"
    return [hint for keywords, hint in _WRITE_FAILURE_HINTS if any(k in haystack for k in keywords)]
