"""子职业插槽：读取当前配置、查找兼容插槽并写入。

以 mixin 挂在 LoadoutEquipmentService 上，通过 self 使用 manifest / bungie / resolver。
"""

from __future__ import annotations

import aiobungie

from ..models import Loadout, LoadoutSubclassConfig, MoveItemStep
from ..utils.hash_utils import to_unsigned
from . import profile_components, write_readback
from .loadout_plug_lookup import (
    EMPTY_FRAGMENT_PLUG,
    PlugLookupMixin,
    socket_is_assignable,
    socket_is_enabled,
)
from .loadout_mod_sockets import plug_already_installed
from .subclass_service import identify_socket_type


class SubclassSocketMixin(PlugLookupMixin):
    """子职业一侧的读取与应用。"""

    _PLUG_CAT_SUPER = 3854839014
    _PLUG_CAT_ABILITIES = 3847589792  # grenade/melee/class_ability/movement
    _PLUG_CAT_ASPECTS = 3472571033
    _PLUG_CAT_FRAGMENTS = 2736821379

    def read_subclass_config(
        self, inst_id: str, item_hash: int, sockets_data: dict
    ) -> LoadoutSubclassConfig | None:
        """Read subclass configuration from sockets.

        **哪个槽能写，只认现场数据**：这个方法是"槽号"唯一的来处，它只把
        `isEnabled: true` 的槽收进 `plug_sockets`（判据见 `socket_is_assignable`）。

        为什么非要在这里就滤掉：真机事故（`equip_build` 的 `subclass` 步骤报
        `DestinySocketActionNotAllowed` / `The requested socket is disabled.`）的根因
        不是碎片、也不是写错了槽号，而是**这个函数把禁用槽也当成了可排的槽**。
        棱镜术士有 6 个碎片槽（下标 9–14）其中 socket 14 是禁用的、里面躺着占位
        `空碎片插槽`，于是 `plug_sockets` 里出现 6 个碎片槽 → 下游 `replace_fragment_config`
        按"第 N 颗进第 N 个碎片槽"把第 6 颗排进 14 → 上游 500。同一颗碎片写进 socket 12
        （`isEnabled: true`）就成功，说明碎片本身没问题。
        这是第 n 次"位置假设 vs 现场事实"：**槽的可用性必须来自 `isEnabled`，不许按
        "第几个碎片槽"猜，也不许照抄别的角色/别的子职业的槽位表。**

        另一个变化是有意为之的：槽里躺着占位 `空碎片插槽` 时**不算"装着碎片"**
        （它不是碎片，只表示那个位置没填）。所以 `fragment_hashes` / `plug_sockets`
        数出来的是"真正装着的碎片"，空槽由 `fragment_sockets` 记着、由报错文案点名。
        """
        if not inst_id:
            return None

        item_sockets = sockets_data.get(inst_id, {}).get("sockets", [])
        if not item_sockets:
            return None

        aspect_hashes: list[int] = []
        fragment_hashes: list[int] = []
        super_hash = 0
        grenade_hash = 0
        melee_hash = 0
        class_ability_hash = 0
        movement_hash = 0
        plug_sockets: dict[int, int] = {}
        socket_states: dict[int, bool] = {}
        fragment_sockets: list[int] = []

        for socket_index, socket in enumerate(item_sockets):
            plug_hash = socket.get("plugHash", 0)
            if not plug_hash:
                continue

            plug_category = self._plug_category_hash(plug_hash)
            category_identifier = (
                self._manifest.get_plug_category_identifier(plug_hash) or ""
            )
            socket_type = (
                identify_socket_type(category_identifier)
                if category_identifier
                else ""
            )
            if not socket_type:
                if plug_category == self._PLUG_CAT_SUPER:
                    socket_type = "super"
                elif plug_category == self._PLUG_CAT_ASPECTS:
                    socket_type = "aspect"
                elif plug_category == self._PLUG_CAT_FRAGMENTS:
                    socket_type = "fragment"
            # 占位 `空碎片插槽` 自己不是一个 fragment 分类的 plug，但**它所在的槽是碎片槽**
            # （真机：棱镜术士 socket 14）。不认这一点的话禁用槽会从"碎片槽"名单里消失，
            # 也就没机会在报错里点名它 —— 而"这个槽不用排、为什么"必须说得出来。
            if not socket_type and to_unsigned(plug_hash) == EMPTY_FRAGMENT_PLUG:
                socket_type = "fragment"
            if not socket_type:
                continue

            enabled = socket_is_enabled(socket)
            if enabled is not None:
                socket_states[socket_index] = enabled
            if socket_type == "fragment":
                # 读到的碎片槽都记下来（含禁用的、含没给状态的）：报错要能点名
                # "哪个槽不能用、为什么"。**槽号只能用这里记的**，不许按位置推。
                fragment_sockets.append(socket_index)
            # 禁用槽不进 plug_sockets：它不是一个可排的槽位，写进去必然被上游拒
            # （见 `socket_is_assignable`）。状态未知（`enabled is None`）同样不排 ——
            # 不赌它开着。
            if not socket_is_assignable(socket):
                continue

            if socket_type == "super":
                super_hash = plug_hash
            elif socket_type == "grenade":
                grenade_hash = plug_hash
            elif socket_type == "melee":
                melee_hash = plug_hash
            elif socket_type == "class_ability":
                class_ability_hash = plug_hash
            elif socket_type == "movement":
                movement_hash = plug_hash
            elif socket_type == "aspect":
                aspect_hashes.append(plug_hash)
            elif socket_type == "fragment":
                fragment_hashes.append(plug_hash)
            else:
                continue
            plug_sockets[socket_index] = plug_hash

        if not any([super_hash, grenade_hash, melee_hash, class_ability_hash,
                    movement_hash, aspect_hashes, fragment_hashes]):
            return None

        return LoadoutSubclassConfig(
            subclass_item_hash=item_hash,
            subclass_instance_id=inst_id,
            super_hash=super_hash,
            grenade_hash=grenade_hash,
            melee_hash=melee_hash,
            class_ability_hash=class_ability_hash,
            movement_hash=movement_hash,
            aspect_hashes=aspect_hashes,
            fragment_hashes=fragment_hashes,
            plug_sockets=plug_sockets,
            socket_states=socket_states,
            fragment_sockets=fragment_sockets,
        )

    # ── Equipment application ────────────────────────────────────────

    async def _apply_subclass_config(
        self,
        player_name: str,
        loadout: Loadout,
        char_id: str,
        membership_type: int,
        steps: list[MoveItemStep],
    ) -> tuple[bool, str]:
        """应用子职业配置：`(成不成, 失败原因)`。

        为什么要把原因**返出去**而不是只写进 `steps`：真机 2026-10-03 第 3 轮一颗碎片写失败，
        回执里 `subclass` 步骤只写"plug 124726504 已应用"、**不带上游原文**，于是"为什么没写进去"
        只能靠猜（同一份回执里 `mod` 步骤是带原文的，两条路口径不一致）。外层要拿它写结论句。
        """
        subclass = loadout.subclass
        if not subclass:
            return True, ""

        all_ok = True
        reason = ""

        p = await self._resolver.resolve_player(player_name)
        profile = await self._resolver.get_profile(
            p["membership_id"], membership_type, profile_components.SUBCLASS
        )
        equip_data = (
            profile.get("characterEquipment", {})
            .get("data", {})
            .get(char_id, {})
            .get("items", [])
        )
        sockets_map = (
            profile.get("itemComponents", {})
            .get("sockets", {})
            .get("data", {})
        )
        inv_data = (
            profile.get("characterInventories", {})
            .get("data", {})
            .get(char_id, {})
            .get("items", [])
        )

        subclass_inst_id = None
        subclass_item_hash = 0
        for raw_item in equip_data:
            item_hash = raw_item.get("itemHash", 0)
            item_info = self._manifest.get_item_info(item_hash) or {}
            if item_info.get("itemType") == 16:
                subclass_inst_id = str(raw_item.get("itemInstanceId", ""))
                subclass_item_hash = item_hash
                break

        if not subclass_inst_id:
            steps.append(MoveItemStep(
                action="error", detail="找不到已装备的子职业", success=False,
            ))
            return False, "找不到已装备的子职业"

        if (
            subclass.subclass_item_hash
            and subclass.subclass_item_hash != subclass_item_hash
        ) or (
            subclass.subclass_instance_id
            and subclass.subclass_instance_id != subclass_inst_id
        ):
            # 子职业不一致：**先换上再配**（用户拍板：equip_loadout 顺手换）。
            # 以前这里直接判失败，于是"配装带着另一个子职业"永远装不上，只能靠人手动换。
            target = next(
                (
                    item
                    for item in inv_data
                    if (
                        subclass.subclass_instance_id
                        and str(item.get("itemInstanceId", ""))
                        == subclass.subclass_instance_id
                    )
                    or (
                        subclass.subclass_item_hash
                        and item.get("itemHash") == subclass.subclass_item_hash
                    )
                ),
                None,
            )
            if target is None:
                steps.append(MoveItemStep(
                    action="subclass",
                    detail=(
                        "要装的子职业不在这个角色的背包里（子职业不能从仓库装）："
                        f"hash={subclass.subclass_item_hash} "
                        f"实例={subclass.subclass_instance_id or '任意'}"
                    ),
                    success=False,
                ))
                return False, "要装的子职业不在这个角色的背包里（子职业不能从仓库装）"
            target_inst = str(target.get("itemInstanceId", ""))
            result = await self._bungie.equip_item(
                item_instance_id=target_inst,
                character_id=char_id,
                membership_type=membership_type,
            )
            if result.get("ErrorCode", 0) != 1:
                upstream = str(result.get("Message") or "").strip() or "上游没给原因"
                steps.append(MoveItemStep(
                    action="subclass",
                    detail=f"换上保存的子职业失败：{upstream}",
                    success=False,
                ))
                return False, f"换上保存的子职业失败：{upstream}"
            steps.append(MoveItemStep(
                action="subclass",
                detail=f"已换上保存的子职业（实例 {target_inst}）",
                success=True,
            ))
            # 插槽数据必须按**新物品**重读：上面那份 profile 里没有它的 sockets。
            # 刚换完可能还读到旧值（上游 profile 同步窗口，真机实采约 3 秒），所以重试到
            # 新实例的插槽出现为止——否则下面找兼容插槽会以"找不到"收场。
            def _has_sockets(candidate: dict) -> bool:
                return bool(
                    candidate.get("itemComponents", {})
                    .get("sockets", {})
                    .get("data", {})
                    .get(target_inst, {})
                    .get("sockets")
                )

            profile = await write_readback.read_until(
                lambda: self._resolver.get_profile(
                    p["membership_id"], membership_type, profile_components.SUBCLASS
                ),
                _has_sockets,
            )
            sockets_map = (
                profile.get("itemComponents", {}).get("sockets", {}).get("data", {})
            )
            subclass_inst_id = target_inst
            subclass_item_hash = int(target.get("itemHash", 0))

        all_plugs: list[tuple[str, int, int | None]] = []
        if subclass.plug_sockets:
            all_plugs.extend(
                ("plug", plug_hash, socket_index)
                for socket_index, plug_hash in sorted(subclass.plug_sockets.items())
            )
        else:
            if subclass.super_hash:
                all_plugs.append(("super", subclass.super_hash, None))
            if subclass.grenade_hash:
                all_plugs.append(("grenade", subclass.grenade_hash, None))
            if subclass.melee_hash:
                all_plugs.append(("melee", subclass.melee_hash, None))
            if subclass.class_ability_hash:
                all_plugs.append(("class_ability", subclass.class_ability_hash, None))
            if subclass.movement_hash:
                all_plugs.append(("movement", subclass.movement_hash, None))
            all_plugs.extend(("aspect", h, None) for h in subclass.aspect_hashes)
            all_plugs.extend(("fragment", h, None) for h in subclass.fragment_hashes)

        assigned_sockets: set[int] = set()
        for plug_type, plug_hash, exact_socket_index in all_plugs:
            try:
                socket_idx = (
                    exact_socket_index
                    if exact_socket_index is not None
                    else await self._find_subclass_socket(
                        subclass_inst_id,
                        subclass_item_hash,
                        plug_hash,
                        plug_type,
                        sockets_map,
                        assigned_sockets,
                    )
                )
                if socket_idx is not None and socket_idx >= len(
                    sockets_map.get(subclass_inst_id, {}).get("sockets", [])
                ):
                    socket_idx = None
                if socket_idx is None:
                    steps.append(MoveItemStep(
                        action="subclass",
                        detail=f"找不到 {plug_type} {plug_hash} 的兼容插槽",
                        success=False,
                    ))
                    all_ok = False
                    continue
                assigned_sockets.add(socket_idx)
                sockets = sockets_map.get(subclass_inst_id, {}).get("sockets", [])
                # 写之前**再按现场核一次这个槽开不开**（判据与读取那条路同一个
                # `socket_is_assignable`）。读取时已经滤过一遍禁用槽，这里防的是
                # "签发方案 → 用户确认 → 执行"之间槽被关掉，或者方案里的槽号不是
                # 这个角色的现场（旧方案、抄来的槽位表）。不核就是真机那条 500
                # `DestinySocketActionNotAllowed` / `The requested socket is disabled.`。
                # 状态读不到（`isEnabled` 缺字段）按不可写处理：这里**不许猜**。
                live_socket = sockets[socket_idx]
                if not socket_is_assignable(live_socket):
                    state = socket_is_enabled(live_socket)
                    why = "禁用" if state is False else "状态未知（账号没给 isEnabled）"
                    detail = (
                        f"{plug_type} '{self.mod_label(plug_hash)}' 未排："
                        f"槽 {socket_idx} {why}，不是可写的槽"
                    )
                    steps.append(MoveItemStep(
                        action="subclass", detail=detail, success=False,
                    ))
                    reason = reason or detail
                    all_ok = False
                    continue
                if to_unsigned(int(sockets[socket_idx].get("plugHash", 0) or 0)) == to_unsigned(plug_hash):
                    # 这个槽已经装着它：**不调用上游**。再写一次回的是 HTTP 500 + 1679
                    # （客户端还会退避重试四次），真机实测每颗白花数秒。
                    steps.append(MoveItemStep(
                        action="subclass",
                        detail=f"{plug_type} '{self.mod_label(plug_hash)}' 已经装着，未改动",
                        success=True,
                    ))
                    continue
                result = await self._bungie.insert_socket_plug_free(
                    subclass_inst_id, plug_hash, socket_idx,
                    0, char_id, membership_type,
                )
                # 1679：这个槽已经装着它了。**配装带着"当前这套子职业"时，每一颗都会撞上它**
                # （`execution_subclass` 就是按当前配置读出来的）—— 把它当失败的话，
                # 每次 equip_build 都会失败并回退，真机实测一次白烧 5 分钟以上。
                already = plug_already_installed(result)
                ok = result.get("ErrorCode", 0) == 1 or already
                # 失败要带上游原文（与模组那条路同一口径）：以前这里只写"plug <hash> 已应用"，
                # 一颗写失败时调用方拿不到任何原因 —— 真机 2026-10-03 第 3 轮就是这么丢的。
                upstream = str(result.get("Message") or "").strip()
                detail = (
                    f"{plug_type} '{self.mod_label(plug_hash)}' 已经装着，未改动"
                    if already
                    else f"{plug_type} '{self.mod_label(plug_hash)}' 已应用"
                )
                if not ok:
                    detail += f" 失败：{upstream or '上游没给原因'}"
                    reason = reason or f"{plug_type} '{self.mod_label(plug_hash)}'：{upstream or '上游没给原因'}"
                steps.append(MoveItemStep(
                    action="subclass", detail=detail, success=ok,
                ))
                if not ok:
                    all_ok = False
            except aiobungie.HTTPError as e:
                steps.append(MoveItemStep(
                    action="error",
                    detail=f"{plug_type} {plug_hash} 应用失败: {e}",
                    success=False,
                ))
                reason = reason or f"{plug_type} '{self.mod_label(plug_hash)}'：{e}"
                all_ok = False

        return all_ok, ("" if all_ok else reason or "子职业配置没写完（原因见 steps）")

    async def _find_subclass_socket(
        self,
        subclass_inst_id: str,
        subclass_item_hash: int,
        plug_hash: int,
        plug_type: str,
        sockets_map: dict,
        assigned_sockets: set[int] | None = None,
    ) -> int | None:
        """Find a compatible subclass socket from plug sets, then categories.

        两个来源都套同一道闸：**禁用/状态未知的槽一律不算候选**（`socket_is_assignable`）。
        少了这道闸，下面按类型兜底那段会顺着下标把禁用槽当成兼容槽还给调用方 ——
        真机 socket 14 是禁用的碎片槽，"按类型兜底"正好会挑中它。
        """
        sockets = sockets_map.get(subclass_inst_id, {}).get("sockets", [])
        if assigned_sockets is None:
            assigned_sockets = set()

        def _usable(index: int) -> bool:
            return (
                index < len(sockets)
                and index not in assigned_sockets
                and socket_is_assignable(sockets[index])
            )

        subclass_definition = self._manifest.get_item_definition(
            subclass_item_hash
        ) or {}
        socket_entries = (subclass_definition.get("sockets") or {}).get(
            "socketEntries", []
        )
        for index, entry in enumerate(socket_entries):
            if not _usable(index):
                continue
            plug_set_hashes = {
                entry.get("reusablePlugSetHash", 0),
                entry.get("randomizedPlugSetHash", 0),
            }
            if entry.get("singleInitialItemHash", 0) == plug_hash:
                return index
            for plug_set_hash in plug_set_hashes:
                if not plug_set_hash:
                    continue
                plug_set = self._manifest.get_definition(
                    "DestinyPlugSetDefinition", plug_set_hash
                ) or {}
                if any(
                    item.get("plugItemHash", 0) == plug_hash
                    for item in plug_set.get("reusablePlugItems", [])
                ):
                    return index

        desired_category = self._plug_category_hash(plug_hash)
        category_identifier = (
            self._manifest.get_plug_category_identifier(plug_hash) or ""
        )
        desired_type = (
            identify_socket_type(category_identifier)
            if category_identifier
            else plug_type
        )
        for index, socket in enumerate(sockets):
            if not _usable(index):
                continue
            current_hash = socket.get("plugHash", 0)
            if current_hash == plug_hash:
                return index
            current_identifier = (
                self._manifest.get_plug_category_identifier(current_hash) or ""
            )
            if current_identifier:
                current_type = identify_socket_type(current_identifier)
                if current_type == desired_type:
                    return index
                continue
            if (
                desired_category
                and self._plug_category_hash(current_hash) == desired_category
            ):
                return index

        return None
