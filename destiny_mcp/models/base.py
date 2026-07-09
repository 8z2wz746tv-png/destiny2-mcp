"""Shared base models used across multiple domains."""

from __future__ import annotations

from pydantic import BaseModel, Field


class ArmorStats(BaseModel):
    """Armor six-stat values (Renegades update).

    'class_stat' = Class stat (class ability cooldown), 'super_stat' = Super stat.
    Trailing underscores avoid Python keyword conflicts.
    """

    weapons: int = 0
    health: int = 0
    class_stat: int = 0
    grenade: int = 0
    melee: int = 0
    super_stat: int = 0

    @property
    def total(self) -> int:
        return self.weapons + self.health + self.class_stat + self.grenade + self.melee + self.super_stat

    def get(self, stat_name: str) -> int:
        """Get a stat value by name (e.g. 'health')."""
        return getattr(self, stat_name, 0)


class PerkInfo(BaseModel):
    """A single perk/plug in a weapon perk pool."""

    plug_hash: int = Field(description="Plug item definition hash")
    name: str = Field(description="Perk name (from manifest)")
    description: str = Field(default="", description="Perk effect description")
    plug_category: str = Field(default="", description="Category: barrel/sight/magazine/perk/trait/masterwork")
    god_roll_pve: bool = Field(default=False, description="Recommended for PvE (DIM wish list)")
    god_roll_pvp: bool = Field(default=False, description="Recommended for PvP (DIM wish list)")


class MoveItemStep(BaseModel):
    """A single step in a move_item operation."""

    action: str = Field(description="search / transfer / equip")
    detail: str = Field(description="Human-readable step description")
    success: bool = Field(default=False)
