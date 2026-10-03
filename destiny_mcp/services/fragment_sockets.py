"""碎片槽现场：哪些槽能用、哪个空着、哪个被账号禁用（报错与数数的唯一判据）。

从 `build_fragments` 抽出来（那边登记着 82 行上限，而这条报错要随真机反馈改措辞）：
"这一片算不算碎片槽"同时决定**数几颗**（`fragment_slot_indices`）与**怎么报**
（`fragment_slot_report`）—— 两处各写一份就会互相漂移（报出来的清单里混进超能/星象槽，
而条数只有碎片那一档）。

判据链（真机，棱镜术士）：`subclass_assistant(intent="get")` 显示 socket 9–13 是
`is_active: true` 的碎片槽、socket 14 是 `is_active: false`（`isEnabled: false`）的
`空碎片插槽`。`equip_build` 的 `subclass` 步骤把第 6 颗碎片排进 14 时，上游回
HTTP 500 `DestinySocketActionNotAllowed` / `request.plug.socketIndex:
The requested socket is disabled.`；同一颗碎片写进 socket 12 成功。
"""

from __future__ import annotations

from ..manifest import ManifestManager
from ..models import LoadoutSubclassConfig
from ..utils.hash_utils import hash_variants, to_unsigned
from .loadout_plug_lookup import EMPTY_FRAGMENT_PLUG


def is_fragment_slot(plug_hash: int, known_fragments: set[int]) -> bool:
    """这个槽算不算碎片槽：装着碎片算，空着（`空碎片插槽` 占位）也算。

    空着的槽同样是**一个能填的碎片槽** —— 漏掉它，玩家换碎片时会被要求"少给一颗"，
    而那一颗本来就能填进去。
    """
    return plug_hash in known_fragments or to_unsigned(plug_hash) == EMPTY_FRAGMENT_PLUG


def is_assignable_fragment_slot(
    current: LoadoutSubclassConfig, index: int, plug_hash: int, known: set[int]
) -> bool:
    """某一个槽现在算不算"可写的碎片槽" —— **数数与报错共用这一份判据**。

    以前这里是两处各写一份（数数只看 `plug_sockets`、报错只看是不是碎片槽），注入一次
    "禁用槽也算碎片槽"就露了：能写的颗数报 5、清单却列了 6 个槽（把禁用的 14 也列上），
    调用方照清单补一颗正好撞回真机那个上游 500。
    `socket_states` 里 `is False` 的槽不算可用；缺记录时**不替账号断言"禁用"**
    （能写的槽本来就只来自 `read_subclass_config` 收进来的那些）。
    """
    return (
        is_fragment_slot(plug_hash, known)
        and current.socket_states.get(index) is not False
    )


def fragment_slot_indices(current: LoadoutSubclassConfig) -> list[int]:
    """**现在能写**的碎片槽下标，按顺序（碎片按这个顺序逐颗落槽）。

    禁用槽不在 `current.plug_sockets` 里 —— `read_subclass_config` 按 `isEnabled` 滤过，
    所以"第 N 个碎片槽"这个位置假设在这里已经不成立（真机 6 个碎片槽里 14 是禁用的，
    旧写法把第 6 颗排进 14 → 上游 500）。
    """
    known = hash_variants(*current.fragment_hashes)
    return sorted(
        index
        for index, plug_hash in current.plug_sockets.items()
        if is_assignable_fragment_slot(current, index, plug_hash, known)
    )


def fragment_slot_report(
    manifest: ManifestManager, current: LoadoutSubclassConfig
) -> str:
    """把碎片槽现场写成一句话：哪些能用、哪个空着、哪个被禁用。

    没有这句，"少了/多了几颗"只能靠调用方自己再读一次账号；社区模板（5 颗）撞上
    6 槽子职业时，玩家看到的就是一句「收到 5 个」而不知道第 6 个该往哪儿补。
    """
    known = hash_variants(*current.fragment_hashes)
    slots: list[str] = []
    free: list[int] = []
    unfinished: list[int] = []
    for index, plug_hash in sorted(current.plug_sockets.items()):
        # 与数数共用同一个判据（`is_assignable_fragment_slot`）：不然会再漂一次 ——
        # 数数按现场状态、清单纯按"是不是碎片槽"，就能出现"能写 5 颗"却列了 6 个槽。
        if not is_assignable_fragment_slot(current, index, plug_hash, known):
            continue
        if to_unsigned(plug_hash) == EMPTY_FRAGMENT_PLUG:
            slots.append(f"{index}=空着")
            free.append(index)
        else:
            slots.append(f"{index}={manifest.get_item_name(plug_hash) or plug_hash}")
    # 不在"可写清单"里的碎片槽分成两档，**不许混着说**：账号明说 `isEnabled: false` 的
    # 是"禁用"（知道），没给这个字段的是"状态未知"（不知道）。真机的 socket 14 属于前者。
    for index in sorted(set(current.fragment_sockets)):
        if is_assignable_fragment_slot(
            current, index, current.plug_sockets.get(index, EMPTY_FRAGMENT_PLUG), known
        ):
            continue
        if current.socket_states.get(index) is False:
            unfinished.append(index)
    parts = [f"可写的碎片槽 {len(slots)} 个：{'、'.join(slots)}"]
    if free:
        parts.append(f"空着的是槽 {'、'.join(str(i) for i in free)}")
    if unfinished:
        parts.append(
            f"槽 {'、'.join(str(i) for i in unfinished)} 被账号禁用（isEnabled=false），"
            "这不算可写的槽、也不写"
        )
    unknown = sorted(
        index
        for index in set(current.fragment_sockets) - set(current.plug_sockets)
        if index not in unfinished
    )
    if unknown:
        parts.append(
            f"槽 {'、'.join(str(i) for i in unknown)} 的活动状态没读到（账号没给 isEnabled），"
            "按不可写处理"
        )
    return "；".join(parts)
