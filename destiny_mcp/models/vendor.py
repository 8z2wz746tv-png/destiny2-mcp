"""Vendor models."""

from __future__ import annotations

from pydantic import BaseModel, Field

from .base import PerkInfo


class VendorCost(BaseModel):
    """Purchase cost for a vendor sale item."""

    item_hash: int = Field(description="Currency item hash")
    item_name: str = Field(default="", description="Currency name (Glimmer, Legendary Shards, etc.)")
    quantity: int = Field(description="Amount required")


class VendorSaleItem(BaseModel):
    """A single item for sale from a vendor."""

    vendor_item_index: int = Field(description="Index in vendor's itemList")
    item_hash: int = Field(description="Item definition hash")
    name: str = Field(default="", description="Item name")
    item_type: str = Field(default="", description="Item type name")
    tier: str = Field(default="", description="Tier: 传说/异域")
    icon: str = Field(default="", description="Icon path")
    icon_url: str = Field(default="", description="Bungie CDN icon URL for rendering in web UI")
    costs: list[VendorCost] = Field(default_factory=list, description="Purchase costs")
    owned: bool = Field(default=False, description="Already owned")
    can_be_sold: bool = Field(default=True, description="Available for purchase")
    failure_reasons: list[str] = Field(default_factory=list, description="Why can't buy")
    perks: list[PerkInfo] | None = Field(default=None, description="Weapon perks (if applicable)")


class VendorInfo(BaseModel):
    """A single vendor with its sale items."""

    vendor_hash: int = Field(description="Vendor definition hash")
    name: str = Field(default="", description="Vendor display name")
    icon: str = Field(default="", description="Vendor icon")
    next_refresh: str = Field(default="", description="Next refresh time (ISO)")
    sale_items: list[VendorSaleItem] = Field(default_factory=list)


class VendorInventoryResponse(BaseModel):
    """get_vendor_inventory tool response."""

    player_name: str
    character: str
    vendors: list[VendorInfo] = Field(default_factory=list)
