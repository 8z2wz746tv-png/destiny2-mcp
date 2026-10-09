"""求解输入的**指纹**：一次求解用的是哪一版账号护甲现场（`snapshot_version`）＋
"这套里的件还是不是那些件"（`substance_version`）。

口径与理由见 `docs/adr/032-write-prep-order-and-baseline.md`；模块拆出来的原因：老文件
（`snapshot_version.py`）贴着体量上限，而这两半是同一件事，得放一起。
"""

from __future__ import annotations

import hashlib
import json
from typing import TYPE_CHECKING

from .constants import SOLVER_SLOTS

if TYPE_CHECKING:
    from .models import InventorySnapshot

__all__ = ["snapshot_version", "substance_version"]


def _rows(snapshot: InventorySnapshot, *, with_place: bool) -> list[dict]:
    """每件一行。"放哪儿 / 穿没穿"（`with_place`）单列，见 `substance_version`。"""
    rows = []
    for slot in SOLVER_SLOTS:
        for armor in snapshot.get_slot(slot):
            row = {
                "instance_id": armor.item_instance_id,
                "item_hash": armor.item_hash,
                "slot": armor.slot,
                "stats": armor.stats.model_dump(),
                "energy_capacity": armor.energy_capacity,
                "installed_mod_energy": armor.installed_mod_energy,
            }
            if with_place:
                row.update({
                    "source_location": armor.source_location,
                    "source_character_id": armor.source_character_id,
                    "is_equipped": armor.is_equipped,
                })
            rows.append(row)
    return rows


def _digest(snapshot: InventorySnapshot, *, with_place: bool) -> str:
    definitions = sorted(
        (d.model_dump() for d in snapshot.stat_mod_definitions), key=lambda d: d["hash"]
    )
    payload = json.dumps(
        {
            "armor": sorted(_rows(snapshot, with_place=with_place), key=lambda r: r["instance_id"]),
            "stat_mod_definitions": definitions,
        },
        ensure_ascii=True, sort_keys=True, separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def snapshot_version(snapshot: InventorySnapshot) -> str:
    """这份快照的确定性版本（同样内容 → 同样字符串，与插入顺序无关）。"""
    return _digest(snapshot, with_place=True)


def substance_version(snapshot: InventorySnapshot) -> str:
    """指纹里"**这件是什么**"的一半：不含"放哪儿 / 穿没穿"。

    腾格与顶下**必然**改位置（那是工具的业务，见 ADR-029/030）；属性、能量、已装模组、件与件数
    变了才是"确认的那套不算数了"。用途见 `make_room.prepare_build_write`。
    """
    return _digest(snapshot, with_place=False)
