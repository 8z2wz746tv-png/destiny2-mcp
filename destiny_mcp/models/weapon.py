"""Weapon models."""

from __future__ import annotations

from pydantic import BaseModel, Field

from .base import PerkInfo


class WeaponPerkSlot(BaseModel):
    """A slot (column) on a weapon that can hold different perks."""

    slot_name: str = Field(description="Slot type: barrel/sight/magazine/perk_1/perk_2/masterwork")
    plugs: list[PerkInfo] = Field(default_factory=list, description="All possible perks in this slot")


class WeaponPerkPool(BaseModel):
    """get_weapon_perks tool response."""

    weapon_name: str = Field(description="Weapon name")
    weapon_type: str = Field(default="", description="Weapon type (Hand Cannon, Auto Rifle, etc.)")
    item_hash: int = Field(description="Weapon definition hash")
    icon_url: str = Field(default="", description="Bungie CDN weapon icon URL")
    slots: list[WeaponPerkSlot] = Field(default_factory=list, description="Perk pool grouped by slot")


class WeaponComparison(BaseModel):
    """compare_weapon_instances 的产出：同一把武器的多个副本。

    每个副本是 `{weapon（身份块 + instance）, sockets（定义级池 + equipped 现在装的）,
    options（实例级 310：这一件能换的）, stats}`；`weapon.owned` 给副本摘要，
    便于"哪一件更好"一眼看完。没有 310 时 `options` 为空，但 `equipped` 仍在。
    """

    weapon: dict = Field(default_factory=dict, description="身份块（含 owned 摘要）")
    instances: list[dict] = Field(default_factory=list, description="每个副本一份完整模板")
    differences: list[dict] = Field(default_factory=list, description="副本之间的已装 perk 差异")


class WeaponStats(BaseModel):
    """Weapon stat values."""

    damage: int = Field(default=0, description="伤害")
    range: int = Field(default=0, description="射程")
    stability: int = Field(default=0, description="稳定性")
    handling: int = Field(default=0, description="操控性")
    reload_speed: int = Field(default=0, description="填装速度")
    aim_assist: int = Field(default=0, description="辅助瞄准")
    zoom: int = Field(default=0, description="变焦")
    airborne_effectiveness: int = Field(default=0, description="空中效率")
    recoil_direction: int = Field(default=0, description="后坐方向")
    rpm: int = Field(default=0, description="射速 (RPM)")
    magazine: int = Field(default=0, description="弹夹容量")


class WeaponSocketInfo(BaseModel):
    """A categorized socket on a weapon instance."""

    slot_label: str = Field(description="Display label: 框架/枪管/弹匣/特性1/特性2/原始特性/模组/装饰/追踪器/纪念物")
    plug_name: str = Field(default="", description="Currently equipped plug name")
    plug_hash: int = Field(default=0, description="Currently equipped plug hash")
    plug_category: str = Field(default="", description="Plug category identifier")
    description: str = Field(default="", description="Plug description/effect text")
    icon_url: str = Field(default="", description="Bungie CDN plug icon URL")


class WeaponDetail(BaseModel):
    """一把武器的完整模板：身份块 + 定义级插槽池 + 这一件的可换项 + 属性。

    形状与 `info`/`analyze`/`perk_pool` 一致（都由 `services.weapon_payload` 造），
    区别只在于这里一定绑定了一个**副本**：`sockets[].equipped` 是它现在装的，
    `options` 是它（组件 310）能换的。
    """

    weapon: dict = Field(
        default_factory=dict, description="身份块（含 roll_summary、gear_tier/item_level/quality）"
    )
    sockets: list[dict] = Field(
        default_factory=list,
        description="定义级：所有插槽与完整池，每栏带 equipped（这一件现在装的）",
    )
    options: list[dict] = Field(
        default_factory=list,
        description="实例级（组件 310）：这一件能换成什么，按 socket_index 与 sockets 对齐",
    )
    stats: list[dict] = Field(default_factory=list, description="属性列表（实例值优先）")
    perks_complete: bool = Field(default=False, description="所有当前插槽的 plug 都解析出来了")
    notes: list[str] = Field(
        default_factory=list, description="数据说明（哪些账号字段没读到、为什么为空）"
    )


class WeaponDetailResponse(BaseModel):
    """Response containing multiple weapon details."""

    weapon_type_query: str = Field(description="What type was searched for")
    weapons: list[WeaponDetail] = Field(default_factory=list)
    total_weapons: int = Field(default=0, description="How many weapons of this type the manifest knows")
    returned_weapons: int = Field(default=0, description="How many are included in this response")
    truncated: bool = Field(default=False, description="True when weapons were cut by limit")
