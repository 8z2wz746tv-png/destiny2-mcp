"""Weapon service — backward-compatible facade.

Delegates to PerkService, WeaponCompareService, and WeaponDetailService.
Kept for backward compatibility with existing imports and tests.
"""

from __future__ import annotations

from ..manifest import ManifestManager
from ..models import (
    WeaponComparison,
    WeaponDetailResponse,
    WeaponPerkPool,
)
from ..player_resolver import PlayerResolver
from .perk_service import PerkService
from .profile_cache import ProfileCache
from .weapon_compare_service import WeaponCompareService
from .weapon_detail_service import WeaponDetailService
from .wishlist_service import WishListService


class WeaponService:
    """Backward-compatible facade over the three weapon sub-services."""

    def __init__(
        self,
        bungie,  # unused, kept for signature compatibility
        manifest: ManifestManager,
        resolver: PlayerResolver,
        wishlist: WishListService | None = None,
        profile_cache: ProfileCache | None = None,
    ) -> None:
        self._manifest = manifest
        self._wishlist = wishlist

        self._perk_svc = PerkService(manifest, wishlist)
        self._compare_svc = WeaponCompareService(
            manifest, resolver, self._perk_svc, wishlist, profile_cache,
        )
        self._detail_svc = WeaponDetailService(manifest, resolver)

    async def get_weapon_perks(self, weapon_name: str) -> WeaponPerkPool:
        return await self._perk_svc.get_weapon_perks(weapon_name)

    async def compare_weapon_instances(
        self,
        player_name: str,
        weapon_name: str,
        item_instance_id: str | None = None,
    ) -> WeaponComparison:
        return await self._compare_svc.compare_weapon_instances(
            player_name,
            weapon_name,
            item_instance_id,
        )

    async def get_weapon_details_by_type(
        self, player_name: str, type_name: str
    ) -> WeaponDetailResponse:
        return await self._detail_svc.get_weapon_details_by_type(player_name, type_name)

    async def get_god_roll(self, weapon_name: str) -> dict:
        return await self._perk_svc.get_god_roll(weapon_name)
