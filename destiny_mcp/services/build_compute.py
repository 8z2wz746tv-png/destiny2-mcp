"""Bounded, cancellable CPU work, without blocking the MCP event loop."""

from functools import partial
from typing import Callable, TypeVar

import anyio
from anyio import to_process
from anyio._core._exceptions import BrokenWorkerProcess

from .. import config
from ..exceptions import BuildValidationError

_T = TypeVar("_T")


class BuildCompute:
    """每个实例同时只跑一个求解；超时只算「真正在计算」的时间。

    以前 `fail_after` 套在 `run_sync(limiter=...)` 外面，**排队等待也算进预算**：
    两个请求同时进来时，第二个还在排队就把 60 秒耗光，报的还是"缩小请求"这种
    与真实原因无关的建议。现在先拿限流器（排队不设时限），再给计算计时。
    """

    def __init__(self, timeout_seconds: float | None = None) -> None:
        self._timeout_seconds = (
            config.BUILD_TIMEOUT_SECONDS if timeout_seconds is None else timeout_seconds
        )
        self._limiter = anyio.CapacityLimiter(1)

    async def run(self, function: Callable[..., _T], *args, **kwargs) -> _T:
        # 排队：不设时限。run_sync 不再传 limiter —— 这里已经串行化了，
        # 传同一个限流器会二次获取而死锁。
        async with self._limiter:
            try:
                # AnyIO terminates the worker on cancellation, not just its awaiting future.
                with anyio.fail_after(self._timeout_seconds):
                    return await to_process.run_sync(
                        partial(function, *args, **kwargs),
                        cancellable=True,
                    )
            except TimeoutError as exc:
                raise BuildValidationError(
                    f"Build computation exceeded the {self._timeout_seconds:.0f}s budget; "
                    "narrow the request (fewer stat targets or a specific replacement slot) "
                    "or raise DESTINY_BUILD_TIMEOUT_SECONDS."
                ) from exc
            except BrokenWorkerProcess as exc:
                # worker 起不来时以前是裸抛：客户端只看到 anyio 的原始异常，看不出原因。
                # 最常见的原因就是启动方式 —— anyio 的 worker 会按路径重跑父进程主模块，
                # 而 `python -m destiny_mcp.server` 的主模块含相对导入，重跑会 ImportError。
                raise BuildValidationError(
                    "配装求值的 worker 进程启动失败，本次没有计算。"
                    "如果你是用 `python -m destiny_mcp.server` 启动服务的，请改用"
                    "`python -m destiny_mcp` 或控制台脚本 `destiny-mcp`（前者同样支持 -m）。"
                    "若仍失败，请把这条消息连同启动命令一起反馈。"
                ) from exc
