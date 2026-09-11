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
