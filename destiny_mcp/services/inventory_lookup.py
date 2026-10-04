"""在 profile 里**定位一件实例**：仓库 / 角色背包 / 已装备。

从 `inventory_service` 抽出来（那边贴着 783 行上限）：这段是纯字典遍历，
没有任何 Bungie 调用、没有服务状态，放在服务类旁边只是历史原因。
`_locate_instance` 的调用方要的是"这件在哪"，与"库存怎么读全"是两件事。
"""

from __future__ import annotations

_CLASS_LOCATION = {0: "titan", 1: "hunter", 2: "warlock"}


def character_location(profile: dict, character_id: str) -> str:
    """角色 ID → 位置键（`titan`/`hunter`/`warlock`，认不出来给 `character`）。"""
    characters = ((profile.get("characters") or {}).get("data")) or {}
    entry = characters.get(character_id) or {}
    return _CLASS_LOCATION.get(entry.get("classType"), "character")


def locate_instance(
    profile: dict, item_instance_id: str
) -> tuple[dict, str, str] | None:
    """在仓库/角色背包/已装备里找一件实例，返回 `(item, 位置, 角色 ID)`；找不到给 `None`。"""
    containers: list[tuple[str, str, list]] = [
        ("vault", "", ((profile.get("profileInventory") or {}).get("data") or {}).get("items") or []),
    ]
    for char_id, inv in (((profile.get("characterInventories") or {}).get("data")) or {}).items():
        containers.append((character_location(profile, char_id), char_id, (inv or {}).get("items") or []))
    for char_id, eq in (((profile.get("characterEquipment") or {}).get("data")) or {}).items():
        containers.append((character_location(profile, char_id), char_id, (eq or {}).get("items") or []))
    for location, char_id, items in containers:
        for item in items:
            if str(item.get("itemInstanceId") or "") == item_instance_id:
                return item, location, char_id
    return None
