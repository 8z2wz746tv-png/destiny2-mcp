"""Bounded, cancellable CPU work, without blocking the MCP event loop."""

from functools import partial
from typing import Callable, TypeVar

import anyio
from anyio import to_process

from ..exceptions import BuildValidationError

_T = TypeVar("_T")


class BuildCompute:
    def __init__(self, timeout_seconds: float = 60) -> None:
        self._timeout_seconds = timeout_seconds
        self._limiter = anyio.CapacityLimiter(1)

    async def run(self, function: Callable[..., _T], *args, **kwargs) -> _T:
        try:
            # AnyIO terminates the worker on cancellation, not just its awaiting future.
            with anyio.fail_after(self._timeout_seconds):
                return await to_process.run_sync(
                    partial(function, *args, **kwargs),
                    cancellable=True,
                    limiter=self._limiter,
                )
        except TimeoutError as exc:
            raise BuildValidationError(
                "Build computation timed out; narrow the request and try again."
            ) from exc
