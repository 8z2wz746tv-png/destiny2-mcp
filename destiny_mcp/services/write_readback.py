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

**一轮窗口的真实代价（2026-10-03 真机，audit `/123435` 对 `/123656`）**：那次 `equip_build`
224.2 秒里有 **147.8 秒**花在"回读没对上"这条路上，而同一晚紧接着的 `equip_loadout`
（写入量相当：5 次搬运 + 批量装备 5 件 + 12 次按快照还模组 + 4 颗碎片）只用 76.4 秒、
回读一次就通过。差额能反解出**单次回读尝试 ≈ 8.45 秒**：一轮失败窗口 = 7 次等待（10.5 秒）
+ 8 次尝试，而通过的那一轮只花 1 次尝试 —— `2×(10.5 + 8R) − R = 147.8 → R = 8.45`。
每次尝试是 3 次网络往返（`player_resolver` 的 `search_player` + profile[200] +
profile[INVENTORY_SOCKETS]），一轮窗口因此约 78 秒。

两点由此定死：

- **窗口一轮调用只许开一轮**：同一份账号状态、同一个判据，再读一轮只是把 78 秒再花一遍
  （`equip_with_recovery` 的外层干过这件事，见 `loadout_exact_flow`）。判据在
  `loadout_verify`，这里只管"重试到满足或次数用尽"。
- **要报"等了多久"就用 `window_seconds()`**：`ATTEMPTS × DELAY_SECONDS` 不是等待时长 ——
  第一次读是立刻发生的、最后一次读后面没有 sleep，实际 sleep 是 `(ATTEMPTS - 1)` 次
  （8 次读 = 10.5 秒等待）。2026-10-03 之前有四处把它写成"12 秒"，那是句假话。
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any, TypeVar

T = TypeVar("T")

# 8 次 × 1.5 秒 ≈ 10.5 秒等待：真机实采窗口 3～10 秒不等（同一台机器、同一晚两次写入，
# 一次 3 秒可见、一次 5 秒还没现身），所以宁可多等一会儿，也别把"还没同步"报成失败。
ATTEMPTS = 8
DELAY_SECONDS = 1.5


def window_seconds() -> float:
    """一轮窗口**实际等待**的秒数（不含每次读本身的往返）。

    `read_until` 的结构是"先读一次，再 (attempts-1) 次：判 → sleep → 读"，所以 sleep 只有
    `attempts - 1` 次。要报给用户"等了多久"就调这里，别再各写一份 `ATTEMPTS * DELAY_SECONDS`
    ——那个数（12）比真实等待（10.5）大，而且和真机上一轮 78 秒的代价差得更远。
    """
    return (ATTEMPTS - 1) * DELAY_SECONDS


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
