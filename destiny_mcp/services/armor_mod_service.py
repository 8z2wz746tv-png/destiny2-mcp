"""单件护甲换模组：先出方案（可确认），确认后再写。

为什么单独一个服务：`loadout_equipment_service.py` 与 `build_service.py` 都卡在体积上限，
而 `loadout_mod_sockets.py` 的既有约定就是"新方法加在这里"。这里用组合的方式复用
`ModSocketMixin` 的插槽读取与写入能力（它只依赖 `_manifest` / `_bungie` / `_resolver`），
不碰整套配装的执行流程。

设计口径（docs/plans/ARMOR_FORMAT_PLAN.md P4）：
- **方案 = 可核对的中文回显**：哪件护甲、哪个槽、从什么换成什么、能量怎么变；
- **校验**：实例必须属于目标角色（在仓库里就让调用方先搬）、模组必须在该槽 plug set 里、
  能量预算够（`已用 − 旧 + 新 ≤ 容量`）；
- 不猜：插件名对不上、槽位对不上、能量不够，都返回结构化错误而不是"试试看"。
"""

from __future__ import annotations

from typing import Any

from ..exceptions import InvalidArgumentError, ItemNotFoundError, TransferError
from ..manifest import ManifestManager
from ..player_resolver import PlayerResolver
from ..vocabulary import LEGACY_STAT_ALIASES
from . import profile_components
from .loadout_mod_sockets import ModSocketMixin

# 属性模组（+5/+10 六维）与部位功能模组在插槽里分属不同 plug 类别，名字可能撞车，
# 所以解析模组名时只在"护甲模组"这一类里找。
_MOD_ITEM_TYPE = 19

#: 调谐插件的 plug 类别（`core.gear_systems.armor_tiering.plugs.tuning.mods`）。
_TUNING_CATEGORY_HASH = 3481777685

#: "手雷调谐"这类说法里的属性词 → 六维键。调谐是零和的，光说加哪一项不够，
#: 还得说减哪一项，所以这种说法一律让调用方明确到具体那一个插件。
_TUNING_STAT_WORDS = {
    "武器": "weapons",
    "生命值": "health",
    "生命": "health",
    "职业": "class_stat",
    "手雷": "grenade",
    "超能": "super_stat",
    "近战": "melee",
}


def _stat_bonus_of(definition: dict[str, Any] | None) -> dict[str, int]:
    """插件定义的六维增量：只认六维、键名与 `to.stat_bonus` 同一套。

    以前这里给的是原始 stat hash，还把非六维的"费用"属性（3578062600）也算进来 ——
    调用方会把它读成"这个版本也有加成"。
    """
    from .armor_payload import STAT_HASH_TO_KEY

    out: dict[str, int] = {}
    for entry in (definition or {}).get("investmentStats") or []:
        key = STAT_HASH_TO_KEY.get(entry.get("statTypeHash", 0))
        value = entry.get("value", 0)
        if key and value:
            out[key] = out.get(key, 0) + int(value)
    return out

# 角色背包 + 已装备 + 实例 + 插槽：够判断"在谁身上、能不能装、能量够不够"
INVENTORY_SOCKETS = profile_components.INVENTORY_SOCKETS


def _mod_label(definition: dict[str, Any] | None, fallback: str) -> str:
    name = ((definition or {}).get("displayProperties") or {}).get("name", "")
    return name or fallback


