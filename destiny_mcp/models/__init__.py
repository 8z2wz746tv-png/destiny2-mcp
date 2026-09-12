"""Pydantic data models for MCP tool responses.

Re-exports all models from sub-modules for backward compatibility.
Import from this module or from specific sub-modules as needed.
"""

from .base import ArmorStats, MoveItemStep, PerkInfo
from .inventory import InventoryItem, InventoryResponse, SearchItemsResponse
from .loadout import Loadout, LoadoutItem, LoadoutListResponse, LoadoutOperationResult, LoadoutSubclassConfig
from .player import CharacterInfo, PlayerInfo, ProfileResponse
from .subclass import ModifySubclassPlug, ModifySubclassResult, PlugOption, SubclassConfig, SubclassPlug
from .transfer import EquipResult, ItemCandidate, MoveItemResult, TransferResult
from .vendor import (
    VendorCategory,
    VendorCost,
    VendorInfo,
    VendorInventoryResponse,
    VendorRank,
    VendorSaleItem,
)
from .weapon import (
    WeaponComparison,
    WeaponDetail,
    WeaponDetailResponse,
    WeaponPerkPool,
    WeaponPerkSlot,
    WeaponSocketInfo,
    WeaponStats,
)
from .weekly import WeeklyActivity, WeeklyMilestone, WeeklyResetResponse

__all__ = [
    # base
    "ArmorStats",
    "MoveItemStep",
    "PerkInfo",
    # player
    "CharacterInfo",
    "PlayerInfo",
    "ProfileResponse",
    # inventory
    "InventoryItem",
    "InventoryResponse",
    "SearchItemsResponse",
    # transfer
    "TransferResult",
    "EquipResult",
    "ItemCandidate",
    "MoveItemResult",
    # subclass
    "PlugOption",
    "SubclassPlug",
    "SubclassConfig",
    "ModifySubclassPlug",
    "ModifySubclassResult",
    # weapon
    "WeaponPerkSlot",
    "WeaponPerkPool",
    "WeaponComparison",
    "WeaponStats",
    "WeaponSocketInfo",
    "WeaponDetail",
    "WeaponDetailResponse",
    # vendor
    "VendorCost",
    "VendorCategory",
    "VendorSaleItem",
    "VendorInfo",
    "VendorInventoryResponse",
    "VendorRank",
    # weekly
    "WeeklyActivity",
    "WeeklyMilestone",
    "WeeklyResetResponse",
    # loadout
    "LoadoutItem",
    "LoadoutSubclassConfig",
    "Loadout",
    "LoadoutListResponse",
    "LoadoutOperationResult",
]
