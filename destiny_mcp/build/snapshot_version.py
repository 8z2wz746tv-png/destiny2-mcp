"""求解输入的**指纹**：一次求解用的是哪一版账号护甲现场。

`find_build` 把它写进候选（`CanonicalBuild.snapshot_version`），`equip_build` 在写之前
重算一遍比对 —— 对不上就拒绝执行、要求重新求解。所以它是"求解 ↔ 执行"这条链的
**一致性契约**，不是 `BuildService` 的内部细节，放在领域层。

为什么必须逐字段列出来而不是 `model_dump()` 整个快照：指纹只该随"影响求解结果的字段"
变化。整包 dump 会把 `execution` 这类**执行现场**也算进去 —— 那正是我们**故意**不要的
（见下），而且任何新字段都会悄悄改变已有候选的指纹，让用户手里的候选莫名失效。

**执行现场（`snapshot.execution`）刻意不进指纹**：它的 `used` 计数会随"用户捡到一件护甲"
而变，进来的后果是"刚求解完、一捡东西就被告知库存变了、请重新求解"。执行前提的变化
留给写入路径去判（上游会如实报 `NoRoomInDestination`）。
"""

from __future__ import annotations

import hashlib
import json
from typing import TYPE_CHECKING

from .constants import SOLVER_SLOTS

if TYPE_CHECKING:
    from .models import InventorySnapshot

__all__ = ["snapshot_version"]


def snapshot_version(snapshot: InventorySnapshot) -> str:
    """这份快照的确定性版本（同样内容 → 同样字符串，与插入顺序无关）。"""
    rows = []
    for slot in SOLVER_SLOTS:
        for armor in snapshot.get_slot(slot):
            rows.append({
                "instance_id": armor.item_instance_id,
                "item_hash": armor.item_hash,
                "slot": armor.slot,
                "stats": armor.stats.model_dump(),
                "energy_capacity": armor.energy_capacity,
                "source_location": armor.source_location,
                "source_character_id": armor.source_character_id,
                "is_equipped": armor.is_equipped,
            })
    mod_definitions = sorted(
        (
            definition.model_dump()
            for definition in snapshot.stat_mod_definitions
        ),
        key=lambda definition: definition["hash"],
    )
    payload = json.dumps(
        {
            "armor": sorted(rows, key=lambda row: row["instance_id"]),
            "stat_mod_definitions": mod_definitions,
        },
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()
