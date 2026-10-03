"""配装快照里的"账号护甲模组现场"：存快照时全拍下来，还原时只写对不上的格。

**为什么不能只拍身上那几件**（真机 2026-10-03 踩的坑）：快照是**测试前的还原点**，可测试里被改写的
往往不是当时穿着的那几件 —— 那天是 `equip_build` 从仓库搬进来的「黎明副歌」头盔与「光芒领主胸甲」：
它们不在快照里，还原时"回不去"，有 3 格模组只能手工近似恢复（结果不是逐格原样）。所以 `save`
把账号里**每一件护甲**的模组现场都记进快照 —— 同一份 profile 读取里就有，不多花一次往返。

**还原范围有意收窄**（别放宽）：只动**现在就在这一位角色身上（穿着或背包里）**的那些件。
`equip_build`/`equip_loadout` 会改写的也只有它当时穿的那几件（换下来的件会被游戏放进背包），
所以这个范围正好盖住"移进来又移出去"的那些；仓库与别的角色上的护甲不改 —— 穿一套旧快照不该
把整个账号的护甲都改一遍（那是"回滚账号"，不是"穿配装"），但会在回执里点名说清楚没动它们。

**能量一格一格算、先腾后占**（与 `loadout_functional_mods` 同一条规矩）：游戏逐格校验能量，
顺序反了瞬时值会超上限、上游直接拒；腾不出来就如实报"这一格没还原"，不硬写。
"""

from __future__ import annotations

from typing import Any

from ..logging_config import get_logger
from ..models import InventoryItem, Loadout, LoadoutArmorState, LoadoutOperationResult, MoveItemStep
from ..utils.hash_utils import to_unsigned
from . import profile_components
from .item_parser import armor_slot_from_bucket, parse_items_from_profile
from .loadout_mod_sockets import plug_already_installed

logger = get_logger(__name__)


def _is_armor(manifest: Any, item: InventoryItem) -> bool:
    """这一件是不是护甲：桶要拿物品定义兜一次 —— `raw.bucketHash` 可能是"仓库/邮政官"桶。

    真机实测仓库里的件 `bucket_type` 是 "Vault (General)"，只看它等于把仓库里的护甲全漏掉，
    而真机那次回不去的那两件（「黎明副歌」与「光芒领主胸甲」）恰恰躺在仓库里。
    `loadout_service._armor_slot` 早就是这么兜的（`bucketHash` 读不到就用定义的 `bucketTypeHash`），
    这里同一口径。
    """
    if item.slot:
        return True
    info = manifest.get_item_info(item.item_hash) or {}
    return bool(armor_slot_from_bucket(info.get("bucketTypeHash", 0)))


def collect_armor_state(
    profile: dict, manifest: Any, equipment: Any, exclude_ids: set[str]
) -> list[LoadoutArmorState]:
    """账号护甲的模组现场（`exclude_ids` = 这一套自己那几件，它们在 `items` 里）。

    只记"是哪一件 + 当时装着什么"：物品 hash 留着认身份，名字/位置留给还原时的现场 profile。
    """
    sockets_data = (
        profile.get("itemComponents", {}).get("sockets", {}).get("data", {}) or {}
    )
    state: list[LoadoutArmorState] = []
    for item in parse_items_from_profile(profile, manifest):
        if item.item_instance_id in exclude_ids or not _is_armor(manifest, item):
            continue
        state.append(LoadoutArmorState(
            item_instance_id=item.item_instance_id,
            item_hash=item.item_hash,
            mod_sockets=dict(equipment.read_armor_mod_sockets(
                item.item_instance_id, item.item_hash, sockets_data
            )),
        ))
    return state


def _live_armor(profile: dict, manifest: Any) -> dict[str, InventoryItem]:
    """现场：实例 ID → 物品（`location` 用来说"在仓库/别的角色"）。"""
    return {
        item.item_instance_id: item
        for item in parse_items_from_profile(profile, manifest)
        if _is_armor(manifest, item)
    }


