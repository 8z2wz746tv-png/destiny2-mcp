"""Inventory models."""

from __future__ import annotations

from pydantic import BaseModel, Field

from .base import ArmorStats


class InventoryItem(BaseModel):
    """A single item in inventory."""

    item_instance_id: str = Field(description="Unique item instance ID")
    item_hash: int = Field(description="Item definition hash")
    name: str = Field(description="Item name (from manifest)")
    item_type: str = Field(default="Unknown", description="Item type name (English)")
    item_type_display: str = Field(default="", description="Item type display name (Chinese, e.g. 手炮/冲锋枪)")
    power: int | None = Field(default=None, description="Attack/Defense value")
    bucket_type: str = Field(default="", description="Inventory bucket (Kinetic/Energy/etc.)")
    is_equipped: bool = Field(default=False)
    quantity: int = Field(default=1)
    location: str = Field(description="Where the item is: vault, hunter, warlock, titan")
    character_id: str = Field(default="", description="Character ID if on a character")
    stats: ArmorStats | None = Field(default=None, description="Armor six-stats (only for armor items)")
    icon_url: str = Field(default="", description="Bungie CDN icon URL for rendering in web UI")


class InventoryResponse(BaseModel):
    """get_inventory tool response."""

    location: str = Field(description="Queried location")
    items: list[InventoryItem] = Field(default_factory=list)


class SearchItemsResponse(BaseModel):
    """search_items tool response."""

    query: str = Field(description="Original search query")
    items: list[InventoryItem] = Field(default_factory=list)
