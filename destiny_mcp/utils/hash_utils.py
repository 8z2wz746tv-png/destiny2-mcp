"""Destiny 2 hash conversion utilities.

Bungie API uses unsigned 32-bit hashes, but the manifest SQLite stores
signed 32-bit integers. This module provides conversion functions used
across the codebase.

约定：**凡是拿外部来源的 hash 去查本地表（或反过来）的入口，都要先过这里**。
两边约定不一致时不会报错，只会查不中 —— 表现是"全部 false"或空结果。已经踩过的
一次：DIM 愿望单用无符号做键、manifest 给有符号，god roll 标记整表失效。
本地 JSON 数据（`data/`、`destiny_mcp/data/`）由各自的抓取脚本写入，用之前先确认
它写的是哪一种。
"""

from __future__ import annotations

_MASK = 0xFFFFFFFF
_SIGN_BIT = 0x80000000


def to_signed(h: int) -> int:
    """Convert unsigned 32-bit hash to signed (for manifest DB lookup)."""
    h = h & _MASK
    return h - _MASK - 1 if h & _SIGN_BIT else h


def to_unsigned(h: int) -> int:
    """Convert signed 32-bit hash to unsigned (for API calls)."""
    return h & _MASK


def hash_variants(*hashes: int | None) -> set[int]:
    """这组 hash 的**两套值域写法**：原样 + 有符号 + 无符号（`None` 跳过）。

    为什么要成组地给：同一个编号，**谁给的有符号、谁给的无符号全看来源** ——
    Manifest 的 `id` 列与 `manifest.search()` 给**有符号**（`保护琢面` = -1668045176），
    物品 JSON 体内的 `plugItemHash` / `singleInitialItemHash` 与账号侧（profile 组件）
    给**无符号**（同一个碎片 = 2626922120）。比较时只归一一边，另一边永远比不中，
    而且**不报错**：表现是"明明有这件东西却说没有"。

    这个病 2026-10 咬了三次，所以集合式比较一律走这里，别在调用点各写一份
    "add 三连"（`allowed_exotic_hashes` / `farm_target` / `solver` 曾各有一份）：

    1. DIM 愿望单用无符号做键、manifest 给有符号 → god roll 标记整表失效；
    2. 配装规模闸门的"指定金装"收窄静默失效（闸门自己还在建议"指定一件金装"）；
    3. 碎片配置：请求里的碎片被判"与插槽不兼容"，真机 5 颗里 4 颗传不进去
       （保护/黎明/勇气/希望；碰巧能过的都是 hash < 2^31 的使命/毁灭）。
    """
    variants: set[int] = set()
    for h in hashes:
        if h is None:
            continue
        variants.update({h, to_signed(h), to_unsigned(h)})
    return variants
