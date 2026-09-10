"""Typed service container shared with MCP tools."""

from __future__ import annotations

from typing import TYPE_CHECKING, TypedDict

if TYPE_CHECKING:
    from .bungie_client import BungieClient
    from .manifest import ManifestManager
    from .player_resolver import PlayerResolver
    from .services.build_service import BuildService
    from .services.build_import_service import BuildImportService
    from .services.collection_service import CollectionService
    from .services.inventory_analysis_service import InventoryAnalysisService
    from .services.inventory_service import InventoryService
    from .services.loadout_service import LoadoutService
    from .services.player_service import PlayerService
    from .services.subclass_service import SubclassService
    from .services.transfer_service import TransferService
    from .services.vendor_service import VendorService
    from .services.perk_service import PerkService
    from .services.weapon_analysis_service import WeaponAnalysisService
    from .services.weapon_compare_service import WeaponCompareService
    from .services.weapon_detail_service import WeaponDetailService
    from .services.weapon_roll_filter_service import WeaponRollFilterService
    from .services.weapon_service import WeaponService
    from .services.weekly_service import WeeklyService
    from .services.weekly_analysis_service import WeeklyAnalysisService
    from .services.profile_cache import ProfileCache
    from .services.activity_service import ActivityService
    from .services.manifest_query_service import ManifestQueryService
    from .services.fragment_service import FragmentService
    from .services.artifact_service import ArtifactService
    from .services.set_bonus_service import SetBonusService
    from .services.starside_service import StarsideService


class ServiceContext(TypedDict):
    manifest: ManifestManager
    bungie: BungieClient
    resolver: PlayerResolver
    profile_cache: ProfileCache
    player_svc: PlayerService
    inventory_svc: InventoryService
    inventory_analysis_svc: InventoryAnalysisService
    transfer_svc: TransferService
    subclass_svc: SubclassService
    perk_svc: PerkService
    weapon_compare_svc: WeaponCompareService
    weapon_detail_svc: WeaponDetailService
    weapon_roll_filter_svc: WeaponRollFilterService
    weapon_analysis_svc: WeaponAnalysisService
    weapon_svc: WeaponService
    build_svc: BuildService
    vendor_svc: VendorService
    weekly_svc: WeeklyService
    weekly_analysis_svc: WeeklyAnalysisService
    loadout_svc: LoadoutService
    build_import_svc: BuildImportService
    activity_svc: ActivityService
    manifest_query_svc: ManifestQueryService
    fragment_svc: FragmentService
    artifact_svc: ArtifactService
    set_bonus_svc: SetBonusService
    collection_svc: CollectionService
    starside_svc: StarsideService
