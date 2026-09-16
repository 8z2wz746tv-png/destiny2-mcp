"""写入后回读核对：Bungie 的 profile 有"刚写完还读到旧值"的窗口。

真机实采（2026-09-16，换子职业）：`EquipItem` 返回 `ErrorCode=1`，**立刻**回读仍是旧子职业，
约 3 秒后再读才是新的。所以：

- "写完立刻读一次就下结论"会把**成功的写入报成失败**（假失败）；
- 但也不能读不到就当成功（假成功）——
  正确做法是**重试一段时间**，用最后一次读到的值判断，并把"重试过多久"写进消息；
- 真机实测这个窗口是**几秒到十几秒不固定**，所以回读不到时只能说"没确认"（`unverified`），
  不能说"没换成"——上游已经返回成功了，那是两件事。

这里只做"重读到满足条件或次数用尽"，返回最后一次读到的值；判断与话术留给调用方，
免得把"没同步"和"真失败"混成一句话。
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any, TypeVar

T = TypeVar("T")

# 8 次 × 1.5 秒 ≈ 10.5 秒：真机实采窗口 3～10 秒不等（同一台机器、同一晚两次写入，
# 一次 3 秒可见、一次 5 秒还没现身），所以宁可多等一会儿，也别把"还没同步"报成失败。
ATTEMPTS = 8
DELAY_SECONDS = 1.5


async def read_until(
    read: Callable[[], Awaitable[T]],
    matches: Callable[[T], bool],
    *,
    attempts: int | None = None,
    delay: float | None = None,
) -> T:
    """反复回读直到 `matches` 成立或次数用尽，返回**最后一次**读到的值。

    `attempts`/`delay` 缺省用模块常量（测试里把常量调小即可，不用真等 5 秒）。
    """
    attempts = ATTEMPTS if attempts is None else attempts
    delay = DELAY_SECONDS if delay is None else delay
    value: Any = await read()
    for _ in range(max(attempts - 1, 0)):
        if matches(value):
            return value
        await asyncio.sleep(delay)
        value = await read()
    return value
