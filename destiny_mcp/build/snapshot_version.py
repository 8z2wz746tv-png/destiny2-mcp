"""求解输入的**指纹**：一次求解用的是哪一版账号护甲现场。

`find_build` 把它写进候选（`CanonicalBuild.snapshot_version`），`equip_build` 在写之前
重算一遍比对 —— 对不上就拒绝执行、要求重新求解。所以它是"求解 ↔ 执行"这条链的
**一致性契约**，不是 `BuildService` 的内部细节，放在领域层。

为什么逐字段列出来而不是 `model_dump()` 整个快照：指纹只该随"影响求解结果、且写入前复算
得出来"的字段变化；整包 dump 会让任何新字段悄悄改变已有候选的指纹。

- **进**：护甲行（实例/hash/槽位/六维/能量上限/位置/是否穿着）、调谐模组定义，外加
  `installed_mod_energy`（已装部位模组的能量占用）。最后这一项是求解输入（决定这件还剩多少
  能量给属性模组），写入那一刻却**复算不出来**（求解预算是 `max(已装, 预留额度)`，预留来自
  请求参数）。**只加这一项**、不把模组清单塞进来：同能量的两颗互换不影响预算，为它作废候选
  是让用户白等几十秒。真机后果与取舍的完整账见 `Armor.installed_mod_energy` 的字段注释。
- **不进**：其余执行现场（各格占用、当前穿着的金装、`Armor.execution_blocker`）—— 它们决定
  "这件这次装不装得上"，写入前按**当时**的现场复检（`services/build_execution_guard`，判据
  `build/execution_feasibility`）。进指纹的代价是"刚求解完、捡到一件护甲 / 换件金装就被告知
  库存变了、请重新求解"，而这两条前提本来就有明确出路（腾一格 / 先顶下冲突的金装）。
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
                "installed_mod_energy": armor.installed_mod_energy,
                "source_location": armor.source_location,
                "source_character_id": armor.source_character_id,
                "is_equipped": armor.is_equipped,
            })
    definitions = sorted(
        (d.model_dump() for d in snapshot.stat_mod_definitions), key=lambda d: d["hash"]
    )
    payload = json.dumps(
        {
            "armor": sorted(rows, key=lambda row: row["instance_id"]),
            "stat_mod_definitions": definitions,
        },
        ensure_ascii=True, sort_keys=True, separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()
