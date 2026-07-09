"""Shared player and character resolution.

Extracts the duplicated _resolve_player / _resolve_character_id logic
from all four services into a single resolver. No caching — every call
hits the Bungie API directly for fresh data.
"""

from __future__ import annotations

from . import config
from .bungie_client import BungieClient
from .exceptions import AuthenticationError, CharacterNotFoundError, PlayerNotFoundError
from .logging_config import get_logger
from .manifest import ManifestManager, class_type_name, resolve_character_name

logger = get_logger(__name__)

CURRENT_OAUTH_PLAYER = "__destiny_current_oauth_player__"


class PlayerResolver:
    """Player name → membership ID + character ID resolution (no cache).

    Every call fetches fresh data from the Bungie API.
    """

    def __init__(
        self, bungie: BungieClient, manifest: ManifestManager
    ) -> None:
        self._bungie = bungie
        self._manifest = manifest

    async def resolve_player(self, player_name: str) -> dict:
        """Resolve a Bungie name to the first player dict.

        Raises:
            PlayerNotFoundError: If no results.
        """
        if player_name == CURRENT_OAUTH_PLAYER:
            return await self.resolve_current_player()

        results = await self._bungie.search_player(player_name)
        if not results:
            raise PlayerNotFoundError(player_name)
        return results[0]

    async def resolve_current_player(self) -> dict:
        """Resolve the Destiny membership owned by the current OAuth token."""
        try:
            return await self._bungie.get_current_destiny_membership()
        except AuthenticationError:
            if config.DESTINY_DEFAULT_PLAYER:
                logger.warning(
                    "Current OAuth membership unavailable; falling back to DESTINY_DEFAULT_PLAYER"
                )
                return await self.resolve_player(config.DESTINY_DEFAULT_PLAYER)
            raise

    async def resolve_character_id(
        self,
        membership_id: str,
        membership_type: int,
        character_name: str,
    ) -> str:
        """Convert a friendly character name to a character ID.

        Raises:
            CharacterNotFoundError: If the character doesn't exist.
        """
        class_type = resolve_character_name(character_name)
        profile = await self._bungie.get_profile(
            membership_id, membership_type, [200]
        )
        chars = profile.get("characters", {}).get("data", {})

        for char_id, char_data in chars.items():
            if char_data.get("classType") == class_type:
                return char_id

        available = [class_type_name(c["classType"]) for c in chars.values()]
        raise CharacterNotFoundError(character_name, available)

    async def get_profile(
        self, membership_id: str, membership_type: int, components: list[int]
    ) -> dict:
        """Fetch profile from Bungie API (no cache)."""
        return await self._bungie.get_profile(
            membership_id, membership_type, components
        )
