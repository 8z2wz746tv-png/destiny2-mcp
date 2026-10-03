"""照抄来的功能模组在执行时的现场决策（从 `loadout_mod_sockets` 拆出来的一个 Mixin）。

`_prepare_mod_operations` 那条路是"求解器算出来的模组，装不下就是预检失败"，照抄来的功能模组
是**另一个口径**：同名多版本里挑"这一位这件护甲插得进"的（实测「鼓舞爆弹」便宜那版
`unlock_state=false`）→ 能插优先、其次挑便宜的；插不进（组件 207 的清单里没有）→ 跳过 + 点名
（与 `equip_mod` 的 `writable=false` 同口径）；能量不够 → **也跳过 + 点名**，不替玩家去拆别的模组、
不许让整条配装失败或回退（玩家要的是"照抄作者这套"，抄不上的如实报出来就够了）；
判能量看**最终净额**、写下去按**先腾后占** —— 两道口子的由来见 `_plan_functional_mods`。
"""

from __future__ import annotations

from typing import Any

from ..utils.hash_utils import to_unsigned
from ..models import ModOperation


class FunctionalModMixin:
    """宿主提供：`_find_mod_socket` / `_plug_energy_cost` / `plug_is_insertable` / `_manifest`。"""

    async def _plan_functional_mods(
        self,
        item: Any,
        sockets: list[dict],
        sockets_cache: dict,
        insertable: dict[int, set[int]] | None,
        assigned_sockets: set[int],
        membership_id: str,
        membership_type: int,
        *,
        used_energy: int,
        capacity: int,
    ) -> list[ModOperation]:
        """把 `item.functional_mod_groups` 变成要写的操作（`mod` / `keep` / `blocked`）。

        `used_energy` = **属性模组已经安排完之后**的已用能量：照抄来的模组只在剩下的空间里装，不够就跳过。

        **为什么分两趟**（真机 2026-10-03「光芒领主护腿」白丢一颗）：1 号槽的「缚丝回收器」
        （3 点，替掉现装的 1 点「复原」）按"此刻"被判成要 12/11 跳过，而同一批里排在它后面的
        「谐振回收器」（1 点，替掉现装的 3 点「宽恕」）会腾出 2 点 —— 最终只要 10/11，它本来
        就装得下。所以第一趟只挑版本、只算净额（目标 - 当前），第二趟按净额从小到大判：判的是最终净额，
        写下去又是先腾后占（游戏逐颗校验能量，顺序反了瞬时值超上限、上游直接拒）。
        """
        operations: list[ModOperation] = []
        pending: list[tuple[int, int, ModOperation]] = []
        for group in item.functional_mod_groups or []:
            plug_hash, socket_index, blocked_reason = await self._choose_functional_variant(
                item, group, sockets, sockets_cache, insertable,
                assigned_sockets, membership_id, membership_type,
            )
            if socket_index is None:
                # 一个版本都插不进这一位角色：不写、如实报（ADR-013 的口径）
                operations.append(ModOperation("blocked", plug_hash, -1, blocked_reason))
                continue
            assigned_sockets.add(socket_index)
            current_hash = int(sockets[socket_index].get("plugHash", 0) or 0)
            if current_hash and to_unsigned(current_hash) == to_unsigned(plug_hash):
                # 已经装着它：想要的状态成立，不写（上游对"再装一次"回 1679 且退避重试，实测白花约 10 秒）
                operations.append(ModOperation("keep", plug_hash, socket_index))
                item.mod_sockets[socket_index] = plug_hash
                continue
            target_cost = self._plug_energy_cost(plug_hash) or 0
            current_cost = self._plug_energy_cost(current_hash) if current_hash else 0
            pending.append((target_cost - (current_cost or 0), len(pending),
                            ModOperation("mod", plug_hash, socket_index)))

        projected = used_energy
        for delta, _, operation in sorted(pending, key=lambda row: row[:2]):
            if projected + delta > capacity:
                operations.append(ModOperation(
                    "blocked", operation.plug_hash, operation.socket_index,
                    f"能量不够（要 {projected + delta}/{capacity}）：这一颗跳过，其余照抄的模组与六维属性模组照常安装",
                ))
                continue
            projected += delta
            operations.append(operation)
            # 记进 `mod_sockets`：写完之后 `_verify_loadout` 要按它逐槽回读核对
            item.mod_sockets[operation.socket_index] = operation.plug_hash
        return operations

    async def _choose_functional_variant(
        self,
        item: Any,
        group: list[int],
        sockets: list[dict],
        sockets_cache: dict,
        insertable: dict[int, set[int]] | None,
        assigned_sockets: set[int],
        membership_id: str,
        membership_type: int,
    ) -> tuple[int, int | None, str]:
        """同名版本里挑一版：**能插优先 → 已经装着 → 便宜**。

        返回 `(挑中的插件 hash, 槽位或 None, 跳过原因)`；一个都插不进时 hash 仍返回（回执要指名道姓）。
        """
        best: tuple[tuple[int, int, int], int, int] | None = None
        for variant in group:
            variant_hash = int(variant)
            socket_index = await self._find_mod_socket(
                item.item_instance_id, item.item_hash, variant_hash,
                membership_id, membership_type, sockets_cache,
                excluded_socket_indices=assigned_sockets,
            )
            if socket_index is None or socket_index >= len(sockets):
                continue
            current_hash = int(sockets[socket_index].get("plugHash", 0) or 0)
            unlocked = None
            if insertable is not None:
                unlocked = self.plug_is_insertable(
                    insertable,
                    self._manifest.get_item_definition(item.item_hash),
                    socket_index,
                    variant_hash,
                    current_hash,
                )
            score = (
                0 if unlocked is not False else 1,   # 这一位明确插不进的那版排最后
                0 if current_hash and to_unsigned(current_hash) == to_unsigned(variant_hash) else 1,
                self._plug_energy_cost(variant_hash) or 0,   # 同效果挑便宜的
            )
            if best is None or score < best[0]:
                best = (score, variant_hash, socket_index)
        if best is None:
            # hash 仍要回去：回执要指名道姓哪一颗没插上
            return int(group[0]), None, "同名版本都不在这一位角色的可插入清单里（游戏里同样装不上）"
        return best[1], best[2], ""
