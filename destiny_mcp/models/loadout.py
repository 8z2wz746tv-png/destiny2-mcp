"""Loadout models."""

from __future__ import annotations

from typing import Any

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
    """A saved equipment loadout (配装).

    ``build_template`` follows the normalized community build shape. The
    instance-bound fields above it remain the execution representation used
    by the account equipment service.
    """

    id: str = Field(description="Unique loadout ID")
    name: str = Field(description="User-defined loadout name (e.g. 'GM 配装')")
    character: str = Field(description="Target character: hunter/warlock/titan")
    items: list[LoadoutItem] = Field(default_factory=list, description="Armor pieces in this loadout")
    subclass: LoadoutSubclassConfig | None = Field(default=None, description="Subclass configuration")
    source: str = Field(
        default="local",
        description="Origin: 'bungie' (官方槽位), 'local' (自建) or 'build' (求解候选)",
    )
    created_at: str = Field(default="", description="Creation time (ISO)")
    notes: str = Field(default="", description="User notes")
    slot_number: int | None = Field(
        default=None,
        ge=1,
        le=20,
        description="Bungie official slot number (1-20), when source=bungie",
    )
    native_character_id: str = Field(
        default="", description="Bungie character ID for an official slot"
    )
    name_hash: int | None = Field(default=None, description="Official loadout name hash")
    icon_hash: int | None = Field(default=None, description="Official loadout icon hash")
    color_hash: int | None = Field(default=None, description="Official loadout color hash")
    build_template: dict[str, Any] = Field(
        default_factory=dict,
        description=(
            "Normalized build template with class/weapons/armor/artifact/stat_targets/source; "
            "same shape as community build records and never an executable credential"
        ),
    )


class LoadoutListResponse(BaseModel):
    """get_loadouts tool response."""

    player_name: str
    scope: str = Field(
        default="account_saved_loadouts",
        description="Only saved account loadouts and Bungie official slots; not community templates",
    )
    loadout_format: str = Field(
        default="destiny2_build_template_v1",
        description="Common format used by each loadout's build_template",
    )
    loadouts: list[Loadout] = Field(default_factory=list)
    total_loadouts: int = Field(default=0, description="过滤后一共有多少套")
    returned_loadouts: int = Field(default=0, description="本次返回多少套")
    truncated: bool = Field(default=False, description="True = 被 limit 截断，不是全部")
    next_offset: int | None = Field(
        default=None, description="还有下一页时传回它的 offset；None = 已到末尾"
    )


class LoadoutOperationResult(BaseModel):
    """equip_loadout / save_loadout / delete_loadout response."""

    success: bool
    loadout_name: str = Field(default="")
    message: str = Field(default="")
    steps: list[MoveItemStep] = Field(default_factory=list, description="Equip steps (for equip_loadout)")
