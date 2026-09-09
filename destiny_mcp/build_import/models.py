"""Build Import Agent 数据模型。

BuildDraft: LLM 输出结构（全 Name）
CanonicalBuild: 系统内部标准表示（全 Hash）

参见 ADR-007: Canonical Build Strategy。
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from ..build_contracts import CanonicalBuild as CanonicalBuild


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
