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
from ..build.constants import ARMOR_SLOT_MAP
from . import profile_components
from .armor_payload import equip_slot_of, slot_key_from_solver
from .item_parser import parse_items_from_profile

# 装备部位 → 背包 bucket（无符号 32 位，profile 的 buckets 组件用的就是这个）。
# 与 item_parser._ARMOR_BUCKETS 是同一批数字：那边按 bucket 判"是不是护甲"，
# 这边按部位取容量；两处都写死过一次，改动时一起看。
# 桶→槽位：从**单一出处**派生（`build/constants.ARMOR_SLOT_MAP` 是正主，认 signed/unsigned；
# 它的槽名是复数 "chests"，这里用 slot_key_from_solver 归一成 "chest"）。
# 以前这里是手抄的一份，和 loadout_service 那份、item_parser 用的那份是同一事实的三份拷贝。
ARMOR_BUCKET_BY_SLOT: dict[str, int] = {
    slot_key_from_solver(slot): bucket_hash
    for bucket_hash, slot in ARMOR_SLOT_MAP.items()
    if bucket_hash > 0
}

_WEAPON_SLOTS = frozenset({"kinetic", "energy", "power"})


def _catalog_hint(slot_key: str) -> str:
    """给调用方的"这个部位有哪些东西"提示：武器与护甲的过滤参数**不是一个**。"""
    if slot_key in _WEAPON_SLOTS:
        return 'intent="get", item_type="武器"'
    return f'intent="get", armor_slot="{slot_key}"'


def item_traits(manifest: EquipItemInfo, item: InventoryItem) -> tuple[str, str, str | None]:
    """(装备槽键, 装备槽中文, 互斥组)。

    槽位与互斥组都取物品定义里的 `equippingBlock` —— 这是游戏自己的说法，武器和护甲一视同仁
    （ADR-011）。互斥组 `None` 表示**判不了**（定义查不到 / 没有这个字段），此时不下结论；
    槽位查不到就退回按 bucket 认出来的护甲槽（老路径），仍认不出给空串。

    **这是"实例 → 槽位"的唯一口径**：`transfer_service` 的回滚/回读也用它，
    否则执行层按护甲槽找、规划层按装备槽出步骤，武器那一步就会在回滚里凭空消失。
    """
    definition = manifest.get_item_definition(item.item_hash)
    slot_key, slot_display = equip_slot_of(definition)
    if not slot_key:
        slot_key, slot_display = item.slot, item.slot_display
    block = (definition or {}).get("equippingBlock")
    if not isinstance(block, dict) or "uniqueLabel" not in block:
        return slot_key, slot_display, None
    return slot_key, slot_display, str(block.get("uniqueLabel") or "")


def _where(location: str) -> str:
    """位置 → 中文（`equipped` 不在位置词表里，单独说）。"""
    if location == "equipped":
        return "正装备"
    return LOCATION_LABELS_ZH.get(location, location)


