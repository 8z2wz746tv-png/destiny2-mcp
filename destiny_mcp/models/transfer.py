"""Transfer and equip result models."""

from __future__ import annotations

from pydantic import BaseModel, Field

from .base import MoveItemStep


class TransferResult(BaseModel):
    """transfer_item tool response."""

    success: bool
    item_name: str = Field(default="")
    item_instance_id: str = Field(default="")
    from_location: str = Field(default="")
    to_location: str = Field(default="")
    message: str = Field(default="")


class EquipResult(BaseModel):
    """equip_item tool response."""

    success: bool
    item_name: str = Field(default="")
    character: str = Field(default="")
    message: str = Field(default="")


class ItemCandidate(BaseModel):
    """候选装备（用于消歧义：同名多件时让用户选择）。"""

    item_instance_id: str = Field(description="物品实例 ID")
    name: str = Field(default="", description="物品名称")
    power: int | None = Field(default=None, description="光等")
    location: str = Field(default="", description="所在位置：vault/hunter/warlock/titan")
    is_equipped: bool = Field(default=False, description="是否已装备")
    perks: list[str] = Field(default_factory=list, description="Perk 名称列表（武器）")
    stats: str = Field(default="", description="六维属性摘要（护甲）")


class MoveItemResult(BaseModel):
    """move_item tool response."""

    success: bool
    item_name: str = Field(default="")
    from_location: str = Field(default="")
    to_location: str = Field(default="")
    equipped: bool = Field(default=False)
    steps: list[MoveItemStep] = Field(default_factory=list)
    message: str = Field(default="")
    needs_disambiguation: bool = Field(
        default=False,
        description="True 时说明存在多件同名装备，需要用户从 candidates 中选择",
    )
    candidates: list[ItemCandidate] = Field(
        default_factory=list,
        description="候选装备列表（needs_disambiguation=True 时有值）",
    )
    question: str = Field(
        default="",
        description="格式化的提问文本（needs_disambiguation=True 时有值）。直接展示给用户，不要修改。",
    )
