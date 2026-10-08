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

import asyncio
import re
from collections.abc import Awaitable, Callable, Collection, Sequence
from dataclasses import dataclass, field

from ..build.constants import SOLVER_SLOTS, SOLVER_SLOT_TO_LOADOUT
from ..build.execution_feasibility import VAULT_LOCATION, read_facts
from ..build.models import Armor
from ..build.snapshot_version import snapshot_version
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
    candidates=None,
    official_instance_ids: Collection[str] = (),
    limit: int = 1,
    names: set[str] | None = None,
) -> tuple[list[str], str]:
    """一次 `equip_build` 的腾格入口：读现场 → 该腾就腾 → 返回 `(回执行, 话术前缀)`。

    `limit=1` 是**每个卡住的件只腾一件**：一个部位的计划件只有一件，腾一格就够；
    多腾是白搬（2026-10-06 真机第四次翻车：按默认 3 腾了三件，用户白丢三个格子）。

    整个编排放在这里（`build_service` 贴着行数上限）；调用方只负责把结果拼进回执。
    """
    snapshot = await inventory.get_armor_snapshot(player_name, character)
    steps, moved, freed_slots = await _make_room_for_build(
        snapshot=snapshot,
        target_class_type=resolve_character_name(build.class_type),
        manifest=manifest,
        plan_items=build.items,
        move_to_vault=lambda armor: equipment.move_single_to_vault(player_name, armor),
        official_instance_ids=official_instance_ids,
        limit=limit,
        only_names=names,
    )
    if not moved:
        return steps, ""
    # ⚠️ **腾动本身会让候选的指纹过期**（快照含 `source_location`/`is_equipped`）—— 不推进基线，
    # 紧接着的写前复检必然判 `stale_inventory_snapshot`（2026-10-06 真机第二次翻车就是这个）。
    # 与 `build_baseline` 同一套道理：这是**我们自己的写入**，把基线推到当前实况即可；
    # 复检照样会重读现场并逐件核对，安全网没削弱。
    if candidates is not None:
        fresh = await _settled_snapshot(
            inventory, player_name, character, resolve_character_name(build.class_type),
            manifest, freed_slots,
        )
        candidates.register(
            build.model_copy(update={"snapshot_version": snapshot_version(fresh)}), player_name
        )
    return steps, f"已自动腾出{'、'.join(moved)}（搬到仓库）。"


async def _make_room_for_build(
    *,
    snapshot,
    target_class_type: int,
    manifest,
    plan_items,
    move_to_vault: Callable[[Armor], Awaitable[None]],
    official_instance_ids: Collection[str] = (),
    limit: int = DEFAULT_LIMIT,
    only_names: set[str] | None = None,
) -> tuple[list[str], list[str], set[str]]:
    """给"这五件能不能落地"腾地方：**只看计划里在仓库、且目标格满的那些部位**。

    返回 `(steps, 腾走的件名, 腾过的槽位)`。"格满"的判据**复用** `execution_feasibility.read_facts`
    （ADR-029 §7：不另写一套）；认不出目标角色、或容量读不到时**不下结论**（与那边同一条纪律）。
    """
    facts = read_facts(snapshot, target_class_type, manifest)
    if not facts.character:
        return [], [], set()
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
    freed_slots: set[str] = set()
    for item in plan_items:
        armor = by_id.get(item.item_instance_id)
        if armor is None or armor.source_location != "vault":
            continue  # 本来就在角色身上的件不撞 NoRoomInDestination
        solver_slot = loadout_to_solver.get(item.slot, item.slot)
        if only_names is not None:
            # P2：上游**真的**回了格满（回执点名了这件）→ 不再信预判，直接给它腾一格。
            if getattr(item, "name", "") not in only_names:
                continue
        elif not facts.blocks_vault_piece(solver_slot):
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
        if outcome.moved:
            freed_slots.add(solver_slot)
    return steps, moved_names, freed_slots


def make_room_steps(details: Sequence[str]) -> list[dict]:
    """把腾格说明转成回执行（`action="make_room"`；复用现有 steps 结构，不新增响应字段）。"""
    from ..models import MoveItemStep

    return [
        MoveItemStep(action="make_room", detail=detail, success=True).model_dump()
        for detail in details
    ]


#: 读到"连续两次相同"才当基线时的间隔与次数。写入有**同步窗口**：刚搬完立刻回读，
#: 读到的可能还是旧状态 —— 2026-10-06 真机第三次翻车就是这么来的（记下的基线比复检看到的旧一拍）。
_STABLE_DELAY_SECONDS = 1.0
_STABLE_ATTEMPTS = 6  # 最多等 5 秒：D2 的写入同步窗口是**秒级**的


