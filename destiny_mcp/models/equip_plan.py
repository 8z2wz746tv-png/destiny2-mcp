"""装备编排的响应形状：计划步骤、阻塞原因与整份计划。

为什么单独一个模块：`inventory_assistant(intent="equip")` 与 `intent="move" + equip=true`
现在返回的是**一条链**（可能要先搬、先顶下、再装），既有 `MoveItemResult.steps`
只有 `action/detail/success`，说不出"这一步动的是哪件、从哪到哪"，
而调用方要拿这些字段拼确认话术（EQUIP_FLOW_PLAN 第 3 节）。

规则（与 `MoveItemStep` 一致的地方就不重复造）：
- 步骤的 `action` 只取四个值，别的字符串一律拒收 —— 计划是这个 intent 的对外契约，
  拼错一个 action 不会报错、只会让调用方看不懂 `steps`；
- `success` 在**计划**里表示"这一步可执行"（`blocked` 恒为 False），在执行结果里
  表示"这一步真做成了"；两者的区别由 `status` 说清，不给同一个键两套含义；
- 位置一律用 `vault`/`hunter`/`warlock`/`titan`/`equipped`，中文标签由调用方按
  `vocabulary.LOCATION_LABELS_ZH` 渲染（中文词表的单一出处在那里）。
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

# 计划里允许出现的动作：搬、顶下异域、装目标。
# **回读核对不在计划里**：它是执行后的核对结果，由 `EquipPlan.verified`/`unverified_reason`
# 表达；动作字面量越少越拼不歪。
EquipStepAction = Literal["move", "downgrade", "equip"]

# 计划的四种结局。`blocked` 与 `already_equipped` 都**不带**可执行步骤：
# 前者是上游铁律挡住（预检已给数字与出路），后者是无事可做。
EquipPlanStatus = Literal["ready", "blocked", "already_equipped"]

# 预检抓到的阻塞原因。`item_equipped` 与 `inventory_full` 只写在 `blockers` 上，
# 不单独成为 status —— 它们都是"这次先做不了"，和别的阻塞一样按 blocked 出。
EquipBlockReason = Literal[
    "item_equipped",       # 要 move 的实例正装备在身上（上游禁 move 已装备物品）
    "exotic_conflict",     # 目标异域与另一个槽的异域冲突，且角色背包里挑不到非异域顶下
    "inventory_full",      # 目标角色的该类目背包已满，搬不进去
    "item_missing",        # 账号上找不到这个实例
]


class EquipPlanStep(BaseModel):
    """一步装备编排：动作、动的是哪件、从哪到哪、为什么。"""

    action: EquipStepAction = Field(description="move=搬、downgrade=顶下异域、equip=装目标")
    item: str = Field(default="", description="这一步操作的物品名")
    item_instance_id: str = Field(default="", description="这一步操作的物品实例 ID")
    from_location: str = Field(
        default="", description="来源：vault/hunter/warlock/titan（顶下没有来源，留空）"
    )
    to_location: str = Field(
        default="",
        description="去向：vault/hunter/warlock/titan/equipped；顶下与装备都是 equipped",
    )
    why: str = Field(default="", description="这一步为什么必须存在（中文，可直接展示）")
    slot: str = Field(
        default="", description="这一步动的部位键（helmet/gauntlets/chest/legs/class_item）；装备编排里回滚靠它"
    )
    source_location: str = Field(
        default="", description="被顶下的那件东西原本在哪（顶下步骤才有）"
    )
    replaces: str = Field(default="", description="被这一步顶下来的物品名（没有则为空）")
    success: bool = Field(
        default=True, description="计划里 = 这一步可执行（blocked 恒 false）；回读里 = 这一步真做成了"
    )


class EquipPlanBlock(BaseModel):
    """挡住计划的那件事：原因码 + 中文说明 + 数字/出路。"""

    reason: EquipBlockReason = Field(description="阻塞原因码")
    detail: str = Field(default="", description="中文说明：缺什么、下一步怎么做")
    slot: str = Field(default="", description="相关部位（helmet/gauntlets/chest/legs/class_item）")
    numbers: dict[str, int] = Field(
        default_factory=dict,
        description="能给出数字的事实（如 used/capacity）；给不出就空着，不编 0",
    )
    success: bool = Field(default=False, description="阻塞不是成功；恒 false，便于共用信封判断")


class EquipPlan(BaseModel):
    """一次装备编排的完整结果：计划（未确认）或执行后的回读（已确认）。"""

    status: EquipPlanStatus = Field(description="ready=有步骤要执行；blocked/already_equipped=无事可执行")
    character: str = Field(default="", description="目标角色：hunter/warlock/titan")
    character_id: str = Field(default="", description="目标角色 ID")
    target_item: str = Field(default="", description="要装备的物品名")
    target_item_instance_id: str = Field(default="", description="要装备的物品实例 ID")
    target_slot: str = Field(
        default="", description="部位键：helmet/gauntlets/chest/legs/class_item（非护甲为空）"
    )
    steps: list[EquipPlanStep] = Field(default_factory=list, description="按执行顺序排列的步骤")
    blockers: list[EquipPlanBlock] = Field(
        default_factory=list, description="挡住计划的事实；status=ready 时为空"
    )
    verified: bool | None = Field(
        default=None,
        description="回读核对是否通过；None = 还没执行（不把「没核对」说成 True/False）",
    )
    unverified_reason: str = Field(
        default="",
        description="回读没确认的原因（上游同步窗口）；verified=None 时才可能有值",
    )
    message: str = Field(default="", description="可直接展示的中文说明")
    success: bool = Field(
        default=False, description="执行结果：全部步骤与回读都通过才 True（计划里恒 False）"
    )