def _energy_of(instances_data: dict, instance_id: str) -> tuple[int, int] | None:
    """这一件的 `(上限, 已用)`；读不到给 `None`（别编 0）。"""
    energy = (instances_data.get(instance_id) or {}).get("energy") or {}
    capacity, used = energy.get("energyCapacity"), energy.get("energyUsed")
    if not isinstance(capacity, int) or not isinstance(used, int):
        return None
    return capacity, used


async def restore_after_equip(
    owner: Any, player_name: str, loadout: Loadout, result: LoadoutOperationResult
) -> LoadoutOperationResult:
    """装备走完之后补还原快照里的"账号护甲现场"（真机那两件回不去的就是这一步）。

    挂在这里而不是挂在 `_equip_local_unlocked` 里面：那样一来"有模组被上游挡住"或模组阶段失败
    的那两条早退分支会把还原整段跳过（真机那次正是有 1 颗被挡住），而还原恰恰是这种时候最需要的。
    现场 profile **这时候才读**：换下来的件要先被游戏放进背包，读早了它们还标着仓库、会被跳过。

    `owner` = `LoadoutEquipmentService`（提供 `read_armor_mod_sockets` / `_plug_energy_cost` /
    `_insert_armor_mod` / `mod_label` / `_manifest` / `_resolver`）。这是**附加还原**：不改这一套
    配装穿什么，但还原没做全时结果要如实降级（每一步的红/绿都在 steps 里）。
    """
    if not loadout.armor_state:
        return result
    steps = await _restore_all(owner, player_name, loadout)
    if not steps:
        return result
    result.steps.extend(steps)
    if not all(step.success for step in steps):
        result.success = False
        result.message = f"配装 '{loadout.name}' 未完全生效（原因见 steps）。"
    return result


async def _restore_all(owner: Any, player_name: str, loadout: Loadout) -> list[MoveItemStep]:
    """解析玩家 + 读一次现场 profile，然后逐件比对还原（只动这一位角色身上的件）。"""
    player = await owner._resolver.resolve_player(player_name)
    membership_id, membership_type = player["membership_id"], player["membership_type"]
    character_id = await owner._resolver.resolve_character_id(
        membership_id, membership_type, loadout.character
    )
    profile = await owner._resolver.get_profile(
        membership_id, membership_type, profile_components.INVENTORY_SOCKETS
    )
    sockets_data = (
        profile.get("itemComponents", {}).get("sockets", {}).get("data", {}) or {}
    )
    instances_data = (
        profile.get("itemComponents", {}).get("instances", {}).get("data", {}) or {}
    )
    live = _live_armor(profile, owner._manifest)
    own_ids = {item.item_instance_id for item in loadout.items}
    steps: list[MoveItemStep] = []
    skipped: list[str] = []
    for record in loadout.armor_state:
        instance_id = record.item_instance_id
        if instance_id in own_ids:
            # 这一件走 `items` 那条路（转移 + 装备 + 逐槽写），别写两遍
            continue
        current = live.get(instance_id)
        if current is None:
            name = owner._manifest.get_item_name(record.item_hash) or instance_id
            skipped.append(f"'{name}'（已经不在账号里）")
            continue
        if to_unsigned(current.item_hash) != to_unsigned(record.item_hash):
            skipped.append(f"'{current.name}'（这个实例号现在指着别的物品）")
            continue
        current_mods = owner.read_armor_mod_sockets(
            instance_id, current.item_hash, sockets_data
        )
        diffs = _diff_sockets(current_mods, record)
        if current.location != loadout.character:
            # 不在这一位角色身上：只在与快照**确实不一致**时点名（否则整个仓库都会被报成"没还原"）
            if diffs:
                skipped.append(f"'{current.name}'（在{current.location}）")
            continue
        if diffs:
            steps.extend(await _restore_one(
                owner, current, current_mods, diffs, instances_data,
                character_id=character_id, membership_type=membership_type,
            ))
    if skipped:
        steps.append(MoveItemStep(
            action="armor_restore_skipped",
            detail=(
                f"{len(skipped)} 件护甲没有按快照还原："
                + "、".join(skipped[:6])
                + ("…" if len(skipped) > 6 else "")
                + "。它们不在这一位角色身上（或已经不在账号里）—— 要连它们一起还原，"
                "先把它们挪到这一位角色上再还原一次。"
            ),
            success=False,
        ))
    return steps