async def _settled_snapshot(
    inventory, player_name: str, character: str, target_class_type: int, manifest, slots: set[str],
):
    """等到**腾出来的格子真的空了**为止（最多 `_STABLE_ATTEMPTS` 次）。

    只等"连续两次相同"不够：写出去了、但还没同步时，连续两次读到的都是**搬之前**的旧状态
    （2026-10-06 真机第五次翻车：搬是搬了，紧接着的前提复检读到的还是 10/10，直接判"装不上"）。
    """
    snapshot = await inventory.get_armor_snapshot(player_name, character)
    for _ in range(_STABLE_ATTEMPTS - 1):
        await asyncio.sleep(_STABLE_DELAY_SECONDS)
        again = await inventory.get_armor_snapshot(player_name, character)
        facts = read_facts(again, target_class_type, manifest)
        freed = not slots or not any(facts.blocks_vault_piece(slot) for slot in slots)
        # **两个条件都要**：格子真空了（否则前提复检照样判满 —— 真机第五次翻车），
        # 且与上一次读到的版本相同（否则记下的基线比复检看到的旧一拍 —— 第六次翻车）。
        if freed and snapshot_version(again) == snapshot_version(snapshot):
            return again
        snapshot = again
    return snapshot


#: 上游格满的两种写法（错误码与那句话；只在**我们自己的回执字符串**里找，不猜别的）
_NO_ROOM_MARKERS = ("NoRoomInDestination", "no item slots available")


def no_room_failed_item_names(result) -> set[str]:
    """这次失败是不是"目标格满"？是的话，回执点名了哪些件。

    回执行长这样：`'光泽袖甲' 装备失败: 操作失败：Transfer vault→character。… error_status:
    DestinyNoRoomInDestination …` —— 名字由我们自己写，引号也是我们加的，所以解析是稳的。
    """
    names: set[str] = set()
    for step in getattr(result, "steps", []) or []:
        if getattr(step, "success", True):
            continue
        detail = str(getattr(step, "detail", ""))
        if any(marker in detail for marker in _NO_ROOM_MARKERS):
            names |= set(re.findall(r"'([^']+)'", detail))
    return names


async def equip_with_make_room_retry(*, attempt, make_room, room_args: dict):
    """先试一次；**撞上格满就腾一格再试一次**（ADR-029 P2）。

    `attempt()` 是装备动作（第一次失败时它已经把自己回滚干净了），`make_room(**room_args,
    names=…)` 只给**回执点名的那几件**腾地方。腾不出来就把第一次的失败原样交回去 —— 不许硬写。
    """
    result = await attempt()
    if getattr(result, "success", False):
        return result, [], ""
    names = no_room_failed_item_names(result)
    if not names:
        return result, [], ""
    steps, prefix = await make_room(**room_args, names=names)
    if not steps:
        return result, [], prefix
    retried = await attempt()
    note = f"第一次撞上格子满（上游 NoRoomInDestination）：{prefix}已重试一次。"
    return retried, steps, note


# ── 通用版（P4）：武器与护甲都走这条；判据与护甲版同源（`is_movable_common`） ──────────


def is_movable_common(
    *,
    instance_id: str,
    is_equipped: bool,
    is_locked: bool,
    reserved_instance_ids: Collection[str] = (),
    official_instance_ids: Collection[str] = (),
) -> bool:
    """「绝不腾」的**共享判据**（护甲版与通用版都调它，避免两处各写一遍慢慢漂）。"""
    if is_equipped or is_locked:
        return False
    if instance_id in reserved_instance_ids:
        return False
    return instance_id not in official_instance_ids


def pick_move_aside_items(
    items: Sequence,
    *,
    reserved_instance_ids: Collection[str] = (),
    official_instance_ids: Collection[str] = (),
    limit: int = 1,
    tier_of: Callable[[object], int] | None = None,
) -> list:
    """通用挑件（`InventoryItem` 这类）：低品阶 → 低光等 → 实例 ID（稳定）。

    与护甲版的差别只有一个：`InventoryItem` 里**没有大师化/巧匠**标志，所以排序里少了那两条
    （护甲版有 `Armor.is_masterworked`/`is_artifice`）。别把两条排序写成"看起来一样"的两份 ——
    调用方要给 `tier_of`（品阶从 Manifest 的 `tierType` 取，别在代码里抄一张表）。
    """
    get_tier = tier_of or (lambda _item: 0)
    movable = [
        item
        for item in items
        if is_movable_common(
            instance_id=str(getattr(item, "item_instance_id", "")),
            is_equipped=bool(getattr(item, "is_equipped", False)),
            is_locked=bool(getattr(item, "is_locked", False)),
            reserved_instance_ids=reserved_instance_ids,
            official_instance_ids=official_instance_ids,
        )
    ]
    movable.sort(key=lambda item: (
        get_tier(item),
        getattr(item, "power", None) if getattr(item, "power", None) is not None else _UNKNOWN_POWER,
        str(getattr(item, "item_instance_id", "")),
    ))
    return movable[: max(0, limit)]


def is_no_room_error(exc_or_text: object) -> bool:
    """这次失败是不是"目标格满"（异常或回执原文都认）。判据只有一个出处。"""
    text = str(exc_or_text)
    return any(marker in text for marker in _NO_ROOM_MARKERS)
