"""Destiny 2 hash conversion utilities.

Bungie API uses unsigned 32-bit hashes, but the manifest SQLite stores
signed 32-bit integers. This module provides conversion functions used
across the codebase.
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
