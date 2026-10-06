"""官方配装槽的三个标识（名字/图标/颜色）：写入前怎么补齐。

从 `loadout_service` 拆出来（那边贴着 986 行上限），也因为这里面记着一条**真机踩出来的**
上级事实，值得单独一个文件。

## 上级事实（2026-10-06 实测，Bungie 侧）

`SnapshotLoadout` / `UpdateLoadoutIdentifiers` **三个标识必须都给**：

| 请求 | 结果 |
| --- | --- |
| 三个都给真值 | ✅ 成功 |
| 少给一个 / 给 `null` / 干脆省略 | ❌ HTTP 500 `DestinyInvalidRequest` |
| 三个都给空哨兵 `2166136261` | ❌ 同上（**创建不出"没名字"的槽**） |

所以两件事是设计约束，不是我们的选择：

- `update_official_identifiers`「只改名字」**在 API 上不存在** —— 必须把另外两个旧值带上；
- 往**空槽**里存，名称/图标/颜色必须由调用方选（`intent="search_identifiers"` 给候选）。

`characterId` 发数字还是字符串**都能成**（当天对照实测：两种都成功）——
之前怀疑的"int64 被 JS 精度截断"不成立，别再照着那个改。

另外两条同一天量到的、别再从文档猜的事实：

- 写入**有同步窗口**：改完标识**立刻**回读还是旧值，**30 秒**后才变
  （与 `docs/reference/bungie_api.md` 那节「写入后的同步窗口」一致）；
- `EquipLoadout` 要求**不在活动里**（在活动里回 `DestinyCannotPerformActionAtThisLocation`），
  而 `SnapshotLoadout` / `ClearLoadout` **没有**这个限制（猎人在活动中快照照样成功）。
"""

from __future__ import annotations

from typing import Any

from ..bungie_loadouts import UNSET_LOADOUT_IDENTIFIER
from . import profile_components

_KEYS = (("name_hash", "nameHash"), ("icon_hash", "iconHash"), ("color_hash", "colorHash"))

NEED_ALL_THREE = (
    "这个槽还是空的，名称、图标、颜色必须自己选 —— Bungie 不接受空的标识"
    "（实测连空哨兵都会被拒），所以创建不出没名字的配装槽。"
    "先用 loadout_assistant(intent=\"search_identifiers\") 挑三个 hash，"
    "再把 name_hash / icon_hash / color_hash 一起传进来。"
)


def current_identifiers(
    profile: dict, char_id: str, loadout_index: int,
) -> dict[str, int] | None:
    """从 profile 快照里取某个槽**当前**的三个标识；空槽或索引越界 → `None`。

    空槽的判据是"三个标识里**任何一个**是空哨兵"——实测空槽三个都是 `2166136261`，
    而只要有一个是哨兵，这个槽就没有可沿用的完整标识（API 会拒）。
    """
    rows = (
        profile.get("characterLoadouts", {}).get("data", {})
        .get(char_id, {}).get("loadouts") or []
    )
    if loadout_index >= len(rows):
        return None
    row = rows[loadout_index]
    values = {key: row.get(camel) for key, camel in _KEYS}
    if any(value is None or value == UNSET_LOADOUT_IDENTIFIER for value in values.values()):
        return None
    return values


async def resolve_identifiers(
    resolver: Any,
    *,
    mid: str,
    mtype: int,
    char_id: str,
    loadout_index: int,
    name_hash: int | None,
    icon_hash: int | None,
    color_hash: int | None,
) -> tuple[dict[str, int] | None, str]:
    """补齐这次写入要用的三个标识 —— 返回 `(标识, 缺什么的中文说明)`。

    三个都给了就**不多读一次 profile**（真机每次 profile 都要几百毫秒到一秒）；
    少给的就从该槽当前标识继承（"只改名字"只能这样表达）；空槽没有旧值可继承时，
    返回 `(None, 说明)` 让调用方把话原样说给玩家，而不是发一个必然 500 的请求。
    """
    given = {"name_hash": name_hash, "icon_hash": icon_hash, "color_hash": color_hash}
    if all(value is not None for value in given.values()):
        return given, ""

    profile = await resolver.get_profile(mid, mtype, profile_components.LOADOUT_SLOTS)
    current = current_identifiers(profile, char_id, loadout_index)
    if current is None:
        return None, NEED_ALL_THREE
    return {
        key: (value if value is not None else current[key])
        for key, value in given.items()
    }, ""
