"""护甲模组插槽：读取已装模组、规划腾挪、查找并写入插槽。

以 mixin 挂在 LoadoutEquipmentService 上，通过 self 使用 manifest / bungie / resolver，
所以调用点不用改。新方法加在这里，不要再往 loadout_equipment_service.py 堆。
"""

from __future__ import annotations

from ..exceptions import TransferError
from ..manifest import ManifestManager
from ..models import LoadoutItem, ModOperation, MoveItemStep
from ..utils.hash_utils import to_unsigned
from . import profile_components, write_readback
from .loadout_energy_budget import plan_energy_clearing
from .loadout_plug_lookup import PlugLookupMixin


def plug_already_installed(result: dict | None) -> bool:
    """1679 `DestinySocketAlreadyHasPlug`：这个槽**已经装着这颗**了。

    上游却把它包在 HTTP 500 里回（"The request to modify an item failed. Refresh the item
    and try again."），照字面读会当成失败。真相是**想要的状态已经成立**，不是错误。

    为什么必须单独判：`equip_build` 的模组循环把 `ErrorCode != 1` 当失败，一颗"已经装着"
    就会让整条配装回退 —— 2026-09-23 真机实测那次回退花了 **3.5 分钟**（5 件搬回仓库 +
    重新装回原来的护甲 + 回读核对），而账号其实一直是好的。`equip_mod` 那条路早就是这么判的，
    这次把它抽成两处共用的一条判据。
    """
    if not result:
        return False
    text = f"{result.get('Message', '')} {result.get('ErrorStatus', '')}"
    return result.get("ErrorCode") == 1679 or "DestinySocketAlreadyHasPlug" in text


