"""Subclass service — read and modify subclass ability configuration.

Handles super, melee, grenade, class ability, movement, aspects, and fragments
via Bungie's InsertSocketPlugFree API endpoint.
"""

from __future__ import annotations

import re

from ..bungie_client import BungieClient
from ..exceptions import SubclassError
from ..logging_config import get_logger
from ..manifest import ManifestManager, class_type_name, resolve_character_name
from ..models import (
    ModifySubclassPlug,
    ModifySubclassResult,
    PlugOption,
    SubclassConfig,
    SubclassPlug,
)
from ..player_resolver import PlayerResolver
from ..utils.hash_utils import to_signed, to_unsigned

logger = get_logger(__name__)

# Subclass bucket hash — where subclass items live
SUBCLASS_BUCKET_HASH = 3284755031

# Pattern to extract socket type from plugCategoryIdentifier
# Examples: "hunter.solar.supers", "shared.void.grenades", "warlock.arc.aspects"
_CATEGORY_PATTERN = re.compile(
    r"^(?:hunter|warlock|titan|shared)\.\w+\.(.+)$"
)

# Map from the regex capture group to our friendly socket type names
_CATEGORY_TO_TYPE: dict[str, str] = {
    "supers": "super",
    "melee": "melee",
    "grenades": "grenade",
    "class_abilities": "class_ability",
    "movement": "movement",
    "aspects": "aspect",
    "fragments": "fragment",
}


def identify_socket_type(plug_category_id: str) -> str:
    """Map a plugCategoryIdentifier to a friendly socket type name.

    Examples:
        'hunter.solar.supers' -> 'super'
        'shared.void.grenades' -> 'grenade'
        'warlock.arc.aspects' -> 'aspect'
    """
    m = _CATEGORY_PATTERN.match(plug_category_id)
    if m:
        return _CATEGORY_TO_TYPE.get(m.group(1), m.group(1))
    return plug_category_id


