"""把本地刷取清单挂到会提到武器的响应上。

这段逻辑原本长在 `assistants.py` 里，但有两件事值得单独放：
1. 它要在多个 intent 之间复用（武器、商人、配装……），并且必须**失败不影响官方数据**；
2. 它决定"哪些名字算商品"，而这个判断容易被响应里的其它 `name` 字段（商人名、
   分类名、等级名）污染，值得单独读、单独测。
"""

from __future__ import annotations

from typing import Any

from ..exceptions import DestinyMCPError


def farming_reference(service: Any, names: str | list[str], *, limit: int = 8) -> dict:
    """按名字回查本地刷取清单；清单不可用时不抛异常，只标记 available=False。"""
    if service is None:
        return {"available": False, "matched_count": 0, "results": [], "unmatched": []}
    try:
        return service.lookup_farming(names, limit=limit)
    except DestinyMCPError as exc:
        return {
            "available": False,
            "matched_count": 0,
            "results": [],
            "unmatched": [],
            "error": str(exc),
            "coverage_scope": "farming_list_unavailable",
        }


def harvest_names(value: Any, *, limit: int = 8) -> list[str]:
    """从结果载荷里收集物品名，用于按名字回查刷取清单。

    只收名字、不判断是不是武器；刷取清单索引本身是精确匹配，非武器名不会命中。
    按名字去重后再计名额：同名多份副本（账号里很常见）不能挤掉别的武器。
    """
    found: list[str] = []

    def walk(node: Any, depth: int) -> None:
        if len(found) >= limit or depth > 6:
            return
        if isinstance(node, dict):
            name = node.get("name")
            if isinstance(name, str):
                candidate = name.strip()
                if candidate and candidate not in found:
                    found.append(candidate)
            for item in node.values():
                walk(item, depth + 1)
        elif isinstance(node, list):
            for item in node:
                walk(item, depth + 1)

    walk(value, 0)
    return found


def sale_item_names(vendors: Any, *, limit: int = 8) -> list[str]:
    """商人的货架商品名。

    只取 `sale_items` 里的名字：`vendors[].name` 是商人名，`categories[].name` 是
    分类名，`rank.name` 是声望名，它们都不是商品，混进来只会把 unmatched 塞满噪声。
    """
    shelf: list[dict] = []
    if isinstance(vendors, dict):
        for vendor in vendors.get("vendors", []) or []:
            if isinstance(vendor, dict):
                shelf.extend(
                    item for item in (vendor.get("sale_items") or []) if isinstance(item, dict)
                )
    return harvest_names(shelf, limit=limit)
