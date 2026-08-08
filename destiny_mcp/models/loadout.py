"""Loadout models."""

from __future__ import annotations

from pydantic import BaseModel, Field

from .base import MoveItemStep


class LoadoutItem(BaseModel):
    """A single item in a loadout."""

    item_hash: int = Field(description="Item definition hash")
    name: str = Field(default="", description="Item name (from manifest)")
    slot: str = Field(description="Armor slot: helmet/gauntlets/chest/legs/class_item")
    item_instance_id: str = Field(default="", description="Item instance ID (if from current inventory)")
    perks: list[int] = Field(default_factory=list, description="Equipped perk plug hashes")
    mods: list[int] = Field(default_factory=list, description="Equipped mod plug hashes")
    mod_sockets: dict[int, int] = Field(
        default_factory=dict,
        description="Exact armor mod socket index to equipped plug hash",
    )
    source_location: str = Field(
        default="",
        description="Location captured with the loadout: vault/hunter/warlock/titan",
    )
    source_character_id: str = Field(
        default="",
        description="Character ID captured with the loadout, when applicable",
    )
    was_equipped: bool = Field(
        default=False,
        description="Whether the item was equipped when the loadout was captured",
    )


class LoadoutSubclassConfig(BaseModel):
    """Subclass configuration saved in a loadout.

    System-wide single source of truth for subclass configuration (ADR-008).
    All equip logic (Build Import, DIM import, voice, overlay) consumes this model.
    """

    subclass_item_hash: int = Field(default=0, description="Equipped subclass item hash")
    subclass_instance_id: str = Field(
        default="", description="Equipped subclass item instance ID"
    )
    super_hash: int = Field(default=0, description="Super ability plug hash")
    grenade_hash: int = Field(default=0, description="Grenade plug hash")
    melee_hash: int = Field(default=0, description="Melee ability plug hash")
    class_ability_hash: int = Field(default=0, description="Class ability plug hash")
    movement_hash: int = Field(default=0, description="Movement ability plug hash")
    aspect_hashes: list[int] = Field(default_factory=list, description="Aspect plug hashes")
    fragment_hashes: list[int] = Field(default_factory=list, description="Fragment plug hashes")
    plug_sockets: dict[int, int] = Field(
        default_factory=dict,
        description="Exact subclass socket index to equipped plug hash",
    )


class Loadout(BaseModel):
    """A saved equipment loadout (配装)."""

    id: str = Field(description="Unique loadout ID")
    name: str = Field(description="User-defined loadout name (e.g. 'GM 配装')")
    character: str = Field(description="Target character: hunter/warlock/titan")
    items: list[LoadoutItem] = Field(default_factory=list, description="Armor pieces in this loadout")
    subclass: LoadoutSubclassConfig | None = Field(default=None, description="Subclass configuration")
    source: str = Field(default="local", description="Origin: 'bungie' (官方) or 'local' (自建)")
    created_at: str = Field(default="", description="Creation time (ISO)")
    notes: str = Field(default="", description="User notes")


class LoadoutListResponse(BaseModel):
    """get_loadouts tool response."""

    player_name: str
    loadouts: list[Loadout] = Field(default_factory=list)


class LoadoutOperationResult(BaseModel):
    """equip_loadout / save_loadout / delete_loadout response."""

    success: bool
    loadout_name: str = Field(default="")
    message: str = Field(default="")
    steps: list[MoveItemStep] = Field(default_factory=list, description="Equip steps (for equip_loadout)")
