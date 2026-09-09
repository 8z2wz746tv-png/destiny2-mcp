"""Shared build contracts, independent of import adapters."""

from __future__ import annotations

from typing import Literal, Self

from pydantic import BaseModel, Field, model_validator

from .models import LoadoutItem


class BuildRecipe(BaseModel):
    """系统内部标准配装表示。全部使用 Hash。

    所有名称已通过 Normalizer 归一化为 Bungie Definition Hash。
    参见 ADR-007: Canonical Build Strategy。
    """

    class_type: str = Field(
        default="",
        description="职业类型：hunter/warlock/titan",
    )
    exotic_hash: int | None = Field(
        default=None,
        description="金装 hash",
    )
    weapon_hashes: list[int] = Field(
        default_factory=list,
        description="武器 hash 列表（最多 3 把）",
    )
    subclass_item_hash: int | None = Field(
        default=None,
        description="子职业物品 Hash",
    )
    super_hash: int | None = Field(
        default=None,
        description="超能 hash",
    )
    grenade_hash: int | None = Field(
        default=None,
        description="手雷 hash",
    )
    melee_hash: int | None = Field(
        default=None,
        description="近战技能 hash",
    )
    class_ability_hash: int | None = Field(
        default=None,
        description="职业技能 hash",
    )
    movement_hash: int | None = Field(
        default=None,
        description="移动技能 hash",
    )
    aspect_hashes: list[int] = Field(
        default_factory=list,
        description="星象 hash 列表",
    )
    fragment_hashes: list[int] = Field(
        default_factory=list,
        description="碎片 hash 列表",
    )
    target_stats: dict[str, int] = Field(
        default_factory=dict,
        description="属性目标（Renegades 属性名：weapons/health/class_stat/grenade/melee/super_stat）",
    )
    @property
    def is_empty(self) -> bool:
        """所有关键字段均为空。"""
        return (
            not self.class_type
            and not self.exotic_hash
            and not self.weapon_hashes
            and not self.super_hash
            and not self.target_stats
        )


class CanonicalBuild(BuildRecipe):
    """Backward-compatible wire format. Not sufficient proof of executability."""

    subclass_instance_id: str = Field(
        default="",
        description="确认时已装备的子职业实例 ID",
    )
    subclass_plug_sockets: dict[int, int] = Field(
        default_factory=dict,
        description="准确的子职业插槽索引到 Plug Hash",
    )
    items: list[LoadoutItem] = Field(
        default_factory=list,
        description="可执行装备项；包含准确实例 ID 和逐件模组 Hash",
    )
    snapshot_version: str = Field(
        default="",
        description="生成该方案时的护甲库存快照版本",
    )
    execution_id: str = Field(
        default="",
        description="服务端生成的一次性候选 ID，用于绑定用户确认内容",
    )


class ExecutableBuild(CanonicalBuild):
    """Complete instance-bound plan; still requires server candidate verification."""

    class_type: Literal["hunter", "warlock", "titan"]
    items: list[LoadoutItem] = Field(min_length=5, max_length=5)
    snapshot_version: str = Field(min_length=1)
    execution_id: str = Field(min_length=1)

    @model_validator(mode="after")
    def validate_exact_items(self) -> Self:
        ids = [item.item_instance_id for item in self.items]
        if any(not value.strip() for value in ids) or len(set(ids)) != 5:
            raise ValueError("Five unique item instance IDs are required.")
        if {item.slot for item in self.items} != {
            "helmet", "gauntlets", "chest", "legs", "class_item",
        }:
            raise ValueError("One item for each armor slot is required.")
        if any(mod <= 0 for item in self.items for mod in item.mods):
            raise ValueError("Mod hashes must be positive.")
        return self
