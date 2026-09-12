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


class WeaponComparisonInstance(BaseModel):
    """A single weapon instance with its current perks."""

    instance_id: str = Field(description="Item instance ID")
    location: str = Field(description="Where this weapon is (vault/hunter/warlock/titan)")
    power: int | None = Field(default=None, description="Power level")
    perks: list[PerkInfo] = Field(default_factory=list, description="Current perks on this instance")
    god_roll_score: str = Field(default="", description="God roll score (e.g. 'PvE 3/5 | PvP 1/4')")
    icon_url: str = Field(default="", description="Bungie CDN icon URL for rendering in web UI")


class WeaponComparison(BaseModel):
    """compare_weapon_instances tool response."""

    weapon_name: str = Field(description="Weapon name")
    instances: list[WeaponComparisonInstance] = Field(default_factory=list)
    differences: list[dict] = Field(default_factory=list, description="Perk differences between instances")


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
    """Comprehensive weapon instance info."""

    instance_id: str = Field(description="Item instance ID")
    item_hash: int = Field(description="Weapon definition hash")
    name: str = Field(description="Weapon name")
    weapon_type: str = Field(default="", description="Weapon type display name (e.g. 微型冲锋枪)")
    tier: str = Field(default="", description="Tier: 传说/异域")
    damage_type: str = Field(default="", description="伤害类型: 动能/烈日/电弧/虚空/冰影/编织")
    ammo_type: str = Field(default="", description="弹药类型: 白弹/绿弹/紫弹")
    power: int | None = Field(default=None, description="Power/light level")
    location: str = Field(default="", description="Where the weapon is")
    is_equipped: bool = Field(default=False)
    sockets: list[WeaponSocketInfo] = Field(default_factory=list, description="All sockets, categorized")
    perks_complete: bool = Field(default=False, description="All current socket plugs were resolved")
    stats: WeaponStats = Field(default_factory=WeaponStats, description="Weapon stat values")
    icon_url: str = Field(default="", description="Bungie CDN icon URL for rendering in web UI")
    # ── 副本级（组件 300/310）：账号数据，Manifest 里没有；没读到就是 None ──
    gear_tier: int | None = Field(
        default=None, description="护甲 3.0 的 T1–T5 等级（0 = 旧装备）；没读到为 null"
    )
    item_level: int | None = Field(default=None, description="Bungie 给的 itemLevel；没读到为 null")
    quality: int | None = Field(default=None, description="Bungie 给的 quality；没读到为 null")
    locked: bool | None = Field(default=None, description="是否已上锁；没读到为 null")
    tracked: bool | None = Field(default=None, description="是否已追踪；没读到为 null")
    options: list[dict] = Field(
        default_factory=list,
        description="副本级可选部件（组件 310）：这一件能换成什么，按 socket_index 与 sockets 对齐",
    )
    notes: list[str] = Field(
        default_factory=list, description="这把武器的数据说明（哪些账号字段没读到、为什么为空）"
    )


class WeaponDetailResponse(BaseModel):
    """Response containing multiple weapon details."""

    weapon_type_query: str = Field(description="What type was searched for")
    weapons: list[WeaponDetail] = Field(default_factory=list)
    total_weapons: int = Field(default=0, description="How many weapons of this type the manifest knows")
    returned_weapons: int = Field(default=0, description="How many are included in this response")
    truncated: bool = Field(default=False, description="True when weapons were cut by limit")
