"""Profile cache — fetch once, reuse across tool calls.

Caches the full Bungie profile (vault + characters + items) in memory
with a configurable TTL. Reduces API calls from 2 per tool invocation
to 1 per cache window.

Also provides name-based item lookup for matching deprecated weapons
whose hashes are no longer in the manifest.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass

import aiobungie

from ..exceptions import DestinyMCPError
from ..manifest import ManifestManager
from ..player_resolver import PlayerResolver

logger = logging.getLogger(__name__)

# Components needed for full inventory display + weapon comparison.
# 302=ItemPerks, 304=ItemStats, 305=ItemSockets, 310=ItemReusablePlugs.
_FULL_COMPONENTS = [102, 200, 201, 205, 300, 302, 304, 305, 310]

# Components for basic inventory (no sockets)
_BASIC_COMPONENTS = [102, 200, 201, 205, 300, 304]


@dataclass
class CachedProfile:
    """A cached profile snapshot."""

    player_info: dict
    profile: dict
    fetched_at: float
    components: list[int]


class ProfileCache:
    """Caches player profile data with TTL-based invalidation.

    Usage:
        cache = ProfileCache(resolver, manifest)
        profile = await cache.get_profile("player#1234", [102, 200, 201, 205, 300, 304])
    """

    def __init__(
        self,
        resolver: PlayerResolver,
        manifest: ManifestManager,
        ttl_seconds: int = 300,  # 5 minutes, same as DIM
    ) -> None:
        self._resolver = resolver
        self._manifest = manifest
        self._ttl = ttl_seconds
        self._cache: dict[str, CachedProfile] = {}
        self._refresh_task: asyncio.Task | None = None
        self._refresh_player: str = ""

    async def get_profile(
        self,
        player_name: str,
        components: list[int] | None = None,
    ) -> dict:
        """Get profile data, using cache if fresh enough.

        Args:
            player_name: Bungie name (e.g. "player#1234").
            components: Bungie API component IDs. None = full components.

        Returns:
            Raw profile dict from Bungie API.
        """
        if components is None:
            components = _FULL_COMPONENTS

        cached = self._cache.get(player_name)
        fresh_cached = cached if cached and self._is_fresh(cached) else None
        if fresh_cached:
            # Check if cached components cover the requested ones
            if all(c in fresh_cached.components for c in components):
                logger.debug("Profile cache hit for %s", player_name)
                return fresh_cached.profile

        # Cache miss or stale — fetch fresh
        logger.info("Profile cache miss for %s, fetching from API", player_name)
        p = await self._resolver.resolve_player(player_name)
        fetch_components = components
        if fresh_cached:
            # Ask Bungie for the real union. Do not mark old components as cached
            # unless the fresh payload actually contains them.
            fetch_components = list(dict.fromkeys(fresh_cached.components + components))
        profile = await self._resolver.get_profile(
            p["membership_id"], p["membership_type"], fetch_components
        )

        self._cache[player_name] = CachedProfile(
            player_info=p,
            profile=profile,
            fetched_at=time.time(),
            components=fetch_components,
        )

        return profile

    async def get_player_info(self, player_name: str) -> dict:
        """Get resolved player info (membership_id, membership_type)."""
        cached = self._cache.get(player_name)
        if cached and self._is_fresh(cached):
            return cached.player_info

        # Force a profile fetch to populate cache
        await self.get_profile(player_name)
        return self._cache[player_name].player_info

    def get_all_item_hashes(self, player_name: str) -> dict[int, str]:
        """Get all item hashes → names for a player from cached profile.

        Returns a dict mapping item_hash → display_name for ALL items
        in the player's vault and character inventories. This enables
        name-based matching for deprecated weapons.
        """
        cached = self._cache.get(player_name)
        if not cached:
            return {}

        result: dict[int, str] = {}
        profile = cached.profile

        # Vault items
        for raw in profile.get("profileInventory", {}).get("data", {}).get("items", []):
            h = raw.get("itemHash", 0)
            if h and h not in result:
                result[h] = self._manifest.get_item_name(h)

        # Character items (inventory + equipment)
        chars_data = profile.get("characters", {}).get("data", {})
        inv_data = profile.get("characterInventories", {}).get("data", {})
        equip_data = profile.get("characterEquipment", {}).get("data", {})

        for char_id in chars_data:
            for source in [
                inv_data.get(char_id, {}).get("items", []),
                equip_data.get(char_id, {}).get("items", []),
            ]:
                for raw in source:
                    h = raw.get("itemHash", 0)
                    if h and h not in result:
                        result[h] = self._manifest.get_item_name(h)

        return result

    def invalidate(self, player_name: str | None = None) -> None:
        """Invalidate cache for a player, or all players."""
        if player_name:
            self._cache.pop(player_name, None)
        else:
            self._cache.clear()

    async def start_refresh(self, player_name: str) -> None:
        """Start background periodic refresh for a player."""
        self._refresh_player = player_name
        if self._refresh_task and not self._refresh_task.done():
            return
        self._refresh_task = asyncio.create_task(self._refresh_loop())
        logger.info("Started profile cache refresh for %s (interval=%ds)", player_name, self._ttl)

    async def stop_refresh(self) -> None:
        """Stop background refresh."""
        if self._refresh_task and not self._refresh_task.done():
            self._refresh_task.cancel()
            try:
                await self._refresh_task
            except asyncio.CancelledError:
                pass
        self._refresh_task = None
        logger.info("Stopped profile cache refresh")

    def _is_fresh(self, cached: CachedProfile) -> bool:
        """Check if cached data is still within TTL."""
        return (time.time() - cached.fetched_at) < self._ttl

    async def _refresh_loop(self) -> None:
        """Background loop that refreshes the profile cache."""
        while True:
            try:
                await asyncio.sleep(self._ttl)
                if self._refresh_player:
                    logger.debug("Refreshing profile cache for %s", self._refresh_player)
                    await self.get_profile(self._refresh_player)
            except asyncio.CancelledError:
                break
            except (DestinyMCPError, aiobungie.HTTPError):
                logger.exception("Profile cache refresh failed")
