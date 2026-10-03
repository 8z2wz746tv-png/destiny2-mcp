"""装备回读核对的两个**入口**：装备这一趟（`verify_loadout`）与回滚那一趟
（`verify_restored_items`）—— 各自去读一份 profile，然后交给 `loadout_matches` 逐项比。

搬出 `loadout_equipment_service.py` 的原因和其他 mixin 一样 —— 那个文件贴着体量上限，
而"回读核对"本来就不属于"执行"：它是**证据**，两边都要用同一份判据，放在执行流程里就迟早
会各写一份。以模块函数挂在 `owner`（`LoadoutEquipmentService`）上取 `_resolver` / `_manifest`。

**一个窗口、一份判据、一句话**：判据在 `loadout_matches`（hash 两边归一，真机数字在那边），
窗口在 `write_readback.read_until`，结论与话术在 `readback_verdict`。写入有 3～10 秒同步窗口，
所以"读到 False"不等于"没写进去"——那个区分由重试够久之后的 `readback_verdict` 下。
`equip_with_recovery` 的外层**不许再读第二遍**：真机 audit `/123435` 对 `/123656` 实测，
同一份账号状态被读了两轮，那 147.8 秒全是白等（算式在 `write_readback`）。
"""

from __future__ import annotations

from typing import Any

from ..exceptions import describe_exception
from ..logging_config import get_logger
from ..models import Loadout, LoadoutItem
from .item_parser import parse_items_from_profile
from .loadout_matches import plugs_present, sockets_match, subclass_matches
from . import profile_components, write_readback

logger = get_logger(__name__)


async def verify_loadout(owner: Any, player_name: str, loadout: Loadout) -> bool:
    """逐项核对：装备实例在身上、模组/功能模组插槽对得上、子职业配置对得上。"""
    p = await owner._resolver.resolve_player(player_name)
    mid, mtype = p["membership_id"], p["membership_type"]
    char_id = await owner._resolver.resolve_character_id(
        mid, mtype, loadout.character
    )
    profile = await owner._resolver.get_profile(mid, mtype, profile_components.INVENTORY_SOCKETS)
    equipped = (
        profile.get("characterEquipment", {})
        .get("data", {})
        .get(char_id, {})
        .get("items", [])
    )
    equipped_ids = _equipped_instance_ids(profile)
    if any(item.item_instance_id not in equipped_ids for item in loadout.items):
        return False

    sockets_data = (
        profile.get("itemComponents", {}).get("sockets", {}).get("data", {})
    )
    for item in loadout.items:
        actual_sockets = sockets_data.get(item.item_instance_id, {}).get(
            "sockets", []
        )
        if not sockets_match(actual_sockets, item.mod_sockets):
            return False
        if not plugs_present(actual_sockets, item.mods):
            return False

    return subclass_matches(owner._manifest, equipped, sockets_data, loadout)


