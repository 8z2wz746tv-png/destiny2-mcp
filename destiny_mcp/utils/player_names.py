"""玩家显示名的唯一出处：**游戏内 ID 优先**。

坑（社区反馈 + 真机实测 2026-09-17）：Bungie 的玩家结构里有两个名字：

- ``bungieGlobalDisplayName`` + ``bungieGlobalDisplayNameCode`` = **游戏内 ID**（`名字#1234`），
  同一账号在四个平台**完全一致**；
- ``displayName`` = **平台名**（Steam / Xbox / PSN / Epic 的 persona），**每个平台各不相同**。

实测同一账号：游戏内 ID 是 `OneTop丶Husky#6641`，而四个平台的 `displayName` 分别是
`OneTop丶Husky` / `SecHusky` / `early_moccasin0` / `此人以嫖到广东`。

以前代码写的是 `displayName or bungieGlobalDisplayName` —— 顺序反了，于是"显示成 Steam 名
而不是游戏内 ID"。**所有需要展示玩家名的地方都走这里**，不要自己拼。
"""

from __future__ import annotations


def bungie_display_name(node: dict | None) -> str:
    """取玩家的**游戏内 ID**：`bungieGlobalDisplayName#code`。

    拿不到游戏内 ID 时才退回平台名（`displayName`）——那属于降级，调用方若在意可以自行判断。
    都拿不到给空串（不编名字）。
    """
    if not isinstance(node, dict):
        return ""
    name = str(node.get("bungieGlobalDisplayName") or "").strip()
    code = node.get("bungieGlobalDisplayNameCode")
    if name:
        return f"{name}#{code}" if code not in (None, "", 0) else name
    return str(node.get("displayName") or "").strip()


def bungie_display_name_of_player(player: dict | None) -> str:
    """PGCR 那种 `{destinyUserInfo: {...}}` 结构里的玩家名。"""
    if not isinstance(player, dict):
        return ""
    return bungie_display_name(player.get("destinyUserInfo") or player)
