"""Artifact service — business logic for seasonal artifact operations.

Extracted from artifact_tools.py per Rule 1: tools should not contain
business logic.
"""

from __future__ import annotations

from ..bungie_client import BungieClient
from ..exceptions import DefinitionNotFoundError, InvalidArgumentError
from ..logging_config import get_logger
from . import profile_components
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