class EquipItemInfo(Protocol):
    """本模块只跟 Manifest 要两件事：物品定义（稀有度）与物品名。"""

    def get_item_definition(self, item_hash: int) -> dict | None: ...

    def get_item_name(self, item_hash: int) -> str: ...

    def get_bucket_definition(self, bucket_hash: int) -> dict | None: ...


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
ARMOR_BUCKET_BY_SLOT: dict[str, int] = ARMOR_BUCKET_BY_SLOT


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
    request: EquipPlanRequest, manifest: EquipItemInfo, slot_key: str
) -> list[InventoryItem]:
    """背包里能顶下同类异域的同部位**非异域**装备，光等降序（挑不到返回空列表）。

    只从 `carried()`（背包，不含装备位）里挑：仓库里的要先搬，会撞"背包满"。
    "是不是异域"同样按 Manifest 的 `uniqueLabel` 判：空串 = 非异域；**判不了（None）不当候选** ——
    没有证据就不拿它当中间件。槽位键为空（认不出部位）时直接返回空：那意味着"同部位"这个
    判据本身不成立，不能拿所有认不出部位的装备来凑。
    """
    if not slot_key:
        return []
    plain: list[InventoryItem] = []
    for item in request.carried():
        if item.item_instance_id == request.target.item_instance_id:
            continue
        item_key, _display, label = item_traits(manifest, item)
        if item_key == slot_key and label == "":
            plain.append(item)
    return sorted(plain, key=lambda item: (-(item.power or 0), item.item_instance_id))


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
    # 槽位与互斥组都来自 Manifest（ADR-011）：武器和护甲同一套槽位键。
    target_slot, target_slot_display, target_label = item_traits(manifest, target)
    plan = EquipPlan(
        status="ready",
        character=request.character,
        character_id=request.character_id,
        target_item=target.name,
        target_item_instance_id=target.item_instance_id,
        target_slot=target_slot,
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
            slot=target_slot,
        ))

    needs_move = target.location != request.character
    capacity = request.bucket_capacity.get(target_slot) if target_slot else None
    if needs_move and capacity is not None and capacity.free <= 0:
        # ── 预检 3：目标角色该类目背包满（给数字，别只说"空间不足"） ──
        blockers.append(_block(
            "inventory_full",
            f"{request.character} 的该类目背包已满（{capacity.used}/{capacity.capacity}），"
            f"搬不进「{target.name}」。出路：先腾出位置（分解/转移到仓库），"
            f"或直接装备该角色背包里已有的一件（{_catalog_hint(target_slot)}）。",
            slot=target_slot,
            numbers={
                "used": capacity.used,
                "capacity": capacity.capacity,
                "free": capacity.free,
            },
        ))

    # ── 预检 2：目标异域 vs 该角色**另一个槽**的**同类**异域（ADR-011）──
    # 判据直接用 Manifest 的 `uniqueLabel`：互斥组相同、槽位不同才算冲突。同槽位是"装上去把它
    # 替换掉"，不是冲突；异域武器与异域护甲分属两个互斥组，因此**互不冲突** ——
    # 以前这里写的是"另一槽的异域就算冲突"（话术还说成"全身只能装备一件异域"），
    # 于是"装异域武器 + 正穿异域护甲"会被误判，而真冲突（两把异域武器）因为武器没有 slot
    # 反而全被跳过。
    downgrade: EquipPlanStep | None = None
    if target_label:
        conflict_item: InventoryItem | None = None
        conflict_slot = ""
        conflict_slot_display = ""
        for worn in request.worn():
            worn_slot, worn_display, worn_label = item_traits(manifest, worn)
            if worn_label != target_label:
                continue
            # 同槽位（且槽位认得出）说明装上目标就会把它替换掉，不算冲突。
            if target_slot and worn_slot == target_slot:
                continue
            conflict_item, conflict_slot, conflict_slot_display = worn, worn_slot, worn_display
            break
        if conflict_item is not None:
            candidates = _downgrade_candidates(request, manifest, conflict_slot)
            if candidates:
                chosen = candidates[0]
                downgrade = EquipPlanStep(
                    action="downgrade",
                    item=chosen.name,
                    item_instance_id=chosen.item_instance_id,
                    slot=conflict_slot,
                    from_location=request.character,
                    to_location="equipped",
                    replaces=conflict_item.name,
                    why=(
                        f"同类的异域只能装备一件：先穿一件非异域的"
                        f"「{chosen.name}」把「{conflict_item.name}」顶下来，"
                        f"才能装上异域的「{target.name}」"
                        f"（顶下的那件回到背包，不分解、不移动）。"
                    ),
                    success=True,
                )
            else:
                where = conflict_slot_display or conflict_slot or "同部位"
                blockers.append(_block(
                    "exotic_conflict",
                    f"「{target.name}」与 {request.character} 正装备的「{conflict_item.name}」"
                    f"属于同一类异域（{where}）；同类异域只能装备一件，"
                    f"需要先换一件非异域的{where}顶下它，"
                    f"但该角色背包里没有可用的非异域{where}。"
                    f"出路：{_catalog_hint(conflict_slot)} 看有哪些，"
                    f"或用 move 从仓库搬一件同部位的非异域装备过来。",
                    slot=conflict_slot,
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
            slot=target_slot,
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
        slot=target_slot,
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
    profile = await resolver.get_profile(mid, mtype, profile_components.ARMOR_SNAPSHOT)

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
        bucket_capacity=_bucket_capacity(manifest, char_id, items),
    )


def _bucket_capacity(
    manifest: EquipItemInfo, char_id: str, items: list[InventoryItem]
) -> dict[str, BucketCapacity]:
    """护甲类目容量：容量取 **Manifest 的桶定义**（`DestinyInventoryBucketDefinition.itemCount`），
    已用/已装备按本次 profile 实点。

    旧实现读 `itemComponents.buckets.data` —— 真机上**根本没有这个组件**（实采：读出来是空的，
    于是"背包满"这条预检静默失效，等于没有）。容量只有一个权威来源：桶定义。
    `used` **含正装备那件**（与 DIM 同口径）：真机上 warlock 的头盔/臂铠/胸甲都是 10/10，
    少算一件就会假报"还能放一件"，然后去撞上游 `NoRoomInDestination`。
    查不到容量（桶定义缺失）就不给这一类目下结论，让上游去判，别编一个满/不满。
    """
    totals: dict[str, int] = {}
    worn: dict[str, int] = {}
    for item in items:
        if item.character_id != char_id or not item.slot:
            continue
        totals[item.slot] = totals.get(item.slot, 0) + 1
        if item.is_equipped:
            worn[item.slot] = worn.get(item.slot, 0) + 1

    capacity: dict[str, BucketCapacity] = {}
    for slot, bucket_hash in ARMOR_BUCKET_BY_SLOT.items():
        definition = manifest.get_bucket_definition(bucket_hash) or {}
        total = int(definition.get("itemCount") or 0)
        if not total:
            continue
        capacity[slot] = BucketCapacity(
            capacity=total,
            used=totals.get(slot, 0),
            equipped=worn.get(slot, 0),
        )
    return capacity
