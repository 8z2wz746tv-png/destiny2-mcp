"""官方配装槽（游戏内配装）端点：读、应用、快照、改标识、清空。

为什么从 `bungie_client` 拆出来：客户端贴着体量上限，而这一族端点刚好带着一批
**真机踩出来的**上级事实。拆法照 `bungie_stats.py` / `manifest.py` + `manifest_*.py`
的老规矩 —— `BungieClient` 留同名门面方法，实现与注释落在这里。

## 先读文档，别在这里抄第二份

`docs/reference/bungie_api.md` **第十七节**记着这一族的全部实测（2026-10-06，本机真账号）：
官方只有哪四个动作、**没有"把任意配装数据写进槽位"的接口**（`SetLoadout` 404 与
`EquipLoadout` 200 的对照）、三个标识必须都给、`EquipLoadout` 的 URL 不带
`{membershipType}` 路径段、写入的同步窗口、槽位数组永远 20 条而
`loadoutCountPerCharacter` 是过期的。

**那个文档是唯一出处**：这里只留代码要用的常量与判据，别再抄一遍表 —— 抄了就会两边走样。
三个标识怎么补齐（继承现值 / 空槽必须给全）在 `services/loadout_official_identifiers.py`。
"""

from __future__ import annotations

from typing import TYPE_CHECKING


if TYPE_CHECKING:  # 只为类型标注：运行时不 import，免得与 bungie_client 形成环
    from .bungie_client import BungieClient

# 官方配装槽"未设置"哨兵：空槽的三个标识、以及每个槽里没插东西的插槽位都长这样（实测）。
UNSET_LOADOUT_IDENTIFIER = 2166136261

# `EquipLoadout` 的 URL **不带 `{membershipType}` 路径段**（membershipType 在请求体里）。
# 2026-10-06 真机对照：带路径段 → 404，不带 → 成功。写成常量是为了让守门能逐字钉住它，
# 也为了让"改这个入口的人"在下一次真机之前就被测试挡住（这条入口此前一直是 404）。
EQUIP_LOADOUT_PATH = "Destiny2/Actions/Loadouts/EquipLoadout/"

_IDENTIFIER_KEYS = (("name_hash", "nameHash"), ("icon_hash", "iconHash"), ("color_hash", "colorHash"))


def _loadout_identifier_payload(
    loadout_index: int,
    character_id: str,
    membership_type: int,
    name_hash: int,
    icon_hash: int,
    color_hash: int,
) -> dict:
    """`SnapshotLoadout` / `UpdateLoadoutIdentifiers` 的请求体 —— **三个标识必须都给**。

    理由见模块 docstring 的第 ② 条（真机原文是 HTTP 500 `DestinyInvalidRequest`）。
    这里刻意把六个参数都写成**必填**（不给默认值）：调用方漏一个，是 `TypeError`
    而不是一次静默的 500。要"沿用槽位当前标识"，由服务层先读出来再传进来
    （`services/loadout_official_identifiers.py`）。
    """
    return {
        "loadoutIndex": loadout_index,
        "characterId": int(character_id),
        "membershipType": membership_type,
        "nameHash": name_hash,
        "iconHash": icon_hash,
        "colorHash": color_hash,
    }


def _slot_payload(loadout_index: int, character_id: str, membership_type: int) -> dict:
    """只认槽位的动作（清空）用的请求体：这三个标识**不参与**。"""
    return {
        "loadoutIndex": loadout_index,
        "characterId": int(character_id),
        "membershipType": membership_type,
    }


async def fetch_loadouts(
    client: BungieClient,
    membership_id: str,
    membership_type: int,
) -> dict:
    """读全部角色的官方配装槽（`GetProfile` 组件 206，顺带 200 拿角色职业）。"""
    return await client.get_profile(membership_id, membership_type, components=[200, 206])


async def equip_loadout(
    client: BungieClient,
    loadout_index: int,
    character_id: str,
    membership_type: int,
) -> dict:
    """应用一个官方配装槽（游戏自己把装备、模组、子职业、神器、外观一起换上）。

    ⚠️ 这个端点**不带 `{membershipType}` 路径段**（见 `EQUIP_LOADOUT_PATH`），
    且**要求不在活动里**，否则回 `DestinyCannotPerformActionAtThisLocation`。
    两条都是 2026-10-06 真机实测，对照表在 `docs/reference/bungie_api.md` 第十七节。

    必须走 `client._post_action` 而不是自己 `static_request` + `result.get(...)`：
    **成功时 `static_request` 返回的是裸的 `Response`（这个动作是 int `0`），不是 dict** ——
    2026-10-06 修路径之前它一直 404 所以没暴露，修完路径立刻变成
    `'int' object has no attribute 'get'`。信封归一只有 `_post_action` 一处。
    """
    return await client._post_action(
        EQUIP_LOADOUT_PATH,
        _slot_payload(loadout_index, character_id, membership_type),
        "装备 Bungie 配装",
    )


async def snapshot_loadout(
    client: BungieClient,
    loadout_index: int,
    character_id: str,
    membership_type: int,
    *,
    name_hash: int,
    icon_hash: int,
    color_hash: int,
) -> dict:
    """把角色**当前装备**存进官方配装槽（三个标识必填，见 `_loadout_identifier_payload`）。"""
    return await client._post_action(
        "Destiny2/Actions/Loadouts/SnapshotLoadout/",
        _loadout_identifier_payload(
            loadout_index, character_id, membership_type, name_hash, icon_hash, color_hash
        ),
        "保存官方配装槽",
    )


async def update_loadout_identifiers(
    client: BungieClient,
    loadout_index: int,
    character_id: str,
    membership_type: int,
    *,
    name_hash: int,
    icon_hash: int,
    color_hash: int,
) -> dict:
    """改官方配装槽的名称/图标/颜色（三个都得给，"只改一个"在 API 上不存在）。"""
    return await client._post_action(
        "Destiny2/Actions/Loadouts/UpdateLoadoutIdentifiers/",
        _loadout_identifier_payload(
            loadout_index, character_id, membership_type, name_hash, icon_hash, color_hash
        ),
        "更新官方配装槽标识",
    )


async def clear_loadout(
    client: BungieClient,
    loadout_index: int,
    character_id: str,
    membership_type: int,
) -> dict:
    """清空一个官方配装槽（**不改动装备**，是这四个动作里最安全的那个）。"""
    return await client._post_action(
        "Destiny2/Actions/Loadouts/ClearLoadout/",
        _slot_payload(loadout_index, character_id, membership_type),
        "清空官方配装槽",
    )
