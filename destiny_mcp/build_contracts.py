"""Shared build contracts, independent of import adapters."""

from __future__ import annotations

from typing import Literal, Self

from pydantic import BaseModel, Field, ValidationError, model_validator

from .models import LoadoutItem


class BuildRecipe(BaseModel):
    """系统内部标准配装表示。全部使用 Hash。

    所有名称已通过 Normalizer 归一化为 Bungie Definition Hash。
    参见 ADR-001: Canonical Build Strategy。
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
            raise ValueError("需要 5 个互不相同的装备实例 ID。")
        if {item.slot for item in self.items} != {
            "helmet", "gauntlets", "chest", "legs", "class_item",
        }:
            raise ValueError("五个护甲部位（头盔/手套/胸甲/腿甲/职业物品）各要一件。")
        if any(mod <= 0 for item in self.items for mod in item.mods):
            raise ValueError("模组 Hash 必须是正数。")
        return self


# 字段名 → 调用方能看懂的说法。报错是给"模型/Agent"看的，得说清缺什么、下一步干什么。
_CANONICAL_FIELD_LABELS = {
    "class_type": "职业 class_type",
    "items": "装备项 items",
    "snapshot_version": "库存快照版本 snapshot_version",
    "execution_id": "服务端候选 ID execution_id",
    "subclass_plug_sockets": "子职业插槽 subclass_plug_sockets",
}


def canonical_build_error_message(exc: ValidationError) -> str:
    """把 ExecutableBuild 的校验失败说成人话。

    以前这里直接把 pydantic 的 `str(exc)` 甩给调用方：一段英文，
    带 `type=missing`、`input_value=...` 和 errors.pydantic.dev 链接。
    对人是噪音，对模型是误导——它看不出"该先求解再原样回传"。
    """
    problems: list[str] = []
    for error in exc.errors()[:6]:
        loc = [str(part) for part in (error.get("loc") or ())]
        field = loc[0] if loc else ""
        label = _CANONICAL_FIELD_LABELS.get(field, field or "整体结构")
        kind = str(error.get("type") or "")
        if kind == "missing":
            problems.append(f"{label} 缺失")
        elif kind == "literal_error":
            problems.append(f"{label} 取值不在允许范围内")
        elif kind == "extra_forbidden":
            problems.append(f"{label} 不是候选里的字段")
        elif kind in {"too_short", "too_long"}:
            problems.append(f"{label} 数量不对")
        else:
            original = (error.get("ctx") or {}).get("error")
            text = str(original) if isinstance(original, BaseException) else str(error.get("msg") or "")
            problems.append(f"{label}：{text.strip()}" if text else label)
    detail = "；".join(problems) if problems else "结构不符合候选配装的形状"
    return (
        f"这不是服务端签发的候选配装（{detail}）。"
        "请先用 recommend / analyze / farm_target 生成候选，把返回里的 canonical_build "
        "整块原样传回来；不要自己拼 hash 或实例 ID，也不能只传 score。"
    )
