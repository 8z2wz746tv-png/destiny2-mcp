"""服务端配装候选的暂存：签发一次、绑定玩家、限时、用完即焚。

`equip_build` 的信任来源是「这份方案是服务端自己签发的」，不是调用方回传的内容 ——
所以候选必须留在服务端（进程内存，个人版单进程够用；重启后候选失效，重新求解即可）。
搬出 `build_service.py` 的原因：那里贴着体积上限，而"候选怎么存、什么时候过期"
与求解流程本来无关。

`resolve()` 返回状态而不是抛异常：unknown / expired / player_mismatch 是**三件不同的事**，
工具层要按状态给对得上的话术（"已过期，请重新求解" ≠ "这不是你的候选"），
所以这里的返回类型把三者分开，不合并成一个 None。
"""

from __future__ import annotations

import time
from collections import OrderedDict
from collections.abc import Callable
from typing import Literal

from ..build_contracts import CanonicalBuild

# 200 条够一个人连着试配装；10 分钟够走完"看预览 → 确认"。
MAX_CANDIDATES = 200
TTL_SECONDS = 30 * 60  # 0.7.10 起 execution_id 是默认出口的唯一引用，"给玩家看→等回话"常超 10 分钟

CandidateStatus = Literal["ok", "unknown", "expired", "player_mismatch"]


class BuildCandidateStore:
    """execution_id → 服务端签发的那份方案。"""

    def __init__(
        self,
        *,
        ttl_seconds: float | None = None,
        max_candidates: int | None = None,
        clock: Callable[[], float] | None = None,
    ) -> None:
        # 三个默认值都在**调用时**解析：写成默认参数会在定义时绑定好函数对象与数字，
        # 测试再替换 `time.monotonic` / `TTL_SECONDS` 就换不掉了。
        self._ttl_seconds = TTL_SECONDS if ttl_seconds is None else ttl_seconds
        self._max_candidates = MAX_CANDIDATES if max_candidates is None else max_candidates
        self._clock = clock or (lambda: time.monotonic())
        self._builds: OrderedDict[str, CanonicalBuild] = OrderedDict()
        self._issued_at: dict[str, float] = {}
        self._players: dict[str, str] = {}

    def register(self, build: CanonicalBuild, player_name: str = "") -> None:
        """存一份深拷贝：调用方之后改自己那份，不能影响已签发的候选。"""
        if not build.execution_id:
            return
        now = self._clock()
        self.evict_expired(now)
        self._builds[build.execution_id] = build.model_copy(deep=True)
        self._builds.move_to_end(build.execution_id)
        self._issued_at[build.execution_id] = now
        self._players[build.execution_id] = player_name.casefold()
        while len(self._builds) > self._max_candidates:
            self._drop(next(iter(self._builds)))

    def resolve(
        self, execution_id: str, player_name: str
    ) -> tuple[CanonicalBuild | None, CandidateStatus]:
        """按 ID 取回候选（不改动它：焚烧由 `consume` 负责）。

        先查再判过期，顺序不能调：先清过期条目的话，"过期"会被报成"不认识"，
        调用方拿到的下一步就成了"重新求解"而不是"这次确认超时了，重新确认一次"。
        """
        if not execution_id:
            return None, "unknown"
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
        """一次确认只能执行一次：执行前就烧掉，失败也不还。"""
        self._drop(execution_id)

    def evict_expired(self, now: float | None = None) -> None:
        moment = self._clock() if now is None else now
        for execution_id, issued_at in tuple(self._issued_at.items()):
            if moment - issued_at >= self._ttl_seconds:
                self._drop(execution_id)
        # 半途失败留下的"有方案没签发时间"的条目也清掉
        for execution_id in tuple(self._builds):
            if execution_id not in self._issued_at:
                self._drop(execution_id)

    def _drop(self, execution_id: str) -> None:
        self._builds.pop(execution_id, None)
        self._issued_at.pop(execution_id, None)
        self._players.pop(execution_id, None)


def describe_candidate(
    store: "BuildCandidateStore", player_name: str, execution_id: str
) -> dict:
    """按候选 ID 取回签发的那份方案（只读、不焚烧），转成工具层要的 dict。

    话术在这儿而不在服务里：`expired` 与 `unknown` 是**两件事**
    （"这次确认超时了，重新确认一次" ≠ "这不是你的候选"），调用方按它给下一步。
    """
    build, status = store.resolve(execution_id, player_name)
    if status == "expired":
        return {
            "success": False,
            "code": "expired_execution_id",
            "message": "该配装候选已过期，请重新求解并确认。",
        }
    if build is None:
        return {
            "success": False,
            "code": "unknown_execution_id",
            "message": "该配装候选已失效或不属于当前玩家，请重新求解并确认。",
        }
    return {"success": True, "build": build.model_dump(mode="json")}

