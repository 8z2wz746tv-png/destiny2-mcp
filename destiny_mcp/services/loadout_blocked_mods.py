"""一趟执行里**写不成的模组**：两类分开记，并且只有"计划要写进某个槽的"那些会让回读核对
注定不通过（判据只有 `OPERATION_KINDS` 一张表）。

真机 2026-10-03 的账：组件 207 预检判死 1 颗时，回读核对仍然跑满窗口 = **70.13 秒**
（8 次读 59.63 + 7 次 sleep 10.5），等的是一个**已知的** False；紧接着 `verify` 步还说
"…可能是同步窗口，过十几秒再看一次…"—— 对这一颗是句**错的话术**（它不是没同步，是永远不会
成立）。根因是"写不成的账"只在上游拒绝那条路追加，预检判死那条路只写一条 `mod_blocked` 步。

判据按"**这颗是不是计划要写进某个槽的**"定（不是 `len(blocked_mods)` 再硬加一笔）：

- `mod`：计划要写的那一颗（`LoadoutItem.mod_sockets` / `mods`；照抄模组写成功之后也记进
  `mod_sockets`）→ 账号上等不到它 → **算**；
- `blocked`：组件 207 预检判死的那一颗 —— 它来自同一份目标列表，只是还没往上写 → **算**。
  话术不许把它说成"被上游拒绝"：连请求都没发过；
- `clear`：腾能量。`plan_energy_clearing` 只在**没被计划占用**的槽里挑，那一格成不成都不
  影响最终状态 → **不算**（算了就是白丢一次本来能通过的核对）；
- `skipped`：照抄模组落不下（能量不够 / 一个版本都挑不到槽）—— 它既没进 `mod_sockets` 也
  没进 `mods`，回读根本不看它 → **不算**。

回执那一侧的分工也留在这里：上游拒绝的聚合成一条 `mod_blocked` 步 + 回执尾句；预检判死的
**只**在它自己那一条 `mod_blocked` 步里出现（执行流程的循环里已经写过），不重复上报。
"""

from __future__ import annotations

from typing import NamedTuple

#: 回执里的两类。分开说是因为**预检判死不是上游拒绝**（那颗连请求都没发过）。
UPSTREAM = "upstream"
PREFLIGHT = "preflight"

#: 每一种"写不成"的操作 → `(回执里算哪一类, 会不会让回读核对注定不通过)`；`None` = 哪一类都不进。
#: 四种取值各自为什么，见模块 docstring —— 改这张表等于改"要不要白等 70 秒"（真机数字）。
#: 没登记的 action **直接 KeyError**：以后新加一种"写不成"，宁可在测试里炸，也别静默漏一颗。
OPERATION_KINDS: dict[str, tuple[str | None, bool]] = {
    "mod": (UPSTREAM, True),        # 写完被上游拒绝：计划目标态里的那颗，谁都不许漏
    "clear": (UPSTREAM, False),     # 腾能量被拒：那一格不是目标态（回执要报，但不拦回读）
    "blocked": (PREFLIGHT, True),   # 组件 207 预检判死：同为目标态，只是还没往上写
    "skipped": (None, False),       # 照抄模组落不下：两份账都不进
}


class BlockedMod(NamedTuple):
    """一颗写不成的模组：`kind` 是回执里的分类，`stops` 是"回读会不会永远等不到它"。"""

    kind: str
    stops: bool
    item_name: str
    mod_hash: int
    reason: str


class BlockedMods:
    """这一趟写不成的模组，以及"回读还核对得成吗"的唯一判据。"""

    def __init__(self) -> None:
        self._rows: list[BlockedMod] = []

    def add(self, operation: str, item_name: str, mod_hash: int, reason: str) -> None:
        """记一颗写不成的模组：类别与"算不算"都从 `OPERATION_KINDS` 取（判据只此一处）。"""
        kind, stops = OPERATION_KINDS[operation]
        if kind is None:
            return
        self._rows.append(BlockedMod(kind, stops, item_name, mod_hash, reason))

    @property
    def has_any(self) -> bool:
        """有写不成的吗（两类都算）—— 回执要说清"少做了哪半"。"""
        return bool(self._rows)

    @property
    def has_upstream(self) -> bool:
        """有被上游拒绝的吗：那一类要聚合上报，也会让 `success` 为 False。"""
        return self._count(UPSTREAM) > 0

    @property
    def stops_readback(self) -> bool:
        """回读核对**必然**不通过吗（有一颗计划目标态里的没写成）。"""
        return any(row.stops for row in self._rows)

    def stopped(self, kind: str) -> int:
        """这一类里让回读注定不通过的颗数（收手那句话要按类报数，不许混成一句）。"""
        return sum(1 for row in self._rows if row.kind == kind and row.stops)

    def _count(self, kind: str) -> int:
        """这一类记了几颗（回执尾句的数法只有这一处）。"""
        return sum(1 for row in self._rows if row.kind == kind)

    def upstream_detail(self) -> str:
        """上游拒绝那一类的逐条明细（聚合成一条 `mod_blocked` 步）。"""
        return "；".join(
            f"'{row.item_name}' 的模组 {row.mod_hash}：{row.reason}"
            for row in self._rows
            if row.kind == UPSTREAM
        )

    def tail(self) -> str:
        """回执尾句：两类**各说各的** —— 预检判死不许并进"被上游拒绝"那句（它连请求都没发过）。

        两句都留着"这些条件在游戏里同样要先解决"的意思：回执回答的不只是"哪几颗没写成"，
        还有玩家紧接着要问的"那我该怎么办"。腾能量被拒也数进上游那句 —— 它确实被上游拒了，
        只是不拦回读（拦不拦由 `stops` 定）。
        """
        upstream = self._count(UPSTREAM)
        preflight = self._count(PREFLIGHT)
        tail = ""
        if upstream:
            tail += (
                f" {upstream} 颗模组被上游拒绝写入（原因见 steps.mod_blocked）——"
                "这些条件在游戏里同样要先解决，不是 API 的限制。"
            )
        if preflight:
            tail += (
                f" {preflight} 颗模组在预检就被判死（组件 207 的清单里没有它，见 steps.mod_blocked）"
                "——它们一次都没往上写过，先解决那些条件再试。"
            )
        return tail
