"""写入失败后的下一步话术（从 `_write_failure_hints` 抽出来：那张表加了平台类条目就撞体量闸）。

为什么单独一个模块：这张表是**话术**，跟着上游原文的关键词走，会随实测不断加条目；
放在信封模块里会把"形状"和"措辞"混在一处。信封只负责形状，措辞按域分开放。
"""

from __future__ import annotations

from typing import Any


# 写入失败时，游戏给的原因往往配得上一句「那就这么做」。这里按原因的关键词补
# next_actions —— 以前失败分支只有 candidates（多数是空的），调用方拿不到下一步，
# 只能自己想到「先换下来再搬」。
_WRITE_FAILURE_HINTS: tuple[tuple[tuple[str, ...], str], ...] = (
    (("UniqueEquipRestricted", "只能装备一件", "一件异域"),
     "同类的异域只能穿一件（异域**武器**一件 + 异域**护甲**一件，两类互不冲突）：目标与已经"
     "穿着的那件同属一类，先从该角色背包里挑一件**非异域的同部位**装备穿上去顶下它，再装目标"
     '（`inventory_assistant` 的 `intent="get"`：武器用 `item_type="武器"`，护甲用 `armor_slot=…`）。'),
    (("equipped item", "CannotPerformActionOnEquippedItem", "已装备"),
     "目标正装备在身上：先用 intent=\"equip\" 把同槽位的另一件换上，再对它执行 transfer/move。"),
    (("not found in the character's inventory", "ItemNotFound", "不在该角色身上"),
     "`EquipItem` 只接受**在该角色身上**的实例：仓库或别的角色身上的要先搬过来"
     '（`intent="move"`，destination 传角色名；他背包满了会撞 NoRoomInDestination），'
     "或者直接换用他背包里已有的那件。"),
    (("CannotEquip", "金装冲突", "1641", "DestinyCannotEquipItem"),
     "金装全身只能穿一件：`equip_loadout` 会先顶下冲突的那件再发批量，顶不下来会点名要你先脱哪件。"),
    (("No space", "空间不足", "InventoryFull", "NoRoomInDestination"),
     "目标位置空间不足：`equip_build`/`move`/`equip_loadout` 会先自动腾一件再重试（见 steps 的 `make_room`）。"),
    # ── 平台/位置类（2026-10-18 真机：equip_build 五件全 1671，而 transfer 五件全成功）──────
    # 这四个码语义**各不相同**，别合并成「稍后重试」：1671 是位置、1672 是限流、1673 是副作用。
    (("1671", "AtThisLocation", "CannotPerformActionAtThisLocation"),
     "角色当前所在的位置/活动里不能换装（1671）：**回轨道或社交空间**后用**同一个 execution_id** 重试 —— "
     "求解结果没错，别重新求解；搬运（transfer）不受这条限制。"),
    (("1670", "LegacyPlatformInaccessible"),
     "上游回了 1670（旧平台不可访问）：这个动作在当前客户端/平台上不可用，换官方客户端或换一个动作。"),
    (("1672", "ThrottledByGameServer"),
     "被游戏服务端限流（1672）：**等一会儿**再重试同一个调用，不要连续重发（越重发越慢）。"),
    (("1673", "ItemNotTransferrableHasSideEffects"),
     "这件不能直接转移（1673，有副作用）：别硬搬，换一件，或先在游戏里处理它。"),
)


def write_failure_hints(payload: dict[str, Any]) -> list[str]:
    """从写入失败的 payload 里挑出可用的下一步建议（挑不到就返回空）。

    **haystack 必须包含 `steps[].detail`**（2026-10-18 真机教训）：`equip_build` 的位置性失败
    顶层只有笼统一句 `build_equip_failed`，真正的 `equipStatus=1671` 只出现在
    `steps[]` 的 `equip_many.detail` 里 —— 只看 code+message 时，按上游原文关键词写的所有条目
    （含 `1641`/`CannotEquip`）都等于死代码。
    """
    steps = payload.get("steps") or (payload.get("result") or {}).get("steps") or []
    haystack = " ".join(
        [f"{payload.get('code', '')} {payload.get('message', '')}"]
        + [f"{s.get('action', '')} {s.get('detail', '')}" for s in steps if isinstance(s, dict)]
    )
    return [hint for keywords, hint in _WRITE_FAILURE_HINTS if any(k in haystack for k in keywords)]
