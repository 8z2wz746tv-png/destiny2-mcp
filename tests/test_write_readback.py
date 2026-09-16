"""回读重试：真机上"写完立刻读还是旧值"是常态，不能当成失败。

真机实采（2026-09-16，换子职业）：`EquipItem` 返回 `ErrorCode=1`，立刻回读仍是旧子职业，
约 3 秒后才变。这里守的就是这段行为：**重试到看见为止**，次数用尽后把最后一次的值
如实交回调用方（由调用方报失败，不是这里编成功）。
"""

from __future__ import annotations

import pytest

from destiny_mcp.services import write_readback


@pytest.fixture(autouse=True)
def _no_delay(monkeypatch):
    monkeypatch.setattr(write_readback, "DELAY_SECONDS", 0)


async def test_returns_as_soon_as_the_value_matches() -> None:
    reads = ["old", "old", "new"]

    async def read() -> str:
        return reads.pop(0)

    value = await write_readback.read_until(read, lambda v: v == "new")

    assert value == "new"
    assert reads == [], "对上之后不该再多读一次"


async def test_first_read_matching_does_not_read_again() -> None:
    calls = 0

    async def read() -> str:
        nonlocal calls
        calls += 1
        return "new"

    assert await write_readback.read_until(read, lambda v: v == "new") == "new"
    assert calls == 1


async def test_gives_up_after_the_attempt_budget_and_returns_the_last_value() -> None:
    calls = 0

    async def read() -> str:
        nonlocal calls
        calls += 1
        return "old"

    value = await write_readback.read_until(read, lambda v: v == "new")

    assert value == "old", "读不到就把最后一次的值交回去，不编成功"
    assert calls == write_readback.ATTEMPTS
