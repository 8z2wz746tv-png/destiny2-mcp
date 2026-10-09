"""服务端配装候选的暂存：签发一次、绑定玩家、限时、**写成功之后**才焚（ADR-025）。

`resolve()` 返回状态而不抛异常：unknown / expired / consumed / player_mismatch 是四件不同的事，
话术在 `candidate_messages`；求解参数在 `candidate_search_args`。
"""

from __future__ import annotations

import time
from collections import OrderedDict
from collections.abc import Callable
from typing import Any, Literal

from ..build_contracts import CanonicalBuild
from .candidate_search_args import SearchArgsBook

# 200 条够一个人连着试配装；TTL 30 分钟（0.7.10 起 execution_id 是默认出口的唯一引用）。
MAX_CANDIDATES = 200
TTL_SECONDS = 30 * 60

CandidateStatus = Literal["ok", "unknown", "expired", "consumed", "player_mismatch"]


class BuildCandidateStore:
    """execution_id → 服务端签发的那份方案。"""

    def __init__(
        self,
        *,
        ttl_seconds: float | None = None,
        max_candidates: int | None = None,
        clock: Callable[[], float] | None = None,
    ) -> None:
        # 默认值**调用时**解析：写成默认参数会在定义时绑定，测试就换不掉了。
        self._ttl_seconds = TTL_SECONDS if ttl_seconds is None else ttl_seconds
        self._max_candidates = MAX_CANDIDATES if max_candidates is None else max_candidates
        self._clock = clock or (lambda: time.monotonic())
        self._builds: OrderedDict[str, CanonicalBuild] = OrderedDict()
        self._issued_at: dict[str, float] = {}
        self._players: dict[str, str] = {}
        # 求解参数单独一本簿记：candidate_messages 直接读它拼「原条件重解」的回放
        self.search_args_book = SearchArgsBook()
        # 焚过的 ID 留墓碑（含属主）：不留就只能把"用过了"报成"不认识"，留属主免得泄露给别人。
        self._consumed: dict[str, tuple[float, str]] = {}

    def register(
        self, build: CanonicalBuild, player_name: str = "", search_args: dict[str, Any] | None = None
    ) -> None:
        """存一份深拷贝（调用方改自己那份不影响候选）；`search_args` 见 `SearchArgsBook`。"""
        if not build.execution_id:
            return
        now = self._clock()
        self.evict_expired(now)
        self._builds[build.execution_id] = build.model_copy(deep=True)
        self._builds.move_to_end(build.execution_id)
        self._issued_at[build.execution_id] = now
        self._players[build.execution_id] = player_name.casefold()
        self.search_args_book.record(build.execution_id, search_args)
        while len(self._builds) > self._max_candidates:
            self._drop(next(iter(self._builds)))

    def resolve(
        self, execution_id: str, player_name: str
    ) -> tuple[CanonicalBuild | None, CandidateStatus]:
        """按 ID 取回候选（不改动它：焚烧由 `consume` 负责）。

        顺序不能调：墓碑（烧过的）先判 —— 一个烧过的 ID 报"用过了"比报"过期/不认识"
        有用得多（前者告诉调用方"不用重解，改完前提拿同一个 ID 重试"）；属主也先判，
        别人的 ID 一律 `player_mismatch`，不泄露"它存在过"。
        """
        if not execution_id:
            return None, "unknown"
        burned = self._consumed.get(execution_id)
        if burned is not None:
            _, owner = burned
            if owner and owner != player_name.casefold():
                return None, "player_mismatch"
            return None, "consumed"
        build = self._builds.get(execution_id)
        issued_at = self._issued_at.get(execution_id)
        owner = self._players.get(execution_id)
        if build is None or issued_at is None or owner is None:
            return None, "unknown"
        if owner and owner != player_name.casefold():
            return None, "player_mismatch"
        if self._clock() - issued_at >= self._ttl_seconds:
            self._drop(execution_id)
            return None, "expired"
        self.evict_expired()
        return build, "ok"

    def consume(self, execution_id: str) -> None:
        """**写成功之后**才烧：留墓碑（供 `resolve` 报"用过了"），不是抹掉。

        被拦下/写入失败的执行**不该调这里** —— 账号没改，候选还该能用（ADR-025）。
        """
        owner = self._players.get(execution_id)
        if execution_id not in self._builds and owner is None:
            return
        self._consumed[execution_id] = (self._clock(), owner or "")
        self._builds.pop(execution_id, None)
        self._issued_at.pop(execution_id, None)
        self._players.pop(execution_id, None)

    def evict_expired(self, now: float | None = None) -> None:
        moment = self._clock() if now is None else now
        for execution_id, issued_at in tuple(self._issued_at.items()):
            if moment - issued_at >= self._ttl_seconds:
                self._drop(execution_id)
        # 墓碑按同一条 TTL 清：留久了是内存泄漏，而且那么老的 ID 报"过期"更有用。
        for execution_id, (burned_at, _owner) in tuple(self._consumed.items()):
            if moment - burned_at >= self._ttl_seconds:
                self._consumed.pop(execution_id, None)
        # 半途失败留下的"有方案没签发时间"的条目也清掉
        for execution_id in tuple(self._builds):
            if execution_id not in self._issued_at:
                self._drop(execution_id)

    def _drop(self, execution_id: str) -> None:
        self._builds.pop(execution_id, None)
        self._issued_at.pop(execution_id, None)
        self._players.pop(execution_id, None)
        self._consumed.pop(execution_id, None)
        self.search_args_book.forget(execution_id)

