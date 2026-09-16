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


class SubclassSwitch(BaseModel):
    """换子职业物品的结果。

    单独一条、不塞进 `changes`：换的是**哪件物品**，插槽变更改的是这件物品上的**槽**，
    两件事的失败原因和回读方式都不一样（一个看装备位，一个看 305），混在一起就没法核对。
    """

    requested: str = Field(default="", description="调用方给的叫法，原样回显")
    from_name: str = Field(default="", description="换之前装备的子职业名")
    from_element: str = Field(default="", description="换之前的元素规范键")
    to_name: str = Field(default="", description="回读到的、换之后的子职业名")
    to_element: str = Field(default="", description="换之后的元素规范键")
    item_hash: int = Field(default=0, description="换上的子职业物品定义 hash")
    item_instance_id: str = Field(default="", description="换上的子职业物品实例 ID")
    success: bool = Field(default=False)
    unverified: bool = Field(
        default=False,
        description="上游说写入成功、但回读窗口内没看到变化（同步延迟）——没确认，别重复写",
    )
    message: str = Field(default="")


class ModifySubclassResult(BaseModel):
    """modify_subclass tool response."""

    success: bool
    character: str = Field(default="")
    subclass_name: str = Field(default="", description="改完（含换子职业）之后实际装备的子职业名")
    subclass_switch: SubclassSwitch | None = Field(
        default=None, description="请求里带了 subclass 时才有：换子职业物品的结果"
    )
    changes: list[ModifySubclassPlug] = Field(default_factory=list)
    message: str = Field(default="")
