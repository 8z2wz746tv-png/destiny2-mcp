"""装备编排：只读预检 + 生成「先搬/先顶下/再装」的步骤链。

为什么要有这个模块（EQUIP_FLOW_PLAN 第 1～3 节）：我们只暴露过 move/equip 两个原语，
于是「把星火协议穿上」这种正常需求要用户和 Agent 自己摸 10 轮 —— 先撞 `EquipItem`
只收本角色实例的 404，再撞「全身只能一件金装」的 500，最后自己发明了「先穿一件非异域
顶下金装」。这些判断全是**只读**的，没有任何理由让调用方去试错。

分工：
- 这里只有「读 profile 字典 → 事实与步骤」的纯逻辑（`plan_equip`），
  以及一次 profile 读取（`plan_for_player`）。**不写账号**、不 import bungie/服务层；
- 真机写入顺序（含回滚与回读）留给 `services/transfer_service.py`，它复用既有的
  `transfer_item`/`equip_item` 与 `write_readback`；
- 中文话术与信封由 `tools/_equip_branches.py` 负责。

预检的四件事（对应计划文档第 3.1 节）：
1. 实例在不在目标角色身上；不在就说明要搬，并给出「背包有位」还是「背包满 + 数字」；
2. 目标异域与该角色另一个槽的异域冲突 → 从**该角色背包**挑一件非异域顶下；
3. 角色背包满（`capacity == used`）→ 给数字，不写计划；
4. 要搬的实例正装备着 → 上游禁 move 已装备物品，预检直接说清，不让调用方等 500。

挑中间件的口径（文档第 3 节末）：只从该角色背包挑非异域同部位，按光等降序、实例 ID 兜底，
挑不到就如实说「没有可用的非异域 X」并指路 `intent="get", armor_slot=…`。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol

from ..models import EquipPlan, EquipPlanBlock, EquipPlanStep, InventoryItem
from ..vocabulary import LOCATION_LABELS_ZH  # 位置中文标签的单一出处
from . import profile_components
from .item_parser import parse_items_from_profile

# 装备部位 → 背包 bucket（无符号 32 位，profile 的 buckets 组件用的就是这个）。
# 与 item_parser._ARMOR_BUCKETS 是同一批数字：那边按 bucket 判"是不是护甲"，
# 这边按部位取容量；两处都写死过一次，改动时一起看。
_ARMOR_BUCKET_BY_SLOT: dict[str, int] = {
    "helmet": 3448274439,
    "gauntlets": 3551918588,
    "chest": 14239492,
    "legs": 20886954,
    "class_item": 1585787867,
}

_TIER_TYPE_EXOTIC = 6


def _where(location: str) -> str:
    """位置 → 中文（`equipped` 不在位置词表里，单独说）。"""
    if location == "equipped":
        return "正装备"
    return LOCATION_LABELS_ZH.get(location, location)


class EquipItemInfo(Protocol):
    """本模块只跟 Manifest 要两件事：物品定义（稀有度）与物品名。"""

    def get_item_definition(self, item_hash: int) -> dict | None: ...

    def get_item_name(self, item_hash: int) -> str: ...


@dataclass(frozen=True)
class BucketCapacity:
    """某个背包类目的容量事实，全部来自 profile 的 buckets 组件（不猜、不编）。"""

    capacity: int
    used: int
    equipped: int

    @property
    def free(self) -> int:
        """还能再放几件非装备物品。"""
        return self.capacity - self.used


@dataclass
class EquipPlanRequest:
    """一次「把这个实例装到这个角色身上」的全部只读输入。

    `character_inventory` 的语义**说死**：它是"该角色**装备位 + 背包**合并后的物品列表"
    （装备位那几件同样在里面），因为判断异域冲突要看该角色正穿着什么。
    谁是装备着的以 `equipped_keys` 为准，**不要**只看条目的 `is_equipped`：
    真机上 `characterEquipment`/`characterInventories` 的条目没有 `isEquipped` 字段，
    那个字段只出现在组件 300 的 `instances.data[实例]` 上，所以 `is_equipped` 由
    调用方（`load_plan_request`）按解析结果填好；`equipped_keys` 是同一事实的第二道口径，
    用来防"列表里混进来的别的角色的装备"。
    """

    character: str
    character_id: str
    target: InventoryItem
    equipped_keys: frozenset[str] = frozenset()
    character_inventory: list[InventoryItem] = field(default_factory=list)
    bucket_capacity: dict[str, BucketCapacity] = field(default_factory=dict)

    def worn(self) -> list[InventoryItem]:
        """该角色正装备的护甲（以 `equipped_keys` 为准，不猜 `is_equipped`）。"""
        return [
            item
            for item in self.character_inventory
            if item.item_instance_id in self.equipped_keys
        ]

    def carried(self) -> list[InventoryItem]:
        """该角色背包里没装备的那些（顶下金装的中间件只能从这里挑）。"""
        return [
            item
            for item in self.character_inventory
            if item.item_instance_id not in self.equipped_keys
        ]


# 公开别名：装配请求（transfer_service）要按**同一张表**数该类目有几件、容量多少，
# 再抄一份就会漂。
ARMOR_BUCKET_BY_SLOT: dict[str, int] = _ARMOR_BUCKET_BY_SLOT


def _is_exotic(manifest: EquipItemInfo, item_hash: int) -> bool | None:
    """稀有度：6 = 异域。**判断不了给 None**，不把「没查到」说成「不是异域」。

    只在完整定义里判：`tierType` 的单一出处就是 Manifest 的物品定义，
    索引摘要（get_item_info）不一定带这个键。
    """
    definition = manifest.get_item_definition(item_hash)
    if not isinstance(definition, dict):
        return None
    inventory = definition.get("inventory")
    if not isinstance(inventory, dict) or "tierType" not in inventory:
        return None
    return int(inventory.get("tierType") or 0) == _TIER_TYPE_EXOTIC


def _locations(item: InventoryItem, character_id: str) -> list[str]:
    """这个实例**已经按目标角色**在哪：正装备 → 背包 → 仓库。

    位置判据用 `is_equipped` / `location`，不再查一遍 manifest —— 同一个事实只读一次。
    别的角色身上正装备的那件**不算"已经装好"**：那是要搬的状态（上游禁 move 已装备物品），
    所以 `equipped` 只在实例属于目标角色时才成立。
    """
    if item.location == "vault":
        return ["vault"]
    equipped_here = item.is_equipped and (
        not item.character_id or item.character_id == character_id
    )
    if equipped_here:
        return ["equipped"]
    return [item.location, "vault"]


def _block(
    reason: str,
    detail: str,
    *,
    slot: str = "",
    numbers: dict[str, int] | None = None,
) -> EquipPlanBlock:
    return EquipPlanBlock(
        reason=reason,  # type: ignore[arg-type]  # 取值受 EquipBlockReason 约束
        detail=detail,
        slot=slot,
        numbers=numbers or {},
    )


def _downgrade_candidates(
    request: EquipPlanRequest, manifest: EquipItemInfo, slot: str
) -> list[InventoryItem]:
    """背包里能顶下金装的非异域同部位护甲，光等降序（挑不到返回空列表）。

    只从 `carried()`（背包，不含装备位）里挑：仓库里的要先搬，会撞"背包满"。
    """
    candidates = [
        item
        for item in request.carried()
        if item.slot == slot and item.item_instance_id != request.target.item_instance_id
    ]
    legendary = [
        item for item in candidates if _is_exotic(manifest, item.item_hash) is False
    ]
    return sorted(legendary, key=lambda item: (-(item.power or 0), item.item_instance_id))


def _plan_sentences(steps: list[EquipPlanStep]) -> list[str]:
    """步骤 → 可直接展示的中文句子（与 steps 一一对应）。"""
    sentences: list[str] = []
    for index, step in enumerate(steps, 1):
        if step.action == "move":
            body = (
                f"把「{step.item}」从{_where(step.from_location)}"
                f"搬到{_where(step.to_location)}"
            )
        elif step.action == "downgrade":
            body = (
                f"把「{step.item}」（{_where(step.source_location)}）穿上"
                f" → 顶下「{step.replaces}」"
            )
        elif step.action == "equip":
            body = f"把「{step.item}」穿上"
        else:  # 动作字面量只有上面三种；真出现别的说明计划造错了，别静默吞掉
            raise ValueError(f"未知的计划动作：{step.action!r}")
        sentences.append(f"步骤{index}：{body}")
    return sentences


def plan_equip(request: EquipPlanRequest, manifest: EquipItemInfo) -> EquipPlan:
    """只读预检 + 出计划。**不写账号、不改 request。**

    返回的 `status` 只有三种：`already_equipped`（无事可做）、`blocked`（上游铁律挡住，
    `blockers` 给出数字与出路）、`ready`（`steps` 可执行，顺序是「先搬/先顶下、再装目标」）。
    """
    target = request.target
    plan = EquipPlan(
        status="ready",
        character=request.character,
        character_id=request.character_id,
        target_item=target.name,
        target_item_instance_id=target.item_instance_id,
        target_slot=target.slot,
    )

    if "equipped" in _locations(target, request.character_id):
        plan.status = "already_equipped"
        plan.message = f"「{target.name}」已经装备在 {request.character} 身上，无需操作。"
        return plan

    # ── 预检 4：要搬的实例正装备着（上游禁 move 已装备物品，别等 500） ──
    blockers: list[EquipPlanBlock] = []
    if target.is_equipped:
        where = target.location
        blockers.append(_block(
            "item_equipped",
            f"「{target.name}」正装备在{_where(where)}身上，"
            f"上游不允许移动已装备的物品：先在{_where(where)}上换一件同部位的别的装备把它换下来，"
            f"或直接 equip 到{_where(where)}（如果它本来就该穿在那个角色上）。",
            slot=target.slot,
        ))

    needs_move = target.location != request.character
    capacity = request.bucket_capacity.get(target.slot) if target.slot else None
    if needs_move and capacity is not None and capacity.free <= 0:
        # ── 预检 3：目标角色该类目背包满（给数字，别只说"空间不足"） ──
        blockers.append(_block(
            "inventory_full",
            f"{request.character} 的该类目背包已满（{capacity.used}/{capacity.capacity}），"
            f"搬不进「{target.name}」。出路：先腾出位置（分解/转移到仓库），"
            f'或直接装备该角色背包里已有的一件（intent="get", armor_slot="{target.slot}"）。',
            slot=target.slot,
            numbers={
                "used": capacity.used,
                "capacity": capacity.capacity,
                "free": capacity.free,
            },
        ))

    # ── 预检 2：目标异域 vs 该角色另一个槽的异域 ──
    downgrade: EquipPlanStep | None = None
    conflict_item: InventoryItem | None = None
    if _is_exotic(manifest, target.item_hash) is True:
        conflicts = [
            item
            for item in request.worn()
            if item.slot != target.slot
            and item.slot
            and _is_exotic(manifest, item.item_hash) is True
        ]
        if conflicts:
            conflict_item = conflicts[0]
            candidates = _downgrade_candidates(request, manifest, conflict_item.slot)
            if candidates:
                chosen = candidates[0]
                downgrade = EquipPlanStep(
                    action="downgrade",
                    item=chosen.name,
                    item_instance_id=chosen.item_instance_id,
                    from_location=request.character,
                    to_location="equipped",
                    replaces=conflict_item.name,
                    why=(
                        f"全身只能装备一件异域：先穿一件非异域的"
                        f"「{chosen.name}」把「{conflict_item.name}」顶下来，"
                        f"才能装上异域的「{target.name}」"
                        f"（顶下的那件回到背包，不分解、不移动）。"
                    ),
                    success=True,
                )
            else:
                blockers.append(_block(
                    "exotic_conflict",
                    f"「{target.name}」是异域，而 {request.character} 正装备着"
                    f"「{conflict_item.name}」（异域 {conflict_item.slot_display or conflict_item.slot}）；"
                    f"全身只能一件异域，需要先换一件非异域的"
                    f"{conflict_item.slot_display or conflict_item.slot}顶下它，"
                    f"但该角色背包里没有可用的非异域"
                    f"{conflict_item.slot_display or conflict_item.slot}。"
                    f'出路：intent="get", location="{request.character}", '
                    f'armor_slot="{conflict_item.slot}" 看有哪些，'
                    f"或用 move 从仓库搬一件非异域的同部位装备过来。",
                    slot=conflict_item.slot,
                ))

    if blockers:
        plan.status = "blocked"
        plan.blockers = blockers
        plan.message = "；".join(block.detail for block in blockers)
        return plan

    # ── 出计划：先搬 / 先顶下 / 再装目标，顺序不可换 ──
    if needs_move:
        plan.steps.append(EquipPlanStep(
            action="move",
            item=target.name,
            item_instance_id=target.item_instance_id,
            from_location=target.location,
            to_location=request.character,
            why=(
                f"上游的装备接口只接受在该角色身上的实例："
                f"「{target.name}」现在在 {target.location}，要先搬到 {request.character}。"
            ),
            success=True,
        ))
    if downgrade is not None:
        plan.steps.append(downgrade)
    plan.steps.append(EquipPlanStep(
        action="equip",
        item=target.name,
        item_instance_id=target.item_instance_id,
        from_location=request.character,
        to_location="equipped",
        why=f"目标：把「{target.name}」装备到 {request.character} 身上。",
        success=True,
    ))

    plan.message = "计划：" + "；".join(_plan_sentences(plan.steps)) + "。"
    return plan


async def plan_for_player(
    player_name: str,
    item_instance_id: str,
    character: str,
    *,
    manifest: EquipItemInfo,
    resolver: Any,
) -> EquipPlan:
    """读一次 profile，然后出计划。这里就是全部的 IO（**只读**）。

    异常照旧往上抛（`ItemNotFoundError` 等），由工具层统一转成错误信封。
    """
    return plan_equip(
        await load_plan_request(
            player_name,
            item_instance_id,
            character,
            manifest=manifest,
            resolver=resolver,
        ),
        manifest,
    )


async def load_plan_request(
    player_name: str,
    item_instance_id: str,
    character: str,
    *,
    manifest: EquipItemInfo,
    resolver: Any,
) -> EquipPlanRequest:
    """把一次 profile 读取整理成预检输入（`plan_equip` 的入参）。

    与 `plan_equip` 分开是为了让单测能逐个断言"读到的事实"，
    而不用先跑一遍计划再反推输入对不对。
    """
    p = await resolver.resolve_player(player_name)
    mid, mtype = p["membership_id"], p["membership_type"]
    char_id = await resolver.resolve_character_id(mid, mtype, character)
    profile = await resolver.get_profile(mid, mtype, profile_components.INVENTORY)

    items = parse_items_from_profile(profile, manifest)
    target = next(
        (item for item in items if item.item_instance_id == item_instance_id), None
    )
    if target is None:
        from ..exceptions import ItemNotFoundError

        raise ItemNotFoundError(item_instance_id, "It may have been moved or dismantled.")
    # 装备位 + 背包一起给预检：判断"该角色正穿着哪个异域"必须看得见装备位那几件。
    # 谁是装备着的由 equipped_keys 说，别指望条目上的 is_equipped（真机的
    # characterEquipment 条目没有这个字段，它只在组件 300 的 instances.data 里）。
    on_character = [item for item in items if item.character_id == char_id]
    return EquipPlanRequest(
        character=character,
        character_id=char_id,
        target=target,
        equipped_keys=frozenset(
            item.item_instance_id for item in on_character if item.is_equipped
        ),
        character_inventory=on_character,
        bucket_capacity=_bucket_capacity(profile, char_id, items),
    )


def _bucket_capacity(
    profile: dict, char_id: str, items: list[InventoryItem]
) -> dict[str, BucketCapacity]:
    """护甲类目容量：容量取 buckets 组件，已用/已装备按本次 profile 实点。

    组件里缺某个 bucket（老账号或上游裁剪）就先不给这一类目下结论 ——
    `plan_equip` 会跳过"背包满"这条预检，让上游去判，而不是拿默认值编一个满/不满。
    """
    buckets = (
        profile.get("itemComponents", {})
        .get("buckets", {})
        .get("data", {})
    )
    equipped_counts: dict[str, int] = {}
    for item in items:
        if item.character_id == char_id and item.is_equipped and item.slot:
            equipped_counts[item.slot] = equipped_counts.get(item.slot, 0) + 1

    capacity: dict[str, BucketCapacity] = {}
    for slot, bucket_hash in _ARMOR_BUCKET_BY_SLOT.items():
        entry = buckets.get(bucket_hash) or buckets.get(str(bucket_hash))
        if not isinstance(entry, dict) or "capacity" not in entry:
            continue
        capacity[slot] = BucketCapacity(
            capacity=int(entry.get("capacity") or 0),
            used=int(entry.get("count") or entry.get("usage") or 0),
            equipped=equipped_counts.get(slot, 0),
        )
    return capacity
