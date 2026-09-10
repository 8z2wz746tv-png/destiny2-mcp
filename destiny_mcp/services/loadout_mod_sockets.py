"""护甲模组插槽：读取已装模组、规划腾挪、查找并写入插槽。

以 mixin 挂在 LoadoutEquipmentService 上，通过 self 使用 manifest / bungie / resolver，
所以调用点不用改。新方法加在这里，不要再往 loadout_equipment_service.py 堆。
"""

from __future__ import annotations

from ..exceptions import TransferError
from ..manifest import ManifestManager
from ..models import LoadoutItem
from .loadout_plug_lookup import PlugLookupMixin


class ModSocketMixin(PlugLookupMixin):
    """护甲模组一侧的读取与写入规划。"""

    _MOD_CATEGORY_HASHES = set(ManifestManager._ARMOR_MOD_CATEGORIES) | {
        595201146,   # Legacy EnhancementsArtifice
        4065769746,  # Legacy EnhancementsV2
        3315022374,  # Legacy EnhancementsV2ArmorOnly
    }

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

    async def _prepare_mod_operations(
        self,
        item: LoadoutItem,
        membership_id: str,
        membership_type: int,
        sockets_cache: dict[str, list[dict]],
        instances_data: dict,
    ) -> list[tuple[str, int, int]]:
        """Order minimal energy-clearing writes before requested mod writes."""
        sockets = sockets_cache.get(item.item_instance_id, [])
        requested = (
            [
                (mod_hash, socket_index)
                for socket_index, mod_hash in sorted(item.mod_sockets.items())
            ]
            if item.mod_sockets
            else [(mod_hash, None) for mod_hash in item.mods]
        )

        assigned_sockets: set[int] = set()
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
        deficit = max(0, projected_used - capacity)
        clear_operations: list[tuple[str, int, int]] = []
        if deficit:
            item_definition = self._manifest.get_item_definition(item.item_hash)
            socket_entries = (
                (item_definition.get("sockets") or {}).get("socketEntries", [])
                if isinstance(item_definition, dict)
                else []
            )
            candidates: list[tuple[int, int, int]] = []
            for socket_index, socket in enumerate(sockets):
                if (
                    socket_index in assigned_sockets
                    or socket_index >= len(socket_entries)
                ):
                    continue
                current_hash = socket.get("plugHash", 0)
                default_hash = socket_entries[socket_index].get(
                    "singleInitialItemHash", 0
                )
                if not current_hash or not default_hash or current_hash == default_hash:
                    continue
                current_category = self._plug_category_hash(current_hash)
                default_category = self._plug_category_hash(default_hash)
                if current_category == self._PLUG_CAT_TUNING:
                    continue
                if (
                    current_category not in self._MOD_CATEGORY_HASHES
                    and default_category not in self._MOD_CATEGORY_HASHES
                ):
                    continue

                current_cost = self._plug_energy_cost(current_hash)
                default_cost = self._plug_energy_cost(default_hash)
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

            remaining = sorted(candidates)
            while deficit > 0:
                sufficient = [
                    candidate for candidate in remaining if candidate[0] >= deficit
                ]
                chosen = min(sufficient) if sufficient else remaining[0]
                remaining.remove(chosen)
                freed, socket_index, default_hash = chosen
                clear_operations.append(("clear", default_hash, socket_index))
                deficit -= freed

        return [
            *clear_operations,
            *[
                ("mod", mod_hash, socket_index)
                for _, mod_hash, socket_index in sorted(target_operations)
            ],
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
        if sockets_cache is not None and item_instance_id in sockets_cache:
            sockets_data = sockets_cache[item_instance_id]
        else:
            profile = await self._resolver.get_profile(
                membership_id, membership_type, [305]
            )
            sockets_data = (
                profile.get("itemComponents", {})
                .get("sockets", {})
                .get("data", {})
                .get(item_instance_id, {})
                .get("sockets", [])
            )
            if sockets_cache is not None:
                sockets_cache[item_instance_id] = sockets_data

        mod_category = self._plug_category_hash(mod_hash)
        if mod_category not in self._MOD_CATEGORY_HASHES:
            return None

        item_definition = self._manifest.get_item_definition(item_hash)
        if isinstance(item_definition, dict):
            socket_entries = (item_definition.get("sockets") or {}).get(
                "socketEntries", []
            )
            for index, entry in enumerate(socket_entries):
                if index >= len(sockets_data) or index in excluded_socket_indices:
                    continue
                if sockets_data[index].get("plugHash", 0) == mod_hash:
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
                        item.get("plugItemHash", 0) == mod_hash
                        for item in plug_set.get("reusablePlugItems", [])
                    ):
                        return index

        # Pass 1: find socket with matching category that doesn't already have this exact mod
        for i, socket in enumerate(sockets_data):
            if i in excluded_socket_indices:
                continue
            plug_hash = socket.get("plugHash", 0)
            if plug_hash == mod_hash:
                continue
            if plug_hash:
                current_category = self._plug_category_hash(plug_hash)
                if current_category == mod_category:
                    return i

        # Pass 2: already has this mod
        for i, socket in enumerate(sockets_data):
            if i in excluded_socket_indices:
                continue
            plug_hash = socket.get("plugHash", 0)
            if plug_hash == mod_hash:
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
        """Use Bungie's paid or free socket endpoint from manifest energy cost."""
        energy_cost = self._plug_energy_cost(mod_hash) or 0
        insert = (
            self._bungie.insert_socket_plug
            if energy_cost > 0
            else self._bungie.insert_socket_plug_free
        )
        return await insert(
            item_instance_id,
            mod_hash,
            socket_index,
            0,
            character_id,
            membership_type,
        )