def _diff_sockets(
    current: dict[int, int], record: LoadoutArmorState
) -> list[tuple[int, int]]:
    """与快照不一致的格：`[(插槽号, 快照里的插件 hash)]`（一致就给空表 = 不用动它）。"""
    return [
        (socket_index, plug_hash)
        for socket_index, plug_hash in sorted(record.mod_sockets.items())
        # 两边都过 `to_unsigned`：快照与现场都来自 profile（无符号），而插件 hash 在别处有
        # 另一套写法（有符号），跨值域比会静默判"不相等"、把每一格都当成要写。
        if socket_index in current
        and to_unsigned(current[socket_index]) != to_unsigned(plug_hash)
    ]


async def _restore_one(
    owner: Any,
    item: InventoryItem,
    current: dict[int, int],
    diffs: list[tuple[int, int]],
    instances_data: dict,
    *,
    character_id: str,
    membership_type: int,
) -> list[MoveItemStep]:
    """一件护甲：把不一致的格按"先腾后占"写回去（`diffs` 由 `_diff_sockets` 给）。"""
    energy = _energy_of(instances_data, item.item_instance_id)
    if energy is None:
        return [MoveItemStep(
            action="armor_restore",
            detail=f"'{item.name}' 有 {len(diffs)} 格与快照不一致，但读不到它的能量上限，没动。",
            success=False,
        )]
    capacity, projected = energy
    steps: list[MoveItemStep] = []
    deltas = {
        socket_index: (owner._plug_energy_cost(plug_hash) or 0)
        - (owner._plug_energy_cost(current[socket_index]) or 0)
        for socket_index, plug_hash in diffs
    }
    for socket_index, plug_hash in sorted(diffs, key=lambda row: (deltas[row[0]], row[0])):
        delta = deltas[socket_index]
        if projected + delta > capacity:
            steps.append(MoveItemStep(
                action="armor_restore",
                detail=(
                    f"'{item.name}' 插槽 {socket_index} 与快照不一致，但能量不够"
                    f"（要 {projected + delta}/{capacity}）：这一格没还原。"
                ),
                success=False,
            ))
            continue
        try:
            result = await owner._insert_armor_mod(
                item.item_instance_id, plug_hash, socket_index, character_id,
                membership_type,
            )
        except Exception as exc:  # noqa: BLE001 —— 一格写不动不许连累整套还原与整条配装
            logger.warning(
                "Restoring armor mods failed for %s socket %s: %s",
                item.item_instance_id, socket_index, exc,
            )
            steps.append(MoveItemStep(
                action="armor_restore",
                detail=f"还原 '{item.name}' 插槽 {socket_index} 失败：{exc}",
                success=False,
            ))
            continue
        already = plug_already_installed(result)
        ok = result.get("ErrorCode", 0) == 1 or already
        label = owner.mod_label(plug_hash)
        detail = (
            f"'{item.name}' 插槽 {socket_index} 已经是快照里的 '{label}'，未改动"
            if already
            else f"按快照还原 '{item.name}' 插槽 {socket_index} → '{label}'"
        )
        if not ok:
            # 上游拒绝要带上原文（原因在回执里，别让人再猜一次）
            detail += f" 失败：{str(result.get('Message') or '').strip() or '上游没给原因'}"
        else:
            projected += delta
        steps.append(MoveItemStep(action="armor_restore", detail=detail, success=ok))
    return steps