class ArmorModService(ModSocketMixin):
    """`inventory_assistant(intent="equip_mod")` 的服务端一半。"""

    def __init__(
        self,
        bungie: Any,
        manifest: ManifestManager,
        resolver: PlayerResolver,
    ) -> None:
        self._bungie = bungie
        self._manifest = manifest
        self._resolver = resolver

    # ── 名字 → 插件 ───────────────────────────────────────────────────

    # 旧六维名 → Armor 3.0 的六维名（很多玩家还在用旧叫法：纪律/机动/韧性/恢复/力量/智慧）
    # 旧六维名 -> 规范名：单一出处见 vocabulary.LEGACY_STAT_ALIASES
    _LEGACY_STAT_ALIASES = LEGACY_STAT_ALIASES

    def _search_mods(self, query: str) -> list[int]:
        return [
            int(row["itemHash"])
            for row in self._manifest.search(query, limit=0)
            if row.get("itemType") == _MOD_ITEM_TYPE
        ]

    def _resolve_mod_candidates(self, mod_name: str) -> list[int]:
        """按名字找护甲模组（同名可能有多个 hash，只有其中一个在该槽的 plug set 里）。

        旧属性名（纪律/机动/…）自动换成新名再找一次：游戏把六维改名成
        武器/生命/职业/手雷/超能/近战，但玩家嘴里还是老叫法。
        """
        query = mod_name.strip()
        if not query:
            raise InvalidArgumentError("必须提供模组名称（mod_name）。")
        candidates = self._search_mods(query)
        if not candidates:
            translated = query
            for legacy, current in self._LEGACY_STAT_ALIASES.items():
                if legacy in translated:
                    translated = translated.replace(legacy, current)
                    break
            if translated != query:
                candidates = self._search_mods(translated)
        if not candidates:
            tuning_hash = self._resolve_tuning(query)
            if tuning_hash is not None:
                return [tuning_hash]
        if not candidates:
            raise InvalidArgumentError(
                f"没找到护甲模组 {mod_name!r}。可用 `build_assistant(intent=\"armor_mods\")` "
                "列出现有模组名，再逐字传进来（六维新名：武器/生命/职业/手雷/超能/近战）。"
            )
        return candidates

    def _resolve_tuning(self, query: str) -> int | None:
        """按名字认调谐插件；"手雷调谐"这种没说要减哪一项的说法直接让调用方挑。

        返回 None = 这不像调谐的名字（交给调用方报"没找到护甲模组"）。
        """
        from ..build.tuning import tuning_catalog  # 局部导入，避免把 build 层拖进工具面

        catalog = tuning_catalog(self._manifest)
        squeezed = query.replace(" ", "")
        for choice in catalog:
            if choice.kind != "empty" and choice.name.replace(" ", "") == squeezed:
                return choice.plug_hash
        stat = next(
            (value for word, value in _TUNING_STAT_WORDS.items() if word in query),
            None,
        )
        if stat is None or not any(
            word in query.lower() for word in ("调谐", "调整", "tuning")
        ):
            return None
        options = [
            choice
            for choice in catalog
            if choice.kind == "directional" and choice.increased == stat
        ]
        if len(options) == 1:  # pragma: no cover - 六维每项都有 5 个方向，走不到
            return options[0].plug_hash
        raise InvalidArgumentError(
            f"{query!r} 对应 {len(options)} 个调谐插件："
            + "、".join(choice.name for choice in options)
            + "。调谐是零和的（+5 一项一定 −5 另一项），得指定减哪一项 —— "
            '把 mod_name 写成其中一个的完整名字，例如 mod_name="+手雷 / -职业"。'
        )

    # ── 方案 ─────────────────────────────────────────────────────────

    async def plan(
        self,
        player_name: str,
        item_instance_id: str,
        mod_name: str,
        character: str,
    ) -> dict[str, Any]:
        """算出"换成这个模组"的方案，不写账号。"""
        if not item_instance_id.strip():
            raise InvalidArgumentError(
                "intent=equip_mod 需要 item_instance_id（护甲实例 ID）；"
                "先用 inventory_assistant(intent=\"item\") 或 intent=\"get\" 拿到它。"
            )
        if not character.strip():
            raise InvalidArgumentError(
                "intent=equip_mod 需要 character（hunter/warlock/titan），"
                "模组只能装在当前角色身上的护甲上。"
            )

        player = await self._resolver.resolve_player(player_name)
        membership_id = player["membership_id"]
        membership_type = player["membership_type"]
        character_id = await self._resolver.resolve_character_id(
            membership_id, membership_type, character
        )

        # 必须带上容器组件（200 角色背包 / 201 已装备），否则拿不到"这件在谁身上"，
        # 会把每件护甲都误判成"不在该角色身上"。305 用来读插槽，300 读能量与 T 级，
        # **310 用来读"这件允许哪些调谐"**（调谐写入的唯一判据，见 ADR-014 修订）。
        profile = await self._resolver.get_profile(
            membership_id, membership_type, [*INVENTORY_SOCKETS, 310]
        )
        components = profile.get("itemComponents") or {}
        instances = (components.get("instances") or {}).get("data") or {}
        sockets_data = (components.get("sockets") or {}).get("data") or {}
        reusable_plugs = (components.get("reusablePlugs") or {}).get("data") or {}
        equipment = ((profile.get("characterEquipment") or {}).get("data") or {}).get(
            character_id, {}
        ).get("items") or []
        inventory = ((profile.get("characterInventories") or {}).get("data") or {}).get(
            character_id, {}
        ).get("items") or []
        on_character = {
            str(item.get("itemInstanceId") or ""): item
            for item in [*equipment, *inventory]
        }
        item = on_character.get(item_instance_id)
        if item is None:
            raise InvalidArgumentError(
                f"{item_instance_id} 不在该角色身上。模组只能装在角色携带的护甲上："
                "先用 inventory_assistant(intent=\"move\") 把它移到这个角色，再重试。"
            )
        item_hash = int(item.get("itemHash", 0))
        definition = self._manifest.get_item_definition(item_hash) or {}
        if definition.get("itemType") != 2:
            raise InvalidArgumentError("intent=equip_mod 只处理护甲（itemType=2）。")

        sockets = (sockets_data.get(item_instance_id) or {}).get("sockets") or []
        pools = self.insertable_plugs(profile, character_id)
        candidates = self._resolve_mod_candidates(mod_name)
        fits: list[tuple[int, int]] = []
        for candidate in candidates:
            # _find_mod_socket 会按 plug set 校验：槽位不接受这个模组就返回 None
            found = await self._find_mod_socket(
                item_instance_id, item_hash, candidate, membership_id, membership_type,
                {item_instance_id: sockets},
            )
            if found is not None:
                fits.append((found, candidate))

        def _unlock_state(socket_index: int, plug_hash: int) -> bool | None:
            """这一位能不能插这颗；None = 上游没给这个槽的 plug set（不判断）。"""
            current = int((sockets[socket_index] or {}).get("plugHash", 0) or 0) \
                if socket_index < len(sockets) else 0
            return self.plug_is_insertable(
                pools, definition, socket_index, plug_hash, current
            )

        # 同名会有多个版本（实测「手雷模组」既有 +0/1 能量的占位版本，也有 +10/3 能量的
        # 真模组），随便挑一个会装上去一个没用的。
        # **解锁优先**：同名模组还有"已解锁/未解锁"两档（实测被上游 1676 拒掉的那几颗
        # 全都不在这一位角色的可插入清单里），挑错那个会让用户确认完才发现装不上。
        def _strength(pair: tuple[int, int]) -> tuple[int, int, int, int]:
            socket_index, plug_hash = pair
            bonus = _stat_bonus_of(self._manifest.get_item_definition(plug_hash))
            return (
                1 if _unlock_state(socket_index, plug_hash) else 0,
                1 if bonus else 0,
                sum(bonus.values()),
                self._plug_energy_cost(plug_hash) or 0,
            )

        socket_index, matched_hash = (0, 0)
        if fits:
            socket_index, matched_hash = max(fits, key=_strength)
        if not fits:
            raise InvalidArgumentError(
                f"{definition.get('displayProperties', {}).get('name', item_instance_id)} "
                f"没有能装 {mod_name!r} 的插槽。用 inventory_assistant(intent=\"item\") 看它有哪些槽，"
                "属性模组要装进 kind=\"general\" 的槽，部位模组装进部位槽。"
            )

        energy = (instances.get(item_instance_id) or {}).get("energy") or {}
        capacity = int(energy.get("energyCapacity", 0) or 0)
        used = int(energy.get("energyUsed", 0) or 0)
        current_hash = int((sockets[socket_index] or {}).get("plugHash", 0) or 0)
        current_cost = self._plug_energy_cost(current_hash) or 0
        new_cost = self._plug_energy_cost(matched_hash) or 0
        after = used - current_cost + new_cost
        if capacity and after > capacity:
            raise InvalidArgumentError(
                f"能量不够：{used}/{capacity} 已用，换上新模组需要 {after}/{capacity}。"
                "先卸掉或换掉别的模组腾出能量。"
            )

        alternatives = []
        if len(fits) > 1:
            for alt_socket, alt_hash in fits:
                if alt_hash == matched_hash:
                    continue
                bonus = _stat_bonus_of(self._manifest.get_item_definition(alt_hash))
                alternatives.append({
                    "hash": alt_hash,
                    "name": _mod_label(self._manifest.get_item_definition(alt_hash), mod_name),
                    "stat_bonus": bonus,
                    "energy_cost": self._plug_energy_cost(alt_hash) or 0,
                    "socket_index": alt_socket,
                    # 调谐一律 null：那份"这一位能不能插"的判定在调谐槽上不可信（见下面 is_tuning
                    # 那段），报出去会被读成"写不了"。与 `to.unlock_state` 同一口径。
                    "unlock_state": (
                        None
                        if self._plug_category_hash(alt_hash) == _TUNING_CATEGORY_HASH
                        else _unlock_state(alt_socket, alt_hash)
                    ),
                })

        is_tuning = self._plug_category_hash(matched_hash) == _TUNING_CATEGORY_HASH
        # 调谐：这颗在不在"这件护甲允许的清单"里（组件 310，单一出处见 models）。
        # 读不到清单 = 不拦（缺数据 ≠ 不许，交给上游说话）；读到了且不在里面 = 明确不写 ——
        # 省得让用户确认完再去撞 1675。
        from ..build.models import (  # 局部导入，同上
            tuning_is_allowed,
            tuning_options_from_reusable,
        )

        allowed = tuning_options_from_reusable(
            (reusable_plugs.get(item_instance_id) or {}),
            self._plug_category_hash,
            _TUNING_CATEGORY_HASH,
        )
        # `tuning_is_allowed` 两边过 `to_unsigned`：`matched_hash` 来自 manifest.search（有符号），
        # 310 清单来自 profile（无符号）—— 裸 `in` 会把清单里有的那颗判成"装不到这件上"（真机踩过）。
        tuning_allowed = (
            (not is_tuning) or (not allowed) or tuning_is_allowed(allowed, matched_hash)
        )
        raw_unlock_state = _unlock_state(socket_index, matched_hash)
        # **调谐不看 `unlock_state`** —— 那份"这一位能不能插"的判定在调谐槽上不可信：实测连
        # 正装着的那颗调谐都被判 false（`loadout_plug_lookup.plug_is_insertable` 为此打过补丁），
        # 而一颗被判 false 的调谐写入上游照样接受（2026-09-22 真机：至高碎片槽 11
        # `+武器 / -超能` → `ErrorCode=1`，回读插槽与六维都对）。拿它拦会把能装的调谐说成
        # "游戏里同样装不上"。判据只有组件 310 清单一条（ADR-014 修订）。
        # 也不把它报出去：调谐配上 `unlock_state: false` 会被读成"写不了"。
        unlock_state = None if is_tuning else raw_unlock_state
        conditions = self.plug_insertion_conditions(matched_hash)
        # 写不进去的情形都别走"确认后写入"：用户确认了、我们却只能失败。三条各自说清：
        #   ① 调谐不在**这件护甲**允许的清单里（组件 310）→ 上游回 1675，
        #      那句话读作"这颗装不到这件上"，**不是**"你没材料"；
        #   ② 非调谐模组不在这一位角色的可插入清单里 → 上游回 1676（插入条件没满足），游戏里同样装不上；
        #   ③ 其余情况可写（角色级清单"没数据"不算拦截 —— 缺数据 ≠ 不许，交给上游说话）。
        if is_tuning and not tuning_allowed:
            reason = (
                "这颗调谐**不在这件护甲允许的调谐里**（组件 310 的清单里没有它）："
                "上游会回 1675「这颗装不到这件上」，不是「你没材料」。"
                "换一件能装它的护甲，或者换成这件清单里已有的那几颗。"
            )
        elif (not is_tuning) and unlock_state is False:
            reason = (
                f"「{_mod_label(self._manifest.get_item_definition(matched_hash), mod_name)}」"
                "不在 Bungie 给这一位角色的可插入清单里 —— 实测这种写入会被回 1676"
                "（插入条件没满足），游戏里同样装不上。它的插入条件是："
                + ("；".join(conditions) if conditions else "上游没给条件文本")
                + "。条件里有「必须在赛季神器中选择」时，先在赛季神器里解锁它。"
            )
        else:
            reason = ""
        return {
            "player_name": player_name,
            "item_instance_id": item_instance_id,
            "kind": "tuning" if is_tuning else "mod",
            "writable": tuning_allowed if is_tuning else unlock_state is not False,
            "writable_reason": reason,
            "note": (
                "调谐不花能量、也不占模组槽；它是零和的："
                "to.stat_bonus 里同时有 +5 和 −5 两项。"
                if is_tuning
                else ""
            ),
            "alternatives": alternatives,
            "item_hash": item_hash,
            "item_name": definition.get("displayProperties", {}).get("name", ""),
            "slot": self._slot_key(definition),
            "gear_tier": (
                instances.get(item_instance_id) or {}
            ).get("gearTier"),
            "power": ((instances.get(item_instance_id) or {}).get("primaryStat") or {}).get("value"),
            "character": character,
            "character_id": character_id,
            "membership_id": membership_id,
            "membership_type": membership_type,
            "socket_index": socket_index,
            "from": {
                "hash": current_hash or None,
                "name": _mod_label(self._manifest.get_item_definition(current_hash), "")
                if current_hash
                else None,
                "energy_cost": current_cost,
            },
            "to": {
                "hash": matched_hash,
                "name": _mod_label(self._manifest.get_item_definition(matched_hash), mod_name),
                "energy_cost": new_cost,
                # 六维可读键，和 alternatives 同一套（工具层不用再自己算一遍）
                "stat_bonus": _stat_bonus_of(self._manifest.get_item_definition(matched_hash)),
                # None = 上游没给这个槽的 plug set（不判断，不是"没解锁"）
                "unlock_state": unlock_state,
                "conditions": conditions,
            },
            "energy": {"capacity": capacity, "used": used, "after": after},
        }

    @staticmethod
    def _slot_key(definition: dict[str, Any]) -> str:
        """护甲槽短键。

        以前这里自己抄了一张**有符号** bucket hash 表（helmet 写 -846692857），而 Manifest 的
        `inventory.bucketTypeHash` 存的是**无符号**值（3448274439）→ 头盔与臂铠的槽名一直是空串，
        确认请求里读成「光芒领主面具（，540，T5）」（真机日志可见）。改走 `armor_payload` 里
        已有的那张表（`_ARMOR_SLOT_HASHES`，键是无符号 hash），单一出处、不再抄第二份。
        """
        from .armor_payload import slot_key_from_bucket_hash  # 局部导入，避免循环依赖

        bucket_hash = (definition.get("inventory") or {}).get("bucketTypeHash", 0)
        return slot_key_from_bucket_hash(bucket_hash)

    # ── 执行 ─────────────────────────────────────────────────────────

    async def apply(self, plan: dict[str, Any]) -> dict[str, Any]:
        """按方案写入。失败时把 Bungie 的原文一并带出来（不吞错）。

        实机教训（0.1.8）：`_insert_armor_mod` 沿用"把 Bungie 的错误**当返回值**"的老约定
        （`{"ErrorCode": 1663, "Message": ...}`），这里以前不管内容直接 `success: True` ——
        一次真实失败被报成了成功（换调谐时 Bungie 回 "This action can only be done
        in-game."，账号一个字节没变，工具却说换好了）。现在必须核对 `ErrorCode == 1`，
        否则抛 TransferError 并把原文带出来。
        """
        try:
            result = await self._insert_armor_mod(
                plan["item_instance_id"],
                plan["to"]["hash"],
                plan["socket_index"],
                plan["character_id"],
                plan["membership_type"],
            )
        except TransferError:
            raise
        except Exception as exc:  # noqa: BLE001 - 转成可读的写入失败
            raise TransferError(
                f"装模组失败：{type(exc).__name__}: {str(exc)[:200]}"
            ) from exc

        code = (result or {}).get("ErrorCode", 0) if isinstance(result, dict) else 0
        if code != 1:
            message = str((result or {}).get("Message") or "Bungie 没有说明原因")
            status = str((result or {}).get("ErrorStatus") or "")
            if "DestinySocketAlreadyHasPlug" in f"{status} {message}":
                # 1679：这个槽**已经装着**这颗了。用户要的状态已经成立，不是失败 ——
                # 报成失败会让人以为要重试。以前"已装的那颗认不出来"是因为 `_find_mod_socket`
                # 拿有符号 hash 比无符号的实时 plugHash（ADR-013），现在两边都转无符号了，
                # 于是重复装同一颗会走到这里而不是错装到别的槽。
                return {
                    "success": True,
                    "already_installed": True,
                    "item_instance_id": plan["item_instance_id"],
                    "item_name": plan["item_name"],
                    "socket_index": plan["socket_index"],
                    "installed": plan["to"],
                    "replaced": plan["from"],
                    "energy": plan["energy"],
                    "bungie_response": result,
                }
            if "DestinyFailedPlugInsertionRules" in f"{status} {message}":
                # 1676：这颗模组的**插入条件**没满足。错误体里不说是哪条没过（message_data 空），
                # 唯一能拿到的中文说法是 Manifest 的 `plug.insertionRules[].failureMessage`。
                # 实测被它拒掉的那些，条件里都有「必须在赛季神器中选择」—— 游戏里同样装不上，
                # 所以不能报成"请在游戏里手动装"（以前那句就是这么错的）。
                conditions = self.plug_insertion_conditions(plan["to"]["hash"])
                raise TransferError(
                    "装模组失败：Bungie 回了 1676（这颗模组的插入条件没满足）。"
                    "Manifest 里它的条件是："
                    + ("；".join(conditions) if conditions else "上游没给条件文本")
                    + "。条件里有「必须在赛季神器中选择」时，先在赛季神器里解锁它再装。"
                )
            if "accessnotpermittedbyapplicationscope" in message.lower() or "scope" in message.lower():
                # 免费插槽接口**不需要** AWA（官方原文：does not require 'Advanced Write Action'
                # authorization and is available to 3rd-party apps）。所以撞到缺 scope，
                # 说明这颗 plug 走到了**付费**接口——那类要 AWA 三段流程，我们没实现（ADR-012）。
                message = (
                    "Bungie 拒绝了这次写入：这颗模组的写入走到了需要 `AdvancedWriteActions` 的"
                    "付费插槽接口，而本项目没有实现 AWA 的授权流程（要用户在某一步亲自批准）。"
                    "护甲模组本身走免费接口就能装，报到这里说明这颗不属于免费可逆的那类。原文："
                    + message[:200]
                )
            raise TransferError(f"装模组失败：{message[:300]}")
        return {
            "success": True,
            "item_instance_id": plan["item_instance_id"],
            "item_name": plan["item_name"],
            "socket_index": plan["socket_index"],
            "installed": plan["to"],
            "replaced": plan["from"],
            "energy": plan["energy"],
            "bungie_response": result,
        }


__all__ = ["ArmorModService", "ItemNotFoundError"]