class SubclassService:
    """Operations for reading and modifying subclass ability configuration."""

    def __init__(
        self,
        bungie: BungieClient,
        manifest: ManifestManager,
        resolver: PlayerResolver,
    ) -> None:
        self._bungie = bungie
        self._manifest = manifest
        self._resolver = resolver

    # ── Helpers ──────────────────────────────────────────────────────

    def _find_subclass_in_equipment(
        self, profile: dict, character_id: str
    ) -> dict | None:
        """Find the equipped subclass item from character equipment.

        Returns the raw equipment item dict for the subclass, or None.
        """
        equip_data = (
            profile.get("characterEquipment", {})
            .get("data", {})
            .get(character_id, {})
            .get("items", [])
        )
        for item in equip_data:
            if item.get("bucketHash") == SUBCLASS_BUCKET_HASH:
                return item
        return None

    def _get_plug_info(self, plug_hash: int) -> dict:
        """Get plug item info from manifest, handling unsigned hash conversion."""
        # Try direct lookup first
        info = self._manifest.get_item_info(plug_hash)
        if info:
            return info
        # Try unsigned→signed conversion
        signed = to_signed(plug_hash)
        info = self._manifest.get_item_info(signed)
        if info:
            return info
        return {
            "name": f"#{plug_hash}",
            "itemType": 0,
            "itemTypeName": "Unknown",
        }

    def _get_plug_name(self, plug_hash: int) -> str:
        """Get plug name from manifest."""
        info = self._get_plug_info(plug_hash)
        return info.get("name", f"#{plug_hash}")

    def _classify_socket(
        self,
        socket_data: dict,
        socket_def: dict,
    ) -> str:
        """Determine the socket type from the socket definition and current plug.

        Looks at the currently equipped plug's plugCategoryIdentifier first,
        then falls back to reusable plugs from the socket definition.
        """
        # Check the currently active plug
        current_plug_hash = socket_data.get("plugHash")
        if current_plug_hash:
            signed_hash = to_signed(current_plug_hash)
            # Query manifest directly for the plug's category identifier
            info = self._manifest.get_plug_category_identifier(signed_hash)
            if info:
                return identify_socket_type(info)

        # Fallback: look at reusable plugs to identify the category
        reusable = socket_data.get("reusablePlugs", [])
        for plug in reusable:
            ph = plug.get("plugItemHash")
            if ph:
                signed_hash = to_signed(ph)
                info = self._manifest.get_plug_category_identifier(signed_hash)
                if info:
                    return identify_socket_type(info)

        return "unknown"

    # ── Public API ──────────────────────────────────────────────────

    async def get_subclass(
        self, player_name: str, character: str
    ) -> SubclassConfig:
        """Read a character's current subclass configuration.

        Returns the subclass name and all configured plugs (super, melee,
        grenade, class ability, movement, aspects, fragments).

        Raises:
            PlayerNotFoundError, CharacterNotFoundError, SubclassError.
        """
        logger.info(
            "Getting subclass config: player=%s character=%s",
            player_name, character,
        )
        p = await self._resolver.resolve_player(player_name)
        mid = p["membership_id"]
        mtype = p["membership_type"]

        char_id = await self._resolver.resolve_character_id(mid, mtype, character)
        class_type = resolve_character_name(character)

        # Fetch profile with equipment + socket data
        # 200=Characters, 205=CharacterEquipment, 305=ItemSockets
        profile = await self._resolver.get_profile(
            mid, mtype, [200, 205, 305]
        )

        # Find equipped subclass
        subclass_item = self._find_subclass_in_equipment(profile, char_id)
        if not subclass_item:
            raise SubclassError(
                "Get subclass",
                f"No subclass equipped on {class_type_name(class_type)}.",
            )

        subclass_hash = subclass_item["itemHash"]
        subclass_name = self._manifest.get_item_name(subclass_hash)
        subclass_instance_id = str(subclass_item.get("itemInstanceId", "0"))

        # Read socket data
        sockets_data = (
            profile.get("itemComponents", {})
            .get("sockets", {})
            .get("data", {})
            .get(subclass_instance_id, {})
            .get("sockets", [])
        )

        # Read the subclass definition from manifest for socket type hashes
        subclass_def = self._manifest.get_item_definition(subclass_hash)
        socket_entries = (
            subclass_def.get("sockets", {}).get("socketEntries", [])
            if subclass_def else []
        )

        plugs: list[SubclassPlug] = []
        for i, socket_data in enumerate(sockets_data):
            current_plug_hash = socket_data.get("plugHash")
            if not current_plug_hash:
                continue

            # Get the socket definition if available
            socket_def = socket_entries[i] if i < len(socket_entries) else {}

            # Identify socket type
            socket_type = self._classify_socket(socket_data, socket_def)

            plug_name = self._get_plug_name(current_plug_hash)

            # Get all available options from the plug set
            available: list[PlugOption] = []
            plug_set_hash = socket_def.get("reusablePlugSetHash")
            if plug_set_hash:
                plug_set_plugs = self._manifest.get_plug_set_plugs(plug_set_hash)
                if plug_set_plugs:
                    available = [
                        PlugOption(plug_hash=p["plugItemHash"], name=p["name"])
                        for p in plug_set_plugs
                    ]

            plugs.append(
                SubclassPlug(
                    plug_hash=current_plug_hash,
                    name=plug_name,
                    socket_index=i,
                    socket_type=socket_type,
                    is_active=socket_data.get("isEnabled", True),
                    available=available,
                )
            )

        logger.info(
            "Subclass config for %s/%s: %s with %d plugs",
            player_name, character, subclass_name, len(plugs),
        )
        return SubclassConfig(
            character_id=char_id,
            character_class=class_type_name(class_type),
            subclass_name=subclass_name,
            subclass_hash=subclass_hash,
            item_instance_id=subclass_instance_id,
            plugs=plugs,
        )

    async def modify_subclass(
        self,
        player_name: str,
        character: str,
        changes: dict[str, str],
    ) -> ModifySubclassResult:
        """Modify a character's subclass configuration.

        Args:
            player_name: Bungie name.
            character: Character name (hunter/warlock/titan or Chinese).
            changes: Dict mapping socket type to target plug name.
                     Keys: super, melee, grenade, class_ability, movement,
                     aspect, fragment.
                     Values: Plug name in Chinese or English (fuzzy matched).

        Returns:
            ModifySubclassResult with per-change results.

        Raises:
            PlayerNotFoundError, CharacterNotFoundError, SubclassError.
        """
        logger.info(
            "Modifying subclass: player=%s character=%s changes=%s",
            player_name, character, changes,
        )
        p = await self._resolver.resolve_player(player_name)
        mid = p["membership_id"]
        mtype = p["membership_type"]

        char_id = await self._resolver.resolve_character_id(mid, mtype, character)
        class_type = resolve_character_name(character)

        # First, read current subclass config
        config = await self.get_subclass(player_name, character)

        # Build maps for socket lookup:
        # - single sockets: type → plug (super, melee, grenade, class_ability, movement)
        # - multi sockets: type → [plug, ...] (aspects, fragments)
        current_by_type: dict[str, SubclassPlug] = {}
        multi_by_type: dict[str, list[SubclassPlug]] = {}
        for plug in config.plugs:
            if plug.socket_type == "unknown":
                continue
            if plug.socket_type in ("aspect", "fragment"):
                multi_by_type.setdefault(plug.socket_type, []).append(plug)
            else:
                current_by_type[plug.socket_type] = plug

        plug_results: list[ModifySubclassPlug] = []

        for target_key, target_name in changes.items():
            normalized_key = target_key.strip().lower()

            # Parse the target: "super" → type=super, index=0
            # "aspect_2" → type=aspect, index=1 (0-based)
            # "fragment_3" → type=fragment, index=2
            if "_" in normalized_key and normalized_key[-1].isdigit():
                base_type = normalized_key.rsplit("_", 1)[0]
                slot_num = int(normalized_key.rsplit("_", 1)[1]) - 1  # 0-based
            else:
                base_type = normalized_key
                slot_num = 0

            # Find the current plug
            if base_type in ("aspect", "fragment"):
                plugs_list = multi_by_type.get(base_type, [])
                if slot_num >= len(plugs_list):
                    plug_results.append(
                        ModifySubclassPlug(
                            socket_type=target_key,
                            socket_index=-1,
                            new_plug_name=target_name,
                            new_plug_hash=0,
                            success=False,
                            message=f"Slot '{target_key}' not found on this subclass (only {len(plugs_list)} {base_type} slot(s)).",
                        )
                    )
                    continue
                current = plugs_list[slot_num]
            else:
                current = current_by_type.get(base_type)
            if not current:
                plug_results.append(
                    ModifySubclassPlug(
                        socket_type=target_key,
                        socket_index=-1,
                        new_plug_name=target_name,
                        new_plug_hash=0,
                        success=False,
                        message=f"Socket type '{base_type}' not found on this subclass.",
                    )
                )
                continue

            # Search for the target plug in the manifest
            search_results = self._manifest.search(target_name, limit=10)
            if not search_results:
                plug_results.append(
                    ModifySubclassPlug(
                        socket_type=target_key,
                        socket_index=current.socket_index,
                        old_plug_name=current.name,
                        new_plug_name=target_name,
                        new_plug_hash=0,
                        success=False,
                        message=f"Plug '{target_name}' not found in manifest.",
                    )
                )
                continue

            # Find the best match that's a valid plug for this socket type
            target_plug_hash = None
            target_plug_name = ""
            for result in search_results:
                signed_hash = to_signed(result["itemHash"])
                cat_id = self._manifest.get_plug_category_identifier(signed_hash)
                if cat_id:
                    identified_type = identify_socket_type(cat_id)
                    if identified_type == base_type:
                        target_plug_hash = to_unsigned(result["itemHash"])
                        target_plug_name = result["name"]
                        break

            if not target_plug_hash:
                plug_results.append(
                    ModifySubclassPlug(
                        socket_type=target_key,
                        socket_index=current.socket_index,
                        old_plug_name=current.name,
                        new_plug_name=target_name,
                        new_plug_hash=0,
                        success=False,
                        message=f"'{target_name}' is not a valid {base_type} for this subclass.",
                    )
                )
                continue

            # Check if already equipped
            if target_plug_hash == current.plug_hash:
                plug_results.append(
                    ModifySubclassPlug(
                        socket_type=target_key,
                        socket_index=current.socket_index,
                        old_plug_name=current.name,
                        new_plug_name=target_plug_name,
                        new_plug_hash=target_plug_hash,
                        success=True,
                        message=f"'{target_plug_name}' is already equipped.",
                    )
                )
                continue

            # Call InsertSocketPlugFree
            result = await self._bungie.insert_socket_plug_free(
                item_instance_id=config.item_instance_id,
                plug_item_hash=target_plug_hash,
                socket_index=current.socket_index,
                socket_array_type=0,
                character_id=char_id,
                membership_type=mtype,
            )

            ok = result.get("ErrorCode", 0) == 1
            plug_results.append(
                ModifySubclassPlug(
                    socket_type=target_key,
                    socket_index=current.socket_index,
                    old_plug_name=current.name,
                    new_plug_name=target_plug_name,
                    new_plug_hash=target_plug_hash,
                    success=ok,
                    message=(
                        f"Changed {base_type}: '{current.name}' → '{target_plug_name}'"
                        if ok
                        else f"Failed: {result.get('Message', 'Unknown error')}"
                    ),
                )
            )

            if ok:
                logger.info(
                    "Changed %s: '%s' → '%s' on %s/%s",
                    target_key, current.name, target_plug_name,
                    player_name, character,
                )

        all_ok = all(r.success for r in plug_results)
        return ModifySubclassResult(
            success=all_ok,
            character=class_type_name(class_type),
            subclass_name=config.subclass_name,
            changes=plug_results,
            message=(
                f"All changes applied to {config.subclass_name}."
                if all_ok
                else f"Some changes failed on {config.subclass_name}."
            ),
        )
