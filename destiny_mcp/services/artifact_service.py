"""Artifact service — business logic for seasonal artifact operations.

Extracted from artifact_tools.py per Rule 1: tools should not contain
business logic.
"""

from __future__ import annotations

from ..bungie_client import BungieClient
from ..exceptions import DefinitionNotFoundError
from ..logging_config import get_logger
from . import profile_components
from ..manifest import ManifestManager, resolve_character_name
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
                "success": True,
                "artifacts": artifacts,
                "message": f"找到 {len(artifacts)} 个赛季神器。",
            }

        artifact = self._manifest.get_artifact_by_name(artifact_name)
        if not artifact:
            raise DefinitionNotFoundError(artifact_name, "没有名称包含它的赛季神器。")

        current = self._manifest.get_current_artifact()
        return {
            "success": True,
            "artifact": artifact,
            "current_artifact": current,
            "message": f"已读取赛季神器：{artifact['name']}。",
        }

    def get_artifact_mod_info(self, mod_hash: int) -> dict:
        """Get details for a seasonal artifact mod by hash."""
        mod_info = self._manifest.get_artifact_mod_details(mod_hash)
        if not mod_info:
            raise DefinitionNotFoundError(f"hash={mod_hash}", "没有这个神器模组。")
        return {
            "success": True,
            "artifact_mod": mod_info,
            "message": f"已读取神器模组：{mod_info['name']}。",
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
        artifact_instance_id = None

        for item in all_items:
            if item.get("bucketHash") == _ARTIFACT_BUCKET_HASH:
                artifact_instance_id = item.get("itemInstanceId")
                break

        if not artifact_instance_id:
            return {"success": False, "message": "未找到赛季神器，请确认角色已装备神器"}

        membership_type = (
            profile.get("profile", {}).get("data", {}).get("membershipType", 3)
        )

        result = await self._bungie.insert_socket_plug_free(
            item_instance_id=artifact_instance_id,
            plug_item_hash=mod_hash,
            socket_index=0,
            socket_array_type=0,
            character_id=character_id,
            membership_type=membership_type,
        )

        if result.get("ErrorCode") == 1:
            mod_def = self._manifest.get_item_definition(mod_hash)
            mod_name = (mod_def.get("displayProperties") or {}).get("name", "") if mod_def else ""
            return {"success": True, "mod_name": mod_name, "message": f"已装备赛季神器模组：{mod_name}"}
        else:
            return {"success": False, "message": f"装备失败：{result.get('Message', '未知错误')}"}
