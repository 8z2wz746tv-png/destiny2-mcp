"""官方配装槽（游戏内配装）端点：读、装备、快照、改标识、清空。

为什么从 `bungie_client` 拆出来：客户端贴着体量上限，而这一族端点刚好带着一批
**真机踩出来的**上级事实。拆法照 `bungie_stats.py` / `manifest.py` + `manifest_*.py`
的老规矩 —— `BungieClient` 留同名门面方法，实现与注释落在这里。

## 2026-10-06 真机实测（本机真账号，三个角色，守护者等级 11）

**① 官方一共只给了四个动作**，`Destiny2/Actions/Loadouts/` 下：

| 动作 | 干什么 | 位置要求 |
| --- | --- | --- |
| `SnapshotLoadout` | 把**当前装备**存进槽位 | 无（在活动中也成功） |
| `UpdateLoadoutIdentifiers` | 改名称/图标/颜色 | 无 |
| `ClearLoadout` | 清空槽位 | 无 |
| `EquipLoadout` | 应用某个槽位 | **必须不在活动里**，否则 `DestinyCannotPerformActionAtThisLocation` |

**没有"把任意配装数据写进槽位"的接口**（官方帮助页 `Actions/Loadouts/SetLoadout/` 是
HTTP 404，而同一写法下的 `EquipLoadout/` 是 200；文档 2.21.8 全量 43 个 `Destiny2`
端点里 `Loadouts/` 下只有上表四个）。所以"配装进游戏槽"只有一条路：
**先把配装穿到身上，再快照** —— 这也正是 DIM「保存为游戏内配装」那个按钮做的事
（它的弹窗标题就是「从当前装备创建游戏内配装」）。

**② 三个标识必须都给**（`nameHash`/`iconHash`/`colorHash`）：

| 请求 | 结果 |
| --- | --- |
| 三个都给真值 | ✅ 成功 |
| 少给一个 / 给 `null` / 省略 | ❌ HTTP 500 `DestinyInvalidRequest` |
| 三个都给空哨兵 `2166136261` | ❌ 同上（**创建不出"没名字"的槽**） |

所以"只改名字"这个动作在 API 上不存在，必须把另外两个旧值带上；往空槽里存时，
名称/图标/颜色必须由调用方选。补齐逻辑在 `services/loadout_official_identifiers.py`。

**③ 两个"想当然"被证伪，别再照着改**：

- `characterId` 发**数字或字符串都能成**（当天对照实测两种都成功）——
  "int64 被 JS 精度截断"不成立；
- 写入**有同步窗口**：改完标识**立刻**回读还是旧值，**30 秒**后才变
  （与 `docs/reference/bungie_api.md` 的「写入后的同步窗口」一致）。

**④ 槽位数组永远 20 条**（索引 0–19，空槽三个标识都是哨兵、十件实例 id 全是 `"0"`），
**数组长度不代表解锁了几个** —— Manifest 里 `DestinyLoadoutConstantsDefinition` 的
`loadoutCountPerCharacter` 写的是 10，而该账号泰坦已用满 18 个，那个字段是过期的。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import aiobungie

from .bungie_errors import _bungie_unavailable_result, _http_error_code
from .logging_config import get_logger

if TYPE_CHECKING:  # 只为类型标注：运行时不 import，免得与 bungie_client 形成环
    from .bungie_client import BungieClient

logger = get_logger(__name__)

# 官方配装槽"未设置"哨兵：空槽的三个标识、以及每个槽里没插东西的插槽位都长这样（实测）。
UNSET_LOADOUT_IDENTIFIER = 2166136261

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

    走 `static_request` 而不是 aiobungie 的高层方法：后者把 `membershipType` 拼错。
    **要求不在活动里** —— 在活动里回 `DestinyCannotPerformActionAtThisLocation`
    （真机原文，2026-10-06；DIM 专门捕获这个码并提示"回轨道再试"）。
    """
    try:
        result = await client.rest.static_request(
            "POST",
            f"Destiny2/Actions/Loadouts/EquipLoadout/{membership_type}/",
            auth=await client.get_access_token(),
            json=_slot_payload(loadout_index, character_id, membership_type),
        )
        code = result.get("ErrorCode", 0)
        logger.debug("EquipLoadout OK: index=%s char=%s", loadout_index, character_id)
        return {"ErrorCode": code, "Message": result.get("Message", "Ok")}
    except aiobungie.HTTPError as exc:
        unavailable = _bungie_unavailable_result(exc, "装备 Bungie 配装")
        if unavailable:
            return unavailable
        code = _http_error_code(exc)
        logger.error("EquipLoadout failed: index=%s error=%s", loadout_index, exc)
        return {"ErrorCode": code, "Message": str(exc)}


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
