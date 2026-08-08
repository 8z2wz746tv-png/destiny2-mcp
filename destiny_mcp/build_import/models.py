"""Build Import Agent 数据模型。

BuildDraft: LLM 输出结构（全 Name）
CanonicalBuild: 系统内部标准表示（全 Hash）

参见 ADR-007: Canonical Build Strategy。
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from ..models import LoadoutItem


class BuildDraft(BaseModel):
    """LLM 输出结构。全部是人类可读名称，不允许 Hash。

    LLM 从截图/文章/视频中提取配装信息，只输出名称。
    名称→Hash 的转换由 Normalizer 负责。
    """

    class_name: str | None = Field(
        default=None,
        description="职业名：猎人/hunter、术士/warlock、泰坦/titan",
    )
    exotic_name: str | None = Field(
        default=None,
        description="金装名称（如 '天穹夜鹰'、'Celestial Nighthawk'）",
    )
    weapon_names: list[str] = Field(
        default_factory=list,
        description="武器名称列表（最多 3 把）",
    )
    super_name: str | None = Field(
        default=None,
        description="超能名称（如 '金枪'、'Gunslinger'）",
    )
    grenade_name: str | None = Field(
        default=None,
        description="手雷名称",
    )
    melee_name: str | None = Field(
        default=None,
        description="近战技能名称",
    )
    class_ability_name: str | None = Field(
        default=None,
        description="职业技能名称（如 '闪避'、'Rift'）",
    )
    movement_name: str | None = Field(
        default=None,
        description="移动技能名称（如 '三段跳'、'Blink'）",
    )
    aspect_names: list[str] = Field(
        default_factory=list,
        description="星象名称列表",
    )
    fragment_names: list[str] = Field(
        default_factory=list,
        description="碎片名称列表",
    )
    target_stats: dict[str, int] = Field(
        default_factory=dict,
        description="属性目标（Renegades 属性名：weapons/health/class_stat/grenade/melee/super_stat）",
    )
    extraction_notes: list[str] = Field(
        default_factory=list,
        description="LLM 的备注（调试用，如 '截图模糊，金装可能是天穹夜鹰'）",
    )


class CanonicalBuild(BaseModel):
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
    subclass_instance_id: str = Field(
        default="",
        description="确认时已装备的子职业实例 ID",
    )
    subclass_plug_sockets: dict[int, int] = Field(
        default_factory=dict,
        description="准确的子职业插槽索引到 Plug Hash",
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
