"""Per-account serialization for Bungie mutations."""

from __future__ import annotations

import asyncio
import weakref
from functools import wraps
from typing import Any, Awaitable, Callable, TypeVar

from ..bungie_client import BungieClient
from ..exceptions import DestinyMCPError

_T = TypeVar("_T")


class ReentrantAsyncLock:
    """An asyncio lock that allows nested service calls in one task."""

    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._owner: asyncio.Task | None = None
        self._depth = 0

    async def __aenter__(self) -> "ReentrantAsyncLock":
        task = asyncio.current_task()
        if task is not None and self._owner is task:
            self._depth += 1
            return self
        await self._lock.acquire()
        self._owner = task
        self._depth = 1
        return self

    async def __aexit__(self, exc_type, exc, traceback) -> None:
        task = asyncio.current_task()
        if self._owner is not task:
            raise DestinyMCPError("账号操作锁由非持有任务释放，已拒绝继续执行。")
        self._depth -= 1
        if self._depth == 0:
            self._owner = None
            self._lock.release()


_LOCKS: weakref.WeakKeyDictionary[object, ReentrantAsyncLock] = weakref.WeakKeyDictionary()


def account_action_lock(bungie: BungieClient) -> ReentrantAsyncLock:
    """Return the mutation lock shared by all services using one client."""
    lock = _LOCKS.get(bungie)
    if lock is None:
        lock = ReentrantAsyncLock()
        _LOCKS[bungie] = lock
    return lock


def serialized_account_action(
    func: Callable[..., Awaitable[_T]],
) -> Callable[..., Awaitable[_T]]:
    """Serialize a service method using its shared account action lock."""

    @wraps(func)
    async def wrapped(self: Any, *args: Any, **kwargs: Any) -> _T:
        async with self._account_action_lock:
            return await func(self, *args, **kwargs)

    return wrapped