class ModSocketMixin(PlugLookupMixin):
    """护甲模组一侧的读取与写入规划。"""

    def keep_mod_step(self, item: LoadoutItem, mod_hash: int, socket_index: int) -> MoveItemStep:
        """"已经装着、未改动"的回执步骤（预检认出来的那种，没调用上游）。"""
        return MoveItemStep(
            action="mod",
            detail=(
                f"'{item.name}' 插槽 {socket_index} 已经装着 "
                f"'{self.mod_label(mod_hash)}'，未改动"
            ),
            success=True,
        )

    def blocked_mod_step(
        self, item: LoadoutItem, mod_hash: int, socket_index: int, reason: str
    ) -> MoveItemStep:
        """这一位装不上这颗的回执：写不了就说清为什么，且**不连累整条配装**。"""
        return MoveItemStep(
            action="mod_blocked",
            detail=(
                f"'{item.name}' 插槽 {socket_index} 的模组 "
                f"'{self.mod_label(mod_hash)}' 装不上：{reason}"
            ),
            success=False,
        )

    def mod_label(self, mod_hash: int) -> str:
        """模组 hash → 中文名（查不到就退回 hash 本身）。

        回执是给人看的：写 hash 等于让调用方再查一次 Manifest，或者干脆再逐件查一遍护甲 ——
        后者实测会让一次装备链路多 5 次往返。

        **名字只从 `get_item_name()` 取**：`get_item_info()` 给的是规范化过的小字典（键是 `name`，
        没有 `displayProperties`），从它身上读 `displayProperties` 永远读空、一路退回 hash ——
        真机 2026-09-24 撞见：文档写着"steps 里的模组写中文名"，实际发出去的全是 hash
        （单测没抓到是因为替身返回的是原始定义）。
        """
        name = self._manifest.get_item_name(mod_hash)
        return str(name) if name else str(mod_hash)

    _MOD_CATEGORY_HASHES = set(ManifestManager._ARMOR_MOD_CATEGORIES) | {
        595201146,   # Legacy EnhancementsArtifice
        4065769746,  # Legacy EnhancementsV2
        3315022374,  # Legacy EnhancementsV2ArmorOnly
    }

    # 调谐（`core.gear_systems.armor_tiering.plugs.tuning.mods`）。
    # 实测纠正一条旧假设：它**本来就在** `_MOD_CATEGORY_HASHES` 里
    # （那个集合由 `ManifestManager._ARMOR_MOD_CATEGORIES` 生成，其中 3481777685 → "tuning"），
    # 所以 `_find_mod_socket` 一直允许往调谐槽写、`read_armor_mods` 也一直会读到它。
    # 唯一为它特判的地方是"腾能量"那一段（调谐免费，清掉它腾不出能量，见下面的 continue）。
    _PLUG_CAT_TUNING = 3481777685

    def read_armor_mod_sockets(
        self,
        inst_id: str,
        item_hash: int,
        sockets_data: dict,
    ) -> dict[int, int]:
        """Read exact armor mod socket state, including empty default plugs."""
        if not inst_id:
            return {}
        item_sockets = sockets_data.get(inst_id, {}).get("sockets", [])
        item_definition = self._manifest.get_item_definition(item_hash)
        if not isinstance(item_definition, dict):
            item_definition = {}
        socket_entries = (item_definition.get("sockets") or {}).get(
            "socketEntries", []
        )
        result: dict[int, int] = {}
        for index, socket in enumerate(item_sockets):
            plug_hash = socket.get("plugHash", 0)
            if plug_hash and self._plug_category_hash(plug_hash) in self._MOD_CATEGORY_HASHES:
                result[index] = plug_hash
                continue
            if index >= len(socket_entries):
                continue
            default_hash = socket_entries[index].get("singleInitialItemHash", 0)
            if (
                default_hash
                and self._plug_category_hash(default_hash) in self._MOD_CATEGORY_HASHES
            ):
                result[index] = plug_hash or default_hash
        return result

    def read_armor_mods(self, inst_id: str, sockets_data: dict) -> list[int]:
        """Read equipped mod plug hashes from an armor piece's sockets."""
        if not inst_id:
            return []

        item_sockets = sockets_data.get(inst_id, {}).get("sockets", [])
        mods: list[int] = []

        for socket in item_sockets:
            plug_hash = socket.get("plugHash", 0)
            if not plug_hash:
                continue
            plug_category = self._plug_category_hash(plug_hash)
            if plug_category in self._MOD_CATEGORY_HASHES:
                mods.append(plug_hash)

        return mods

    def mod_write_blocker(self, result: dict, plug_hash: int) -> str:
        """写入被上游挡住时给一句中文原因；不是"挡住"就返回空串。

        三种实测，**原因不同**，都不是重试能成的：

        - 403 `Access not permitted by application scope`：只有**付费**插槽接口
          `InsertSocketPlug` 会这样，它要 `AdvancedWriteActions`（AWA）而本项目没实现那段
          授权流程；免费接口官方明说不需要它，所以撞到这条说明这颗 plug 属于"非免费可逆"那类。
        - 1663 `DestinyItemActionForbidden` / `can only be done in-game`：上游一句含糊话术，
          至少对应"角色不在社交区/轨道/离线"与"这个槽本身禁用"两种，原文照转、不替它下结论。
        - 1675 `DestinyCannotAffordMaterialRequirements`：这个动作**要材料**，免费接口不接。
          2026-09-22 实测撞到它的那次是**调谐**：想装的调谐不在这件护甲允许的清单里（组件 310）
          → 1675；清单里的调谐写入不花材料、直接成功。**算"挡住"而不是硬失败**：装备已经换好了，
          为一颗插件把整条配装回退更糟（也符合"调谐写不进去不回退"这条口径）。
        - 1676 `DestinyFailedPlugInsertionRules`：这颗模组的**插入条件**没满足（实测被拒的
          那些条件里都有「必须在赛季神器中选择」）。这条**游戏里同样装不上**，以前把它归到
          "去游戏里手动装"是错的。
        """
        text = f"{result.get('Message', '')} {result.get('ErrorStatus', '')}"
        if result.get("ErrorCode") == 1675 or "DestinyCannotAffordMaterialRequirements" in text:
            return (
                "要材料（1675）：免费插槽接口不接这类动作。调谐实测只有「这颗不在这件护甲允许的"
                "清单里」那一档会撞上它（清单里的调谐不花材料、能直接写）"
            )
        if "DestinyFailedPlugInsertionRules" in text or result.get("ErrorCode") == 1676:
            conditions = self.plug_insertion_conditions(plug_hash)
            return (
                "插入条件没满足（1676）："
                + ("；".join(conditions) if conditions else "上游没给条件文本")
            )
        if "Access not permitted by application scope" in text:
            return "走到了需要 AdvancedWriteActions（AWA）的付费插槽接口，本项目没实现那段授权"
        if "can only be done in-game" in text:
            return "上游回「This action can only be done in-game.」（原文照转，未替它判断原因）"
        return ""

    def _plug_energy_cost(self, plug_hash: int) -> int | None:
        """Return a plug's manifest energy cost, or None if it is unknown."""
        definition = self._manifest.get_item_definition(plug_hash)
        if not isinstance(definition, dict):
            return None
        raw_cost = ((definition.get("plug") or {}).get("energyCost") or {}).get(
            "energyCost", 0
        )
        try:
            return max(0, int(raw_cost or 0))
        except (TypeError, ValueError):
            return None

    async def _read_sockets(
        self,
        item_instance_id: str,
        membership_id: str,
        membership_type: int,
        *,
        attempts: int | None = None,
        delay: float | None = None,
    ) -> list[dict]:
        """读某件实例的插槽，**带同步窗口重试**。

        真机踩过：`equip_build` 刚把仓库里那件搬过来就立刻读，上游 profile 还没带上它的插槽
        （与写入后回读同一个同步窗口，3～10 秒），于是拿到的是一串空数据，接着每件都报
        "找不到唯一兼容插槽"。这里用 `write_readback` 重试到拿到非空插槽为止。
        """

        async def read() -> list[dict]:
            # 组件必须带上"清单类"那几个：只请求 305 时上游**一个插槽都不返回**
            # （真机实测：只要 305 → 0 件带插槽；带 102/200/201/205/300 → 1627 件）。
            profile = await self._resolver.get_profile(
                membership_id, membership_type, profile_components.INVENTORY_SOCKETS
            )
            return (
                profile.get("itemComponents", {})
                .get("sockets", {})
                .get("data", {})
                .get(item_instance_id, {})
                .get("sockets", [])
            )

        return await write_readback.read_until(
            read, bool, attempts=attempts, delay=delay
        )

    async def _prepare_mod_operations(
        self,
        item: LoadoutItem,
        membership_id: str,
        membership_type: int,
        sockets_cache: dict[str, list[dict]],
        instances_data: dict,
        insertable: dict[int, set[int]] | None = None,
    ) -> list[tuple[str, int, int]]:
        """Order minimal energy-clearing writes before requested mod writes.

        `insertable` = `PlugLookupMixin.insertable_plugs` 的结果。给了就**在计划阶段**先查
        "这一位能不能插这颗"：不能就带着插入条件报错，别等写到一半才让上游回 1676。
        没给（None）或上游没给这个 plug set，都退回不判断。
        """
        sockets = sockets_cache.get(item.item_instance_id) or []
        if not sockets:
            # 插槽缺失/为空 → 现场新读（含同步窗口重试），写回 cache 供后面的能量预检与写入用。
            sockets = await self._read_sockets(
                item.item_instance_id, membership_id, membership_type
            )
            sockets_cache[item.item_instance_id] = sockets
            if not sockets:
                window = int(write_readback.ATTEMPTS * write_readback.DELAY_SECONDS)
                raise TransferError(
                    "模组预检",
                    f"等 {window} 秒仍读不到 '{item.name}' 的插槽数据"
                    "（上游 profile 没同步），这次没改任何模组；稍后重试即可。",
                )
        requested = (
            [
                (mod_hash, socket_index)
                for socket_index, mod_hash in sorted(item.mod_sockets.items())
            ]
            if item.mod_sockets
            else [(mod_hash, None) for mod_hash in item.mods]
        )

        assigned_sockets: set[int] = set()
        keep_operations: list[ModOperation] = []
        blocked_operations: list[ModOperation] = []
        target_operations: list[tuple[int, int, int]] = []
        for mod_hash, exact_socket_index in requested:
            socket_index = (
                exact_socket_index
                if exact_socket_index is not None
                else await self._find_mod_socket(
                    item.item_instance_id,
                    item.item_hash,
                    mod_hash,
                    membership_id,
                    membership_type,
                    sockets_cache,
                    excluded_socket_indices=assigned_sockets,
                )
            )
            if (
                socket_index is None
                or socket_index in assigned_sockets
                or socket_index >= len(sockets)
            ):
                raise TransferError(
                    "模组预检",
                    f"找不到模组 {mod_hash} 在 '{item.name}' 上的唯一兼容插槽。",
                )

            target_cost = self._plug_energy_cost(mod_hash)
            current_hash = sockets[socket_index].get("plugHash", 0)
            if current_hash and to_unsigned(int(current_hash)) == to_unsigned(mod_hash):
                # 这个槽**已经装着这颗**：想要的状态已经成立，不排写入。
                # 上游对"再装一次"回的是 HTTP 500 + 1679（客户端还会退避重试四次，
                # 真机实测每颗白花约 10 秒），而 1679 一旦被当成失败，整条配装还会回退
                # 3.5 分钟。这里提前认出来，回执里仍然如实说"已装着、未改动"。
                assigned_sockets.add(socket_index)
                keep_operations.append(ModOperation("keep", mod_hash, socket_index))
                continue
            if insertable is not None:
                state = self.plug_is_insertable(
                    insertable,
                    self._manifest.get_item_definition(item.item_hash),
                    socket_index,
                    mod_hash,
                    int(current_hash or 0),
                )
                if state is False:
                    # 这一位装不上这颗（组件 207 的清单里没有）。**不抛错、不回退整条配装**：
                    # 装备照换，这一颗如实报"装不上"并给出插入条件（ADR-013 的口径）。
                    conditions = self.plug_insertion_conditions(mod_hash)
                    assigned_sockets.add(socket_index)
                    blocked_operations.append(ModOperation(
                        "blocked", mod_hash, socket_index,
                        "不在 Bungie 给这一位角色的可插入清单里（游戏里同样装不上）。插入条件是："
                        + ("；".join(conditions) if conditions else "上游没给条件文本"),
                    ))
                    continue
            current_cost = self._plug_energy_cost(current_hash) if current_hash else 0
            if target_cost is None or current_cost is None:
                raise TransferError(
                    "模组预检",
                    f"无法从 Manifest 计算 '{item.name}' 的模组能量。",
                )

            assigned_sockets.add(socket_index)
            target_operations.append(
                (target_cost - current_cost, mod_hash, socket_index)
            )

        energy = instances_data.get(item.item_instance_id, {}).get("energy") or {}
        capacity = energy.get("energyCapacity")
        current_used = energy.get("energyUsed")
        if not isinstance(capacity, int) or capacity < 0:
            raise TransferError(
                "模组预检",
                f"无法读取 '{item.name}' 的能量上限。",
            )
        if not isinstance(current_used, int) or current_used < 0:
            costs = [
                self._plug_energy_cost(socket.get("plugHash", 0))
                for socket in sockets
                if socket.get("plugHash", 0)
            ]
            if any(cost is None for cost in costs):
                raise TransferError(
                    "模组预检",
                    f"无法读取 '{item.name}' 的已用能量。",
                )
            current_used = sum(cost for cost in costs if cost is not None)

        projected_used = current_used + sum(
            delta for delta, _, _ in target_operations
        )
        clear_operations = plan_energy_clearing(
            self, item, sockets, assigned_sockets,
            deficit=max(0, projected_used - capacity),
        )

        # 照抄来的功能模组（社区模板写的流派取向）：只在**属性模组安排完之后的剩余能量**里装，
        # 装不下/插不进就跳过并点名（见 `loadout_functional_mods.py` 的口径）。
        if item.functional_mod_groups:
            # 属性模组为了装下自己而腾出来的能量，照抄模组也能用 —— 所以预算是"腾完之后"的，
            # 不是 `projected_used`（那个还是腾之前、带着 deficit 的数）。
            cleared_energy = sum(
                (self._plug_energy_cost(sockets[op.socket_index].get("plugHash", 0)) or 0)
                - (self._plug_energy_cost(op.plug_hash) or 0)
                for op in clear_operations
            )
            functional_operations = await self._plan_functional_mods(
                item,
                sockets,
                sockets_cache,
                insertable,
                assigned_sockets,
                membership_id,
                membership_type,
                used_energy=projected_used - max(cleared_energy, 0),
                capacity=capacity,
            )
        else:
            functional_operations = []

        functional_writes = [op for op in functional_operations if op.action == "mod"]
        return [
            *clear_operations,
            *keep_operations,
            *blocked_operations,
            *[
                ModOperation("mod", mod_hash, socket_index)
                for _, mod_hash, socket_index in sorted(target_operations)
            ],
            *[
                op for op in functional_operations
                if op.action in {"keep", "blocked"}
            ],
            *functional_writes,
        ]

    async def _find_mod_socket(
        self,
        item_instance_id: str,
        item_hash: int,
        mod_hash: int,
        membership_id: str,
        membership_type: int,
        sockets_cache: dict | None = None,
        excluded_socket_indices: set[int] | None = None,
    ) -> int | None:
        """Find the socket index where a mod should be inserted."""
        excluded_socket_indices = excluded_socket_indices or set()
        # 注意 `or {}`：缓存里可能是**一条空列表**（装备刚被搬过来，快照里还没有它的插槽），
        # 那不是"这件的插槽是空的"。以前只判"键在不在"，于是整件装备的每个槽都被
        # `index >= len(sockets_data)` 跳过，最后报"找不到唯一兼容插槽"（真机复现：
        # equip_build 搬完仓库里那件之后，逐件都报这句）。
        if sockets_cache is not None and sockets_cache.get(item_instance_id):
            sockets_data = sockets_cache[item_instance_id]
        else:
            sockets_data = await self._read_sockets(
                item_instance_id, membership_id, membership_type
            )
            if sockets_cache is not None:
                sockets_cache[item_instance_id] = sockets_data

        mod_category = self._plug_category_hash(mod_hash)
        if mod_category not in self._MOD_CATEGORY_HASHES:
            return None

        # 所有 hash 比较都过 `to_unsigned`：profile 给的 plugHash 是无符号的，而
        # `manifest.search` / 方案里的 mod_hash 是**有符号**的（实测 `复原` = -207911122
        # vs 4087056174）。直接比永远不相等 —— 于是"这个槽已经装着它了"永远判不出来，
        # pass 1 还会把它当成"别的同类模组"返回错槽。
        target = to_unsigned(mod_hash)
        item_definition = self._manifest.get_item_definition(item_hash)
        if isinstance(item_definition, dict):
            socket_entries = (item_definition.get("sockets") or {}).get(
                "socketEntries", []
            )
            for index, entry in enumerate(socket_entries):
                if index >= len(sockets_data) or index in excluded_socket_indices:
                    continue
                if to_unsigned(sockets_data[index].get("plugHash", 0)) == target:
                    return index
                for plug_set_hash in {
                    entry.get("reusablePlugSetHash", 0),
                    entry.get("randomizedPlugSetHash", 0),
                }:
                    if not plug_set_hash:
                        continue
                    plug_set = self._manifest.get_definition(
                        "DestinyPlugSetDefinition", plug_set_hash
                    )
                    if isinstance(plug_set, dict) and any(
                        to_unsigned(item.get("plugItemHash", 0)) == target
                        for item in plug_set.get("reusablePlugItems", [])
                    ):
                        return index

        # Pass 1: find socket with matching category that doesn't already have this exact mod
        for i, socket in enumerate(sockets_data):
            if i in excluded_socket_indices:
                continue
            plug_hash = socket.get("plugHash", 0)
            if to_unsigned(plug_hash) == target:
                continue
            if plug_hash:
                current_category = self._plug_category_hash(plug_hash)
                if current_category == mod_category:
                    return i

        # Pass 2: already has this mod
        for i, socket in enumerate(sockets_data):
            if i in excluded_socket_indices:
                continue
            if to_unsigned(socket.get("plugHash", 0)) == target:
                return i

        return None

    async def _insert_armor_mod(
        self,
        item_instance_id: str,
        mod_hash: int,
        socket_index: int,
        character_id: str,
        membership_type: int,
    ) -> dict:
        """插模组：**先走 free 接口**，只有上游回"这个插槽不走免费"时才退回付费接口。

        以前按"能量消耗 > 0"选接口，那是错的：Bungie 的 free 指的是**没有材料消耗**，
        官方文档明确 `InsertSocketPlugFree` 就覆盖 "Perks, **Armor Mods**, Shaders, Ornaments"，
        而且**不需要 `AdvancedWriteActions`**（原文：does not require 'Advanced Write Action'
        authorization and is available to 3rd-party apps）。护甲模组消耗的是能量、不是材料，
        所以它本来就该走 free —— 真机上 5 颗属性模组就是这么装上的（ADR-012）。

        退回付费接口只针对"非免费可逆"的 plug（调谐/强化类）。但**付费那条路我们没实现**：
        它要 AWA 三段流程（`AwaInitializeRequest` → 用户亲自批准 → `AwaGetActionToken`）拿
        `actionToken`，我们没发这个字段，所以退过去也是白退——本来该在免费失败时就如实说清楚。
        """
        result = await self._bungie.insert_socket_plug_free(
            item_instance_id,
            mod_hash,
            socket_index,
            0,
            character_id,
            membership_type,
        )
        if result.get("ErrorCode", 0) == 1:
            return result
        text = f"{result.get('Message', '')} {result.get('ErrorStatus', '')}"
        if not ("in-game" in text or "DestinyItemActionForbidden" in text):
            return result
        return await self._bungie.insert_socket_plug(
            item_instance_id,
            mod_hash,
            socket_index,
            0,
            character_id,
            membership_type,
        )
