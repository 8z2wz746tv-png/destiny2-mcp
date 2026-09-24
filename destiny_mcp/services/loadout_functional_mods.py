"""照抄来的功能模组在执行时的现场决策（从 `loadout_mod_sockets` 拆出来的一个 Mixin）。

为什么单独一块：`_prepare_mod_operations` 那条路是"求解器算出来的模组，装不下就是预检失败"，
而照抄来的功能模组是**另一个口径**——

- 同名有多个版本，只有一版能被这一位角色的这件护甲插进去（实测「鼓舞爆弹」2 点/1 点两版，
  便宜的那版 `unlock_state=false`）：**能插优先，其次挑便宜的**（同效果没必要多占能量）；
- 插不进（组件 207 的清单里没有）→ 跳过 + 点名（与 `equip_mod` 的 `writable=false` 同一口径）；
- 能量不够 → **也跳过 + 点名**，不替玩家去拆别的模组、更不许让整条配装失败或回退：
  玩家要的是"照抄作者这套"，抄不上的那几颗如实报出来就够了。
"""

from __future__ import annotations

from typing import Any

from ..utils.hash_utils import to_unsigned
from ..models import ModOperation


class FunctionalModMixin:
    """宿主（`LoadoutEquipmentService`）提供：`_find_mod_socket` / `_plug_energy_cost` /
    `plug_is_insertable` / `_manifest`。"""

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

        `used_energy` 是**属性模组已经安排完之后**的已用能量：照抄来的模组只在剩下的空间里装，
        不够就跳过（不去拆玩家别的模组）。返回的操作由调用方并进同一个 `steps` 里。
        """
        operations: list[ModOperation] = []
        projected = used_energy
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
                # 这个槽已经装着它：想要的状态已经成立，不写
                # （上游对"再装一次"回 1679，客户端还会退避重试，真机实测每颗白花约 10 秒）
                operations.append(ModOperation("keep", plug_hash, socket_index))
                item.mod_sockets[socket_index] = plug_hash
                continue

            target_cost = self._plug_energy_cost(plug_hash) or 0
            current_cost = self._plug_energy_cost(current_hash) if current_hash else 0
            delta = target_cost - (current_cost or 0)
            if projected + delta > capacity:
                operations.append(ModOperation(
                    "blocked", plug_hash, socket_index,
                    f"能量不够（要 {projected + delta}/{capacity}）：这一颗跳过，"
                    "其余照抄的模组与六维属性模组照常安装",
                ))
                continue
            projected += delta
            operations.append(ModOperation("mod", plug_hash, socket_index))
            # 记进 `mod_sockets`：写完之后 `_verify_loadout` 要按它逐槽回读核对
            item.mod_sockets[socket_index] = plug_hash
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

        返回 `(挑中的插件 hash, 槽位或 None, 跳过时的原因)`。一个版本都插不进时，
        hash 仍返回（回执要指名道姓），槽位是 `None`。
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
            return int(group[0]), None, (
                "同名版本都不在这一位角色的可插入清单里（游戏里同样装不上）"
            )
        return best[1], best[2], ""
