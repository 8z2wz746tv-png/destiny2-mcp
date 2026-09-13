"""单件护甲换模组：先出方案（可确认），确认后再写。

为什么单独一个服务：`loadout_equipment_service.py` 与 `build_service.py` 都卡在体积上限，
而 `loadout_mod_sockets.py` 的既有约定就是"新方法加在这里"。这里用组合的方式复用
`ModSocketMixin` 的插槽读取与写入能力（它只依赖 `_manifest` / `_bungie` / `_resolver`），
不碰整套配装的执行流程。

设计口径（ARMOR_FORMAT_PLAN.md P4）：
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
    _LEGACY_STAT_ALIASES = {
        "机动": "武器",
        "韧性": "生命",
        "恢复": "职业",
        "力量": "近战",
        "纪律": "手雷",
        "智慧": "超能",
    }

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
        # 会把每件护甲都误判成"不在该角色身上"。305 用来读插槽，300 读能量与 T 级。
        profile = await self._resolver.get_profile(
            membership_id, membership_type, INVENTORY_SOCKETS
        )
        components = profile.get("itemComponents") or {}
        instances = (components.get("instances") or {}).get("data") or {}
        sockets_data = (components.get("sockets") or {}).get("data") or {}
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
        # 同名会有多个版本（实测「手雷模组」既有 +0/1 能量的占位版本，也有 +10/3 能量的
        # 真模组），随便挑一个会装上去一个没用的。优先选真有属性加成的那个。
        def _strength(plug_hash: int) -> tuple[int, int, int]:
            bonus = _stat_bonus_of(self._manifest.get_item_definition(plug_hash))
            return (1 if bonus else 0, sum(bonus.values()), self._plug_energy_cost(plug_hash) or 0)

        socket_index, matched_hash = (0, 0)
        if fits:
            socket_index, matched_hash = max(fits, key=lambda pair: _strength(pair[1]))
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
                })

        is_tuning = self._plug_category_hash(matched_hash) == _TUNING_CATEGORY_HASH
        return {
            "player_name": player_name,
            "item_instance_id": item_instance_id,
            "kind": "tuning" if is_tuning else "mod",
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
            },
            "energy": {"capacity": capacity, "used": used, "after": after},
        }

    @staticmethod
    def _slot_key(definition: dict[str, Any]) -> str:
        from .armor_payload import slot_key_from_bucket  # 局部导入，避免循环依赖

        bucket_hash = (definition.get("inventory") or {}).get("bucketTypeHash", 0)
        mapping = {
            20886954: "Leg Armor",
            14239492: "Chest Armor",
            -846692857: "Helmet",
            -743048708: "Gauntlets",
            1585787867: "Class Armor",
        }
        return slot_key_from_bucket(mapping.get(bucket_hash, ""))

    # ── 执行 ─────────────────────────────────────────────────────────

    async def apply(self, plan: dict[str, Any]) -> dict[str, Any]:
        """按方案写入。失败时把 Bungie 的原文一并带出来（不吞错）。"""
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