async def readback_verdict(
    owner: Any, player_name: str, loadout: Loadout, *, blocked_count: int = 0
) -> tuple[str, bool]:
    """按 `write_readback` 的窗口重读一遍，返回**这一趟的结论与话术**（`(detail, verified)`）。

    写法集中在这里，是因为"同一份状态被说成两种结论"已经出过一次事故：内层说完"没确认、
    别当成没装上"，外层拿自己那次读覆盖成"对不上"（真机 2026-10-03 第 3 轮）。调用方只许
    拿这个返回值去写 `verify` 步骤与 message，**不许自己再措辞、更不许自己再读**。

    `blocked_count` > 0 = 有模组被上游拒绝写入：那几颗**永远**不在账号上，核对必然不通过，
    所以**一次都不读** —— 真机 2026-10-03 实测跑满窗口 70.13 秒（8 次读 59.63 + 7 次 sleep
    10.5），等的是一个已知的 False；而且 `verify_loadout` 在**第一个不一致处**就返回，
    "核对过一部分"这种说法本来就立不住。
    """
    if blocked_count:
        return (
            f"这次没有回读核对：{blocked_count} 颗模组被上游拒绝写入（见 mod_blocked 步骤），"
            "核对注定不通过，所以一次都没读 —— 装备与子职业那一半这次没有独立证据"
            "（写入步骤报的是成功，但别把'没核对'当成'没装上'）。",
            False,
        )
    try:
        verified = await write_readback.read_until(
            lambda: verify_loadout(owner, player_name, loadout), bool
        )
    except Exception as exc:  # noqa: BLE001 —— 回读是**可选证据**：它自己炸了不能把写成功报成失败
        # 留痕两处：回执里那句（调用方唯一的证据）+ 服务器日志（哪一次读炸的、栈是什么）——
        # 只写进回执的话，排查时看不到栈；只打日志的话，用户会以为"核对没做"是正常的。
        logger.exception("Local loadout verification failed: %s", exc)
        # `describe_exception`：`TimeoutError()` 的 str 是空的，直接用 `exc` 会留下"没做成但没有原因"。
        return f"回读核对没做成：{describe_exception(exc)}", False
    if verified:
        return "已回读核对：装备实例、模组与子职业配置都对得上。", True
    # 次数与等待时长都要**现读** `write_readback`（模块属性，不是 import 时绑的名字）：测试会把
    # `ATTEMPTS` 压小免得真等，写死就再说一次假话（旧文案那个"12 秒"= 8×1.5，真实等待只有 7 次 sleep）。
    return (
        f"写入步骤都成功了，但回读重试 {write_readback.ATTEMPTS} 次"
        f"（约 {write_readback.window_seconds():g} 秒等待 + 每次一整份档案回读）后仍对不上"
        "（可能是同步窗口）——过十几秒再看一次，别当成没装上。",
        False,
    )


def _equipped_instance_ids(profile: dict) -> set[str]:
    """**全角色**正穿着的实例号。

    取全角色而不是只看目标角色：`_restore_exact_state` 要把件搬回"原来的角色"并核对它确实
    又穿上了，而目标角色之外的穿着状态同样算数（一件东西不可能同时穿在两个角色身上）。
    """
    return {
        str(raw.get("itemInstanceId", ""))
        for equipment in (
            profile.get("characterEquipment", {}).get("data", {}).values()
        )
        for raw in equipment.get("items", [])
    }


async def verify_restored_items(
    owner: Any, player_name: str, target_states: dict[str, LoadoutItem]
) -> bool:
    """回滚核对：候选件是否回到原位、原位该穿着的又穿上了、插槽也回到记录的样子。

    插槽那一格与 `verify_loadout` 共用 `loadout_matches.sockets_match`：`target_states` 里的
    `mod_sockets` 可能带着求解/社区模板那套**有符号**写法（见 `loadout_matches`），
    不归一同样是"恒为 False"——回滚明明成功却报"自动恢复不完整"。
    """
    p = await owner._resolver.resolve_player(player_name)
    mid, mtype = p["membership_id"], p["membership_type"]
    profile = await owner._resolver.get_profile(
        mid, mtype, profile_components.INVENTORY_SOCKETS
    )
    current_items = {
        item.item_instance_id: item
        for item in parse_items_from_profile(profile, owner._manifest)
    }
    equipped_ids = _equipped_instance_ids(profile)
    sockets_data = (
        profile.get("itemComponents", {}).get("sockets", {}).get("data", {})
    )
    for original in target_states.values():
        current = current_items.get(original.item_instance_id)
        if current is None or current.location != original.source_location:
            return False
        if original.was_equipped and original.item_instance_id not in equipped_ids:
            return False
        actual_sockets = sockets_data.get(original.item_instance_id, {}).get(
            "sockets", []
        )
        if not sockets_match(actual_sockets, original.mod_sockets):
            return False
    return True
