"""Artifact service — business logic for seasonal artifact operations.

Extracted from artifact_tools.py per Rule 1: tools should not contain
business logic.
"""

from __future__ import annotations

import unicodedata

from ..bungie_client import BungieClient
from ..exceptions import DefinitionNotFoundError, InvalidArgumentError
from ..logging_config import get_logger
from . import profile_components, write_readback
from ..manifest import ManifestManager, resolve_character_name
from ..utils.hash_utils import to_unsigned
from ..player_resolver import PlayerResolver
from .account_action_lock import account_action_lock, serialized_account_action

logger = get_logger(__name__)


# Artifact bucket hash
_ARTIFACT_BUCKET_HASH = 1506418338


class ArtifactService:
    """Business logic for seasonal artifact operations."""

    def __init__(self, bungie: BungieClient, manifest: ManifestManager, resolver: PlayerResolver) -> None:
        self._bungie = bungie
        self._manifest = manifest
        self._resolver = resolver
        self._account_action_lock = account_action_lock(bungie)

    def get_seasonal_artifact(self, artifact_name: str = "") -> dict:
        """Get seasonal artifact list or a specific artifact preview."""
        if not artifact_name:
            artifacts = self._manifest.get_all_artifacts()
            return {
                "artifacts": artifacts,
                # 「我现在用哪个神器」不该要求调用方先知道神器名字：不带名字也要给当前神器
                # （以前只有按名字查那条分支才附 current_artifact，语料实跑抓到的）。
                "current_artifact": self._manifest.get_current_artifact(),
            }

        artifact = self._manifest.get_artifact_by_name(artifact_name)
        if not artifact:
            raise DefinitionNotFoundError(artifact_name, "没有名称包含它的赛季神器。")

        current = self._manifest.get_current_artifact()
        return {
            "artifact": artifact,
            "current_artifact": current,
        }

    def get_artifact_mod_info(self, mod_hash: int) -> dict:
        """Get details for a seasonal artifact mod by hash."""
        mod_info = self._manifest.get_artifact_mod_details(mod_hash)
        if not mod_info:
            raise DefinitionNotFoundError(f"hash={mod_hash}", "没有这个神器模组。")
        return {
            "artifact_mod": mod_info,
        }

    # ── 角色身上的神器（换神器要用）────────────────────────────────────
    #
    # 读的是**账号实例**，不是 Manifest 目录。实采证明两者不是一回事：
    # 目录里的「当前神器」（`DestinyArtifactDefinition` 全表只有 1 行，报的是 s27 好奇之器）
    # 与角色身上那件可以完全不同（同一时刻三角色分别装着 s26/s21/s25）；而且同名不同 hash
    # （好奇之器：目录 -1600062152、玩家实例 23349941）。所以"我现在用哪个、能不能换"
    # 只能读实例，目录只配用来查模组池。

    def _character_artifacts(
        self, profile: dict, character_id: str
    ) -> list[tuple[str, int]]:
        """这个角色身上（装备位 + 背包）的神器：(实例 id, 定义 hash)，按桶顺序去重。

        神器**不可转移**（实采 `transferStatus`：背包里=2、装备位=3，与子职业物品同类），
        所以"能换的"只能是同一个角色背包里那几件；仓库与邮政长里一件都没有（实采 0 件）。
        """
        found: list[tuple[str, int]] = []
        seen: set[str] = set()
        for bucket in ("characterEquipment", "characterInventories"):
            for item in (
                profile.get(bucket, {})
                .get("data", {})
                .get(character_id, {})
                .get("items", [])
            ):
                if item.get("bucketHash") != _ARTIFACT_BUCKET_HASH:
                    continue
                instance_id = str(item.get("itemInstanceId", ""))
                if not instance_id or instance_id in seen:
                    continue
                seen.add(instance_id)
                found.append((instance_id, to_unsigned(int(item.get("itemHash", 0) or 0))))
        return found

    def _artifact_instances(self, profile: dict, character_id: str) -> list[dict]:
        """同上，但带上给人看的东西（官方名 + 是否正装着）。"""
        equipped_id = self._equipped_artifact_instance(profile, character_id)
        return [
            {
                "name": self._manifest.get_item_name(item_hash) or f"#{item_hash}",
                "hash": item_hash,
                "instance_id": instance_id,
                "is_equipped": instance_id == equipped_id,
            }
            for instance_id, item_hash in self._character_artifacts(profile, character_id)
        ]

    def _equipped_artifact_instance(self, profile: dict, character_id: str) -> str:
        """这个角色正装着哪件神器（实例 id）。

        「装没装」只有一个权威字段：组件 300 的 `itemComponents.instances.data[实例].isEquipped`。
        实采踩过两件事，都写在这里免得再踩：

        1. 205 的条目里**没有** `isEquipped`（原始字段只有
           `itemHash/itemInstanceId/location/bucketHash/transferStatus/lockable/state`），
           照 item 上的字段读永远读成"没装备"；
        2. `isEquipped` 对**所有**装备都为真（武器、护甲、子职业全是 true），
           所以必须先在"神器桶里的实例"里挑，不能只按"属于这个角色"过滤 ——
           否则挑中的是第一件装备的武器（真机调试就是这么错的）。

        组件缺失（调用方没要 300）时退回"装备位里有哪件神器"，免得把正装着的那件当候选。
        """
        instances = (
            profile.get("itemComponents", {})
            .get("instances", {})
            .get("data", {})
        )
        if instances:
            for instance_id, _hash in self._character_artifacts(profile, character_id):
                if (instances.get(instance_id) or {}).get("isEquipped"):
                    return instance_id
            return ""
        return self._artifact_in_equipment_bucket(profile, character_id)

    @staticmethod
    def _artifact_in_equipment_bucket(profile: dict, character_id: str) -> str:
        for item in (
            profile.get("characterEquipment", {})
            .get("data", {})
            .get(character_id, {})
            .get("items", [])
        ):
            if item.get("bucketHash") == _ARTIFACT_BUCKET_HASH:
                return str(item.get("itemInstanceId", ""))
        return ""

    async def _identity(self, player_name: str, character: str) -> tuple[str, int, str]:
        p = await self._resolver.resolve_player(player_name)
        mid, mtype = p["membership_id"], p["membership_type"]
        char_id = await self._resolver.resolve_character_id(mid, mtype, character)
        return mid, mtype, char_id

    async def artifact_state(self, player_name: str, character: str) -> dict:
        """这个角色神器的真实状态：正装备的那件 + 背包里能换的那几件。"""
        mid, mtype, char_id = await self._identity(player_name, character)
        profile = await self._resolver.get_profile(mid, mtype, profile_components.ARTIFACT)
        instances = self._artifact_instances(profile, char_id)
        return {
            "equipped": next((i for i in instances if i["is_equipped"]), None),
            "available": [i for i in instances if not i["is_equipped"]],
        }

    @serialized_account_action
    async def switch_artifact(
        self,
        player_name: str,
        character: str,
        artifact_name: str,
    ) -> dict:
        """换神器：按官方名在这个角色的实例里找 → equip → 回读核对。

        名字**精确匹配**（NFKC + 大小写归一）实例定义里的 `displayProperties.name`，
        不模糊匹配：猜错神器比报错更坑。找不到就说清这个角色现在有哪几件。
        """
        if not artifact_name.strip():
            raise InvalidArgumentError("换神器要给出神器名字，例如 artifact_name=\"好奇之器\"。")

        mid, mtype, char_id = await self._identity(player_name, character)
        profile = await self._resolver.get_profile(mid, mtype, profile_components.ARTIFACT)
        instances = self._artifact_instances(profile, char_id)
        equipped = next((i for i in instances if i["is_equipped"]), None)

        def _norm(value: str) -> str:
            return unicodedata.normalize("NFKC", value).strip().casefold()

        wanted = _norm(artifact_name)
        target = next((i for i in instances if _norm(i["name"]) == wanted), None)
        if target is None:
            holding = "、".join(i["name"] for i in instances) or "一件都没有"
            raise InvalidArgumentError(
                f"「{artifact_name}」不在这个角色身上：神器不能跨角色/仓库转移"
                f"（实采 transferStatus=2），只能换他背包里有的。他现在有：{holding}。"
            )

        from_payload = {
            "name": (equipped or {}).get("name", ""),
            "hash": (equipped or {}).get("hash", 0),
            "instance_id": (equipped or {}).get("instance_id", ""),
        }
        to_payload = {
            "name": target["name"],
            "hash": target["hash"],
            "instance_id": target["instance_id"],
        }

        if target["is_equipped"]:
            return {
                "success": True,
                "from": from_payload,
                "to": to_payload,
                "available": [i for i in instances if not i["is_equipped"]],
                "message": f"现在装的就是「{target['name']}」，不用换。",
            }

        result = await self._bungie.equip_item(
            item_instance_id=target["instance_id"],
            character_id=char_id,
            membership_type=mtype,
        )
        if result.get("ErrorCode", 0) != 1:
            return {
                "success": False,
                "from": from_payload,
                "to": to_payload,
                "message": (
                    f"换上「{target['name']}」失败：{result.get('Message', '上游没给原因')}"
                ),
            }

        # 回读核对：装备位换成目标实例才算成功（假成功比失败更糟）。
        # 上游 profile 有"刚写完还读到旧值"的窗口（真机实采约 3 秒），所以要重试几次再下结论。
        fresh = await write_readback.read_until(
            lambda: self._resolver.get_profile(mid, mtype, profile_components.ARTIFACT),
            lambda profile: self._equipped_artifact_instance(profile, char_id)
            == target["instance_id"],
        )
        equipped_now = self._equipped_artifact_instance(fresh, char_id)
        if equipped_now != target["instance_id"]:
            window = int(write_readback.ATTEMPTS * write_readback.DELAY_SECONDS)
            return {
                "success": False,
                "unverified": True,
                "from": from_payload,
                "to": to_payload,
                "message": (
                    f"「{target['name']}」的写入上游返回成功，但 {window} 秒内回读装备位仍是 "
                    f"{equipped_now or '空'}（目标 {target['instance_id']}）；"
                    "上游 profile 同步有延迟，这次**没确认**，"
                    '别重复写，先用 intent="artifact" 带上 character 重新读一次。'
                ),
            }

        return {
            "success": True,
            "from": from_payload,
            "to": to_payload,
            "available": [i for i in instances if i["instance_id"] != target["instance_id"]],
            "message": (
                f"已换上「{target['name']}」"
                + (f"，原为「{from_payload['name']}」。" if from_payload["name"] else "。")
            ),
        }

    @serialized_account_action
    async def equip_artifact_mod(
        self,
        player_name: str,
        mod_hash: int,
        character: str,
    ) -> dict:
        """Equip a seasonal artifact mod.

        Args:
            player_name: Bungie name.
            mod_hash: The mod hash to equip.
            character: Character name (hunter/warlock/titan or Chinese).

        Returns:
            {"success": bool, "mod_name": str, "message": str}
        """
        class_type = resolve_character_name(character)

        p = await self._resolver.resolve_player(player_name)
        mid = p["membership_id"]
        mtype = p["membership_type"]

        profile = await self._resolver.get_profile(
            mid, mtype, profile_components.ARTIFACT
        )

        characters = profile.get("characters", {}).get("data", {})
        character_id = None
        for char_id, char_data in characters.items():
            if char_data.get("classType") == class_type:
                character_id = char_id
                break

        if not character_id:
            return {"success": False, "message": f"未找到 {character} 角色"}

        all_items = (
            profile.get("characterInventories", {})
            .get("data", {})
            .get(character_id, {})
            .get("items", [])
        )
        artifact = next(
            (item for item in all_items if item.get("bucketHash") == _ARTIFACT_BUCKET_HASH),
            None,
        )
        if not artifact:
            return {"success": False, "message": "未找到赛季神器，请确认角色已装备神器"}

        artifact_instance_id = artifact.get("itemInstanceId")
        artifact_hash = int(artifact.get("itemHash", 0) or 0)
        membership_type = (
            profile.get("profile", {}).get("data", {}).get("membershipType", 3)
        )

        # 槽位从**这件神器的定义**算，不写死槽数/槽号：不同赛季的神器槽数与阶数不同。
        slots = self._artifact_slots(artifact_hash, profile, artifact_instance_id)
        target = to_unsigned(int(mod_hash))
        if not slots:
            raise DefinitionNotFoundError(
                f"hash={artifact_hash}", "这件神器的定义里没有可插槽位。"
            )
        if not any(target in slot["candidates"] for slot in slots.values()):
            raise InvalidArgumentError(
                f"模组 {target} 不在「{self._manifest.get_item_name(artifact_hash)}」的候选里："
                '可能还没解锁，或不属于本赛季。先用 intent="artifact" 看已解锁的模组。'
            )

        candidates = [i for i, slot in slots.items() if target in slot["candidates"]]
        empty = [i for i in candidates if not (slots[i]["current"] or {}).get("plug_hash")]
        slot_index = (empty or candidates)[0]
        current = slots[slot_index]["current"] or {}
        mod_name = (
            (self._manifest.get_item_definition(target) or {}).get("displayProperties") or {}
        ).get("name", "")

        result = await self._bungie.insert_socket_plug_free(
            item_instance_id=artifact_instance_id,
            plug_item_hash=target,
            socket_index=slot_index,
            socket_array_type=0,
            character_id=character_id,
            membership_type=membership_type,
        )
        if result.get("ErrorCode") != 1:
            return {
                "success": False,
                "mod_name": mod_name,
                "message": f"装备失败：{result.get('Message', '未知错误')}",
            }

        # 回读核对：resolver.get_profile 不走缓存，这里读到的是写入后的状态
        fresh = await self._resolver.get_profile(
            mid, mtype, sorted({*profile_components.ARTIFACT, *profile_components.ITEM_SOCKETS})
        )
        verified = self._socket_plug_at(fresh, artifact_instance_id, slot_index)
        if verified != target:
            return {
                "success": False,
                "mod_name": mod_name,
                "message": (
                    f"上游返回成功，但回读第 {slot_index} 号槽是 {verified}（期望 {target}）："
                    '状态不一致，请用 intent="artifact" 复核，别当作已装好。'
                ),
            }

        payload = {
            "success": True,
            "mod_name": mod_name,
            "slot_index": slot_index,
            "to": {"plug_hash": target, "name": mod_name},
            "message": f"已装备赛季神器模组：{mod_name}（第 {slot_index} 号槽）",
        }
        if current.get("plug_hash"):
            payload["from"] = current
            payload["message"] = (
                f"已把第 {slot_index} 号槽的「{current.get('name') or current['plug_hash']}」"
                f"换成「{mod_name}」"
            )
        return payload

    def _artifact_slots(
        self, artifact_hash: int, profile: dict, artifact_instance_id: str
    ) -> dict[int, dict]:
        """神器定义 → `{槽号: {candidates, current}}`；槽号 = socketEntries 的数组位置。

        实采（见 docs/plans/SUBCLASS_ARTIFACT_PLAN.md）：组件 305 的槽条目**没有 socketIndex 字段**，
        位置只能按数组下标算；候选模组来自定义里每个 socket entry 的 plug set，
        **不是**组件 310（神器在 310 里查不到候选）。
        """
        definition = self._manifest.get_item_definition(artifact_hash) or {}
        entries = ((definition.get("sockets") or {}).get("socketEntries")) or []
        instance_sockets = (
            (
                (profile.get("itemComponents") or {})
                .get("sockets", {})
                .get("data", {})
                .get(artifact_instance_id, {})
            ).get("sockets")
        ) or []
        slots: dict[int, dict] = {}
        for index, entry in enumerate(entries):
            plug_set = int(entry.get("reusablePlugSetHash") or 0)
            candidates = {
                to_unsigned(int(plug.get("plugItemHash", 0) or 0))
                for plug in (self._manifest.get_plug_set_plugs(plug_set) or [])
            }
            current: dict = {}
            if index < len(instance_sockets):
                plug_hash = to_unsigned(int(instance_sockets[index].get("plugHash", 0) or 0))
                # 「空神器模组」这类占位插件的名字里带「空」→ 算空槽（不写死 hash）
                name = self._manifest.get_item_name(plug_hash) if plug_hash else ""
                if plug_hash and "空" not in (name or ""):
                    current = {"plug_hash": plug_hash, "name": name}
            slots[index] = {"candidates": candidates, "current": current}
        return slots

    @staticmethod
    def _socket_plug_at(profile: dict, artifact_instance_id: str, index: int) -> int:
        """按数组位置读某槽当前插件的 hash（0 = 空）。"""
        entries = (
            (
                (profile.get("itemComponents") or {})
                .get("sockets", {})
                .get("data", {})
                .get(artifact_instance_id, {})
            ).get("sockets")
        ) or []
        if index >= len(entries):
            return 0
        return to_unsigned(int(entries[index].get("plugHash", 0) or 0))
