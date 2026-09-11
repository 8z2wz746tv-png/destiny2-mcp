"""Vendor models."""

from __future__ import annotations

from typing import Literal

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
    category_index: int | None = Field(
        default=None,
        description="Shelf tab this item sits on; matches categories[].index",
    )
    sale_status: int | None = Field(
        default=None,
        description="Upstream saleStatus code; semantics are undocumented, kept for cross-checking",
    )
    perks: list[PerkInfo] | None = Field(default=None, description="Weapon perks (if applicable)")


class VendorRank(BaseModel):
    """Reputation progress — only vendors that actually track a rank have this."""

    progression_hash: int = Field(description="DestinyProgressionDefinition hash")
    name: str = Field(default="", description="Reputation track name, e.g. 先锋")
    level: int = Field(default=0, description="Current rank level")
    level_cap: int = Field(default=0, description="Maximum rank level")
    progress: int = Field(default=0, description="Progress earned inside the current level")
    progress_to_next_level: int = Field(default=0, description="Progress still needed")
    next_level_at: int = Field(default=0, description="Progress value at which the next level ends")
    daily_progress: int = Field(default=0)
    daily_limit: int = Field(default=0)
    weekly_progress: int = Field(default=0)
    weekly_limit: int = Field(default=0)
    reset_hint: str = Field(default="", description="Reset cycle inferred from the upstream limits")


class VendorCategory(BaseModel):
    """One tab on a vendor's shelf."""

    index: int = Field(description="Category index used by sale items' category_index")
    name: str = Field(default="", description="Tab name")
    identifier: str = Field(default="", description="Upstream identifier, e.g. category.rank_rewards_seasonal")
    kind: Literal["sale", "rewards", "submenu"] = Field(
        default="sale",
        description="sale = normal shelf, rewards = rank-up rewards, submenu = opens another vendor page",
    )
    item_count: int = Field(default=0)
    target_vendor_hash: int | None = Field(
        default=None, description="For kind=submenu: the vendor page this tab opens"
    )
    target_vendor_name: str = Field(default="", description="For kind=submenu: that page's own name")
    target_available: bool = Field(
        default=False, description="For kind=submenu: whether that page is in the current payload"
    )


class VendorInfo(BaseModel):
    """A single vendor with its sale items."""

    vendor_hash: int = Field(description="Vendor definition hash")
    name: str = Field(default="", description="Vendor display name")
    identifier: str = Field(default="", description="Upstream vendorIdentifier, e.g. GUNSMITH")
    icon: str = Field(default="", description="Vendor icon")
    next_refresh: str = Field(default="", description="Next refresh time (ISO)")
    rank: VendorRank | None = Field(default=None, description="Reputation progress when the vendor has one")
    categories: list[VendorCategory] = Field(
        default_factory=list, description="Shelf tabs; empty only for vendors with no tab data"
    )
    total_items: int = Field(default=0, description="How many items this vendor really has")
    purchasable_items: int = Field(
        default=0, description="How many of them have no upstream purchase failure"
    )
    truncated: bool = Field(default=False, description="True when sale_items is shorter than total_items")
    sale_items: list[VendorSaleItem] = Field(default_factory=list)


class VendorInventoryResponse(BaseModel):
    """get_vendor_inventory tool response."""

    player_name: str
    character: str
    mode: Literal["menu", "detail"] = Field(
        default="detail",
        description="menu = a list of vendors to choose from, detail = one vendor's shelf",
    )
    vendors: list[VendorInfo] = Field(default_factory=list)
    total_vendors: int = Field(
        default=0,
        description="menu: vendors that have something on the shelf; detail: vendors in this answer",
    )
    returned_vendors: int = Field(default=0, description="Vendors included in this response")
    truncated: bool = Field(default=False, description="True when vendors were cut by limit")
    question: str | None = Field(
        default=None, description="What the caller should decide next, when the request was too broad"
    )
    next_actions: list[str] = Field(default_factory=list, description="Concrete follow-up calls")
    warnings: list[str] = Field(default_factory=list)
