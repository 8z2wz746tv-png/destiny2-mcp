"""目标格满时**自动腾一件**（判据照 DIM v8.143.0；决定见 ADR-029）。

上游 `TransferItem` 在目标格满时回 500 `DestinyNoRoomInDestination`。以前我们只会把问题丢回给
用户（"先在游戏里腾出一格"）—— 2026-10-06 真机连着撞了两次。这里做的是 DIM 的那件事：
**挑一件最该走的，搬进仓库**。

判据的**唯一出处**就是本模块（调用方只提供候选与搬迁动作，不许各自再写一套）：

- 绝不腾（任一命中即跳过）：**穿着的**、**锁定的**（比 DIM 保守 —— DIM 那段没按锁过滤）、
  **本次要装的**（reserved）、**官方配装槽正引用的**（official）；
- 排序（越靠前越先腾）：稀有度低 → 光等低 → 非大师化 → 非巧匠 → 实例 ID（稳定）。
  DIM 里还有"标签顺序（归档/灌注/垃圾/最爱）"，我们没有标签系统，见 ADR-029 的 Consequences。
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Collection, Sequence
from dataclasses import dataclass, field

from ..build.constants import SOLVER_SLOTS, SOLVER_SLOT_TO_LOADOUT
from ..build.execution_feasibility import VAULT_LOCATION, read_facts
from ..build.models import Armor
from ..manifest import resolve_character_name

#: 单次最多腾几件（ADR-029 §4）。超过就如实拒绝，不无限搬。
DEFAULT_LIMIT = 3

#: 光等未知时排序用的大数：**不知道就别先动它**（缺值当"最不该腾"）。
_UNKNOWN_POWER = 10**9


@dataclass
class MakeRoomResult:
    """腾格结果：腾走了哪几件、每一步的说明、以及没腾成的原因。"""

    moved: list[Armor] = field(default_factory=list)
    steps: list[str] = field(default_factory=list)
    blocked_reason: str = ""

    @property
    def moved_names(self) -> str:
        return "、".join(a.name for a in self.moved)


def is_movable(
    armor: Armor,
    *,
    reserved_instance_ids: Collection[str] = (),
    official_instance_ids: Collection[str] = (),
) -> bool:
    """这一件能不能腾（"绝不腾"四条 + 一条"腾了也没用"；同部位由调用方在候选里保证）。

    ⚠️ **在仓库里的件不算候选**：快照里同一个槽位既有角色身上的、也有仓库里的（真机上臂铠格
    57 件），把仓库件"搬进仓库"什么都不会发生 —— 格子还是满的、复检照样拒（2026-10-06 真机
    就是这么翻的车：单测绿、真机没腾成）。
    """
    if armor.source_location == VAULT_LOCATION:
        return False
    if armor.is_equipped:  # DIM 第一条："Try our hardest never to unequip something"
        return False
    if armor.is_locked:
        return False
    if armor.item_instance_id in reserved_instance_ids:
        return False
    return armor.item_instance_id not in official_instance_ids


def sort_key(armor: Armor) -> tuple:
    """排序键：**越小越先腾**。字段读法与理由写在 ADR-029。"""
    return (
        armor.tier or 0,  # 5=传说 6=异域：先腾低品阶
        armor.power if armor.power is not None else _UNKNOWN_POWER,  # 光等低先腾；未知最后
        bool(armor.is_masterworked),  # 大师化留着
        bool(armor.is_artifice),  # 巧匠留着
        armor.item_instance_id,  # 同分时稳定（不许依赖输入顺序）
    )


def pick_move_aside(
    candidates: Sequence[Armor],
    *,
    reserved_instance_ids: Collection[str] = (),
    official_instance_ids: Collection[str] = (),
    limit: int = DEFAULT_LIMIT,
) -> list[Armor]:
    """从`candidates`里挑要腾的几件（顺序即"最该腾的在前"）。"""
    movable = [
        armor
        for armor in candidates
        if is_movable(
            armor,
            reserved_instance_ids=reserved_instance_ids,
            official_instance_ids=official_instance_ids,
        )
    ]
    movable.sort(key=sort_key)
    return movable[: max(0, limit)]


async def make_room(
    *,
    candidates: Sequence[Armor],
    move_to_vault: Callable[[Armor], Awaitable[None]],
    reserved_instance_ids: Collection[str] = (),
    official_instance_ids: Collection[str] = (),
    limit: int = DEFAULT_LIMIT,
) -> MakeRoomResult:
    """挑一件（或几件）搬进仓库。

    `move_to_vault` 由调用方注入（它才知道自己那条链路的搬运入口），**抛异常即视为没搬成** ——
    这里不吞异常，也不继续搬下一件（半途搬动最麻烦）。
    """
    result = MakeRoomResult()
    picked = pick_move_aside(
        candidates,
        reserved_instance_ids=reserved_instance_ids,
        official_instance_ids=official_instance_ids,
        limit=limit,
    )
    if not picked:
        result.blocked_reason = (
            "这个格子里没有可腾的件（穿着的、锁定的、本次要装的、以及官方配装槽在用的都不动）"
        )
        return result
    for armor in picked:
        await move_to_vault(armor)
        result.moved.append(armor)
        result.steps.append(f"腾格：'{armor.name}' → 仓库（{armor.slot}）")
    return result


async def make_room_for_build(
    *,
    player_name: str,
    character: str,
    inventory,
    equipment,
    manifest,
    build,
    official_instance_ids: Collection[str] = (),
    limit: int = DEFAULT_LIMIT,
) -> tuple[list[str], str]:
    """一次 `equip_build` 的腾格入口：读现场 → 该腾就腾 → 返回 `(回执行, 话术前缀)`。

    整个编排放在这里（`build_service` 贴着行数上限）；调用方只负责把结果拼进回执。
    """
    snapshot = await inventory.get_armor_snapshot(player_name, character)
    steps, moved = await _make_room_for_build(
        snapshot=snapshot,
        target_class_type=resolve_character_name(build.class_type),
        manifest=manifest,
        plan_items=build.items,
        move_to_vault=lambda armor: equipment.move_single_to_vault(player_name, armor),
        official_instance_ids=official_instance_ids,
        limit=limit,
    )
    prefix = f"已自动腾出{'、'.join(moved)}（搬到仓库）。" if moved else ""
    return steps, prefix


async def _make_room_for_build(
    *,
    snapshot,
    target_class_type: int,
    manifest,
    plan_items,
    move_to_vault: Callable[[Armor], Awaitable[None]],
    official_instance_ids: Collection[str] = (),
    limit: int = DEFAULT_LIMIT,
) -> tuple[list[str], list[str]]:
    """给"这五件能不能落地"腾地方：**只看计划里在仓库、且目标格满的那些部位**。

    返回 `(steps, 腾走的件名)`。"格满"的判据**复用** `execution_feasibility.read_facts`
    （ADR-029 §7：不另写一套）；认不出目标角色、或容量读不到时**不下结论**（与那边同一条纪律）。
    """
    facts = read_facts(snapshot, target_class_type, manifest)
    if not facts.character:
        return [], []
    # 计划里的部位名是**单数**（`helmet`，`build_service` 就是按它校验的），快照与现场是**复数**
    # （`helmets`）。换算用 `SOLVER_SLOT_TO_LOADOUT`（那边的注释写着"换算只此一处"），别另写一张表。
    loadout_to_solver = {SOLVER_SLOT_TO_LOADOUT[solver]: solver for solver in SOLVER_SLOTS}
    by_id = {
        armor.item_instance_id: armor
        for slot in SOLVER_SLOTS
        for armor in snapshot.get_slot(slot)
    }
    plan_ids = {item.item_instance_id for item in plan_items}
    steps: list[str] = []
    moved_names: list[str] = []
    for item in plan_items:
        armor = by_id.get(item.item_instance_id)
        if armor is None or armor.source_location != "vault":
            continue  # 本来就在角色身上的件不撞 NoRoomInDestination
        solver_slot = loadout_to_solver.get(item.slot, item.slot)
        if not facts.blocks_vault_piece(solver_slot):
            continue
        outcome = await make_room(
            candidates=snapshot.get_slot(solver_slot),
            move_to_vault=move_to_vault,
            reserved_instance_ids=plan_ids | {item.item_instance_id},
            official_instance_ids=official_instance_ids,
            limit=limit,
        )
        steps += outcome.steps
        moved_names += [a.name for a in outcome.moved]
    return steps, moved_names


def make_room_steps(details: Sequence[str]) -> list[dict]:
    """把腾格说明转成回执行（`action="make_room"`；复用现有 steps 结构，不新增响应字段）。"""
    from ..models import MoveItemStep

    return [
        MoveItemStep(action="make_room", detail=detail, success=True).model_dump()
        for detail in details
    ]
