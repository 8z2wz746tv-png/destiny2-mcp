"""Subclass configuration models."""

from __future__ import annotations

from pydantic import BaseModel, Field


class PlugOption(BaseModel):
    """A single available plug option for a socket."""

    plug_hash: int = Field(description="Plug item definition hash")
    name: str = Field(description="Plug name (from manifest)")


class SubclassPlug(BaseModel):
    """A single plug on a subclass (super, melee, grenade, aspect, fragment, etc.)."""

    plug_hash: int = Field(description="Plug item definition hash")
    name: str = Field(description="Plug name (from manifest)")
    socket_index: int = Field(description="Socket index on the subclass item")
    socket_type: str = Field(
        default="", description="Socket category: super, melee, grenade, aspect, fragment, class_ability"
    )
    is_active: bool = Field(default=True, description="Whether this plug is currently equipped")
    available: list[PlugOption] = Field(default_factory=list, description="All available options for this socket")


class SubclassConfig(BaseModel):
    """A character's current subclass configuration."""

    character_id: str = Field(description="Character ID")
    character_class: str = Field(description="Class name: Hunter/Warlock/Titan")
    subclass_name: str = Field(description="Subclass name (e.g. 'Gunslinger', 'Voidwalker')")
    subclass_hash: int = Field(description="Subclass item definition hash")
    item_instance_id: str = Field(default="", description="Subclass item instance ID")
    plugs: list[SubclassPlug] = Field(default_factory=list, description="All configured plugs")


class ModifySubclassPlug(BaseModel):
    """A single plug change request result."""

    socket_type: str = Field(description="What was changed: super/melee/grenade/aspect/fragment/class_ability")
    socket_index: int = Field(default=0, description="Socket index on the subclass item")
    old_plug_name: str = Field(default="", description="Previous plug name")
    new_plug_name: str = Field(default="", description="New plug name")
    new_plug_hash: int = Field(description="New plug hash")
    success: bool = Field(default=False)
    message: str = Field(default="")


class ModifySubclassResult(BaseModel):
    """modify_subclass tool response."""

    success: bool
    character: str = Field(default="")
    subclass_name: str = Field(default="")
    changes: list[ModifySubclassPlug] = Field(default_factory=list)
    message: str = Field(default="")
