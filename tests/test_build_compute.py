"""配装求解的时间预算：排队不该算在预算里，预算要能配。

原来的写法是 `fail_after(预算)` 套在 `to_process.run_sync(limiter=...)` 外面，
于是**排队等待也吃预算**：两个求解同时进来，第二个还在排队就把预算耗光，
报的还是"缩小请求"这种与真实原因无关的建议。这里的测试用假任务把这个语义钉住。
"""

from __future__ import annotations

import asyncio
import time

import pytest

from destiny_mcp import config
from destiny_mcp.exceptions import BuildValidationError
from destiny_mcp.services.build_compute import BuildCompute


def _sleep(seconds: float) -> float:
    """必须放在模块顶层：worker 进程要能按名字重新导入它。"""
    time.sleep(seconds)
    return seconds


async def test_queue_wait_is_not_charged_to_the_budget() -> None:
    """第二个任务要排队 2 秒，但它自己只算 1 秒 —— 预算 3 秒，两个都该成功。"""
    compute = BuildCompute(timeout_seconds=3)

    results = await asyncio.gather(compute.run(_sleep, 2), compute.run(_sleep, 1))

    assert results == [2, 1]


async def test_slow_computation_still_times_out() -> None:
    """真正的计算超时还是要报，并说清预算和怎么办。"""
    compute = BuildCompute(timeout_seconds=1)

    with pytest.raises(BuildValidationError) as excinfo:
        await compute.run(_sleep, 5)

    message = str(excinfo.value)
    assert "1s budget" in message
    assert "DESTINY_BUILD_TIMEOUT_SECONDS" in message


def test_budget_defaults_to_config_and_is_overridable(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(config, "BUILD_TIMEOUT_SECONDS", 42.0)

    assert BuildCompute()._timeout_seconds == 42.0
    assert BuildCompute(timeout_seconds=7)._timeout_seconds == 7
