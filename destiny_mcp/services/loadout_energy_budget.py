"""模组的能量预算与腾挪（从 `loadout_mod_sockets` 拆出的一块纯决策逻辑）。

为什么单独一块：`_prepare_mod_operations` 混了两件事 —— "这颗模组该进哪个槽"（插槽匹配）与
"装不下时拿哪几颗旧模组去换能量"（预算）。前者要读插槽与可插入清单，后者只是一道算术 + 挑选规则。
体量闸要求先抽代码（`loadout_mod_sockets.py` 已贴着上限），而这块的输入输出都只是数字与操作，
抽出来不用带任何 I/O。

口径（真机 2026-09-22 的教训）：只为**求解器算出来的属性模组**腾能量，且腾的必须是"可替换"的槽
（有默认插件、且当前插件真占着能量）；腾不出来就**如实失败**（预检失败 → 上层不动模组），
不许硬写 —— 硬写的后果是 12/11 那种装不下的方案跑到一半炸掉。
"""

from __future__ import annotations

from typing import Any

from ..exceptions import TransferError
from ..models import ModOperation


def plan_energy_clearing(
    owner: Any,
    item: Any,
    sockets: list[dict],
    assigned_sockets: set[int],
    *,
    deficit: int,
) -> list[ModOperation]:
    """为 `deficit` 点能量挑选"把槽恢复成默认插件"的操作（腾不出来就抛 `TransferError`）。

    `owner` 提供 `_manifest` / `_plug_category_hash` / `_plug_energy_cost` / `_MOD_CATEGORY_HASHES`
    / `_PLUG_CAT_TUNING`（就是 `ModSocketMixin` 自己）。
    """
    if deficit <= 0:
        return []
    item_definition = owner._manifest.get_item_definition(item.item_hash)
    socket_entries = (
        (item_definition.get("sockets") or {}).get("socketEntries", [])
        if isinstance(item_definition, dict)
        else []
    )
    candidates: list[tuple[int, int, int]] = []
    for socket_index, socket in enumerate(sockets):
        if socket_index in assigned_sockets or socket_index >= len(socket_entries):
            continue
        current_hash = socket.get("plugHash", 0)
        default_hash = socket_entries[socket_index].get("singleInitialItemHash", 0)
        if not current_hash or not default_hash or current_hash == default_hash:
            continue
        current_category = owner._plug_category_hash(current_hash)
        default_category = owner._plug_category_hash(default_hash)
        if current_category == owner._PLUG_CAT_TUNING:
            continue
        if (
            current_category not in owner._MOD_CATEGORY_HASHES
            and default_category not in owner._MOD_CATEGORY_HASHES
        ):
            continue
        current_cost = owner._plug_energy_cost(current_hash)
        default_cost = owner._plug_energy_cost(default_hash)
        if current_cost is None or default_cost is None:
            raise TransferError(
                "模组预检",
                f"无法计算 '{item.name}' 插槽 {socket_index} 的能量。",
            )
        freed = current_cost - default_cost
        if freed > 0:
            candidates.append((freed, socket_index, default_hash))

    if sum(freed for freed, _, _ in candidates) < deficit:
        raise TransferError(
            "模组预检",
            f"'{item.name}' 没有足够的可替换模组来腾出 {deficit} 点能量。",
        )

    operations: list[ModOperation] = []
    remaining = sorted(candidates)
    while deficit > 0:
        sufficient = [candidate for candidate in remaining if candidate[0] >= deficit]
        chosen = min(sufficient) if sufficient else remaining[0]
        remaining.remove(chosen)
        freed, socket_index, default_hash = chosen
        operations.append(ModOperation("clear", default_hash, socket_index))
        deficit -= freed
    return operations
