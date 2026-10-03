"""写账号之前的最后一道**只读**闸：这份候选在**此刻**的账号现场还成不成立。

`equip_build` 里"用户确认 → 真写账号"之间只隔着这一段。它漏一条，用户看到的就是
"以为在装备、实际在撞上游 500"——真机实测（2026-10-03，两次各 **0 颗模组落地**）。

为什么单独成模块：`build_service` 贴着体积上限（加一道闸得先腾位置，历史同款拆分：
`_snapshot_version` → `build/snapshot_version.py`、候选暂存 → `build_candidates.py`），
而这一段是"一次重取 profile + 四道比对"的完整单元 —— 四步的顺序本身就是结论，分散在两处
读不出"为什么先查它"。判据不在这里：执行前提的判据只有 `build/execution_feasibility`
一份，话术在 `build/execution_diagnosis`；指纹口径只有 `build/snapshot_version` 一份。

顺序不能换（每一步都对应一类白跑）：

1. **重取**一份账号现场（`get_armor_snapshot`）—— 不复用求解那份：`find` → 用户确认之间，
   人可能已经回过游戏。两次读取**本来就不是同一次读**（`find` 还带着照抄模组的预留能量、
   两次读到的 Manifest 桶定义也可以不同）；
2. **执行前提复检**：格满（仓库件搬不进来 → 上游 500 `DestinyNoRoomInDestination`）与
   "与当前穿着的金装冲突"（→ `equipStatus=1641`）。**放在指纹比对之前**：
   `snapshot.execution` **刻意不进指纹**（见 `snapshot_version`），所以"指纹一样"证明不了
   这两条还成立 —— 判据读不到时（桶定义缺失、认不出唯一角色）`read_facts` 按设计一件都不拦，
   那种候选**从来没被这条前提判过**；而这两条本来就是"写入前必须按当时的现场再说一遍"的
   那一类（`snapshot_version` 的 docstring 写着这句承诺，在本次改动之前没有任何地方兑现）。
   先报这一条，用户拿到的下一步才是对的（腾一格 / 先顶下金装，而不是"重新求解"）。
   读的就是求解那一刻写在件上的 `execution_blocker`（判据与出路唯一出处
   `build/execution_feasibility`）—— 换一份现场重读，不是第二份判据；
3. **库存指纹比对**（`snapshot_version`）：除执行前提之外的任何变化都在这里兜住
   （换了件、改了模组、穿上脱下…）；
4. **五个实例还在、hash 没变**：指纹只比"护甲行整体"，这一条把"确认的那五件本身"钉死
   （被分解/被替换过的实例不许拿旧 hash 去写）。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ..build.constants import SOLVER_SLOTS
from ..build.snapshot_version import snapshot_version
from ..build_contracts import CanonicalBuild
from ..error_codes import ErrorCode

if TYPE_CHECKING:  # 只用于标注：服务实现由 `BuildService` 注入，这里不 import 服务层
    from .inventory_service import InventoryService

__all__ = ["recheck_confirmed_build"]


async def recheck_confirmed_build(
    *,
    inventory: InventoryService,
    player_name: str,
    character: str,
    build: CanonicalBuild,
) -> dict | None:
    """过闸给 `None`；过不去给一份可直接当服务层结果返回的拒绝载荷。

    拒绝的理由（`blockers`）逐件一条中文：**哪条约束 + 出路**，都是
    `execution_feasibility` 写在件上的原句（工具层连同 `message` 一起交出去，
    读的人不必回查账号就知道下一步做什么）。
    """
    snapshot = await inventory.get_armor_snapshot(player_name, character)
    # 五个部位一次收齐（`SOLVER_SLOTS` 是槽位顺序的唯一出处，别在这另写一个元组）
    fresh = {
        armor.item_instance_id: armor
        for slot in SOLVER_SLOTS
        for armor in snapshot.get_slot(slot)
    }
    refusals = [
        fresh[item.item_instance_id].execution_blocker
        for item in build.items
        if item.item_instance_id in fresh
        and fresh[item.item_instance_id].execution_blocker
    ]
    if refusals:
        return {
            "success": False,
            "code": ErrorCode.EXECUTION_PRECONDITION_FAILED,
            "message": "这次确认的配装**现在装不上**：" + "；".join(refusals),
            "blockers": refusals,
        }

    current_version = snapshot_version(snapshot)
    if current_version != build.snapshot_version:
        return {
            "success": False,
            "code": ErrorCode.STALE_INVENTORY_SNAPSHOT,
            "message": "生成配装后库存或护甲状态已变化；为避免装备另一套，请重新求解并确认。",
            "expected_snapshot_version": build.snapshot_version,
            "current_snapshot_version": current_version,
        }

    for item in build.items:
        current = fresh.get(item.item_instance_id)
        if current is None or current.item_hash != item.item_hash:
            return {
                "success": False,
                "code": ErrorCode.EXACT_ITEM_MISSING,
                "message": f"确认的装备实例 '{item.item_instance_id}' 已不存在或发生变化。",
            }
    return None
