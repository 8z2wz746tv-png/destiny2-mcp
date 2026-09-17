"""上游 HTTP 错误 → 领域错误的**唯一翻译层**（aiobungie 抛出的异常只在这里被解释）。

为什么单独一个模块：`bungie_client.py` 贴着 1263 行的体量上限，而"统计接口要新增一个
端点"这件事没有别的地方可放。这一层与客户端实例无关（纯函数 + 异常类型），抽出来既给
客户端腾出了位置，也让"哪些上游错误会被翻译、翻成什么码"只有一处可查：
`tests/test_upstream_error_mapping.py` 钉的就是这里的行为。

调用方（`bungie_client`）沿用小写下划线名字，避免把 30 多处调用点跟着改名 —— 这些名字
在本包内仍然是"内部管道"，不是对外 API。

三条口径（真机踩出来的）：

1. **404 单独成码**：`UpstreamNotFoundError`（`upstream_not_found_error`）——
   客户端要靠它决定"改 ID"还是"稍后重试"，混进 `a_p_i_error` 就分不清了；
2. **503 / SystemDisabled**：`BungieServiceUnavailableError`，消息明说是上游维护/限流；
3. **其它 4xx**：`APIError`，消息带 HTTP 状态与 Bungie 原文，并写明"这不是你的账号问题"。

另有两个只给写路径用的小工具：`_bungie_unavailable_result`（把 503 变成
`{"ErrorCode": 503, ...}` 让调用方自己决定回退）、`_http_error_code`（业务错误码，
与 HTTP 状态码不是一回事 —— 1653 = DestinyPGCRNotFound 而 http_status 才是 404）。
"""

from __future__ import annotations

from typing import NoReturn

import aiobungie

from .exceptions import APIError, BungieServiceUnavailableError, UpstreamNotFoundError
from .logging_config import get_logger

logger = get_logger(__name__)


def _is_insufficient_privileges(exc: aiobungie.HTTPError) -> bool:
    """Return whether Bungie rejected a request due to missing OAuth scope."""
    error_status = str(getattr(exc, "error_status", "") or "")
    if error_status == "InsufficientPrivileges":
        return True
    return "InsufficientPrivileges" in str(exc)


def _is_bungie_service_unavailable(exc: aiobungie.HTTPError) -> bool:
    """Return whether Bungie is temporarily unavailable or under maintenance."""
    http_status = getattr(exc, "http_status", None)
    try:
        if int(http_status) == 503:
            return True
    except (TypeError, ValueError):
        pass

    status_parts = [
        str(getattr(exc, "error_status", "") or ""),
        str(getattr(exc, "message", "") or ""),
        str(exc),
    ]
    return any(
        marker in part
        for part in status_parts
        for marker in ("SystemDisabled", "ServiceUnavailable", "Serviceunavailable")
    )


def _raise_bungie_unavailable(exc: aiobungie.HTTPError, operation: str) -> None:
    """只在「Bungie 暂时不可用」时抛；其余情况返回，交给调用方决定（例如回退到基础组件）。"""
    if _is_bungie_service_unavailable(exc):
        logger.warning("Bungie unavailable during %s: %s", operation, exc)
        raise BungieServiceUnavailableError(operation) from exc


def _raise_bungie_error(exc: aiobungie.HTTPError, operation: str) -> NoReturn:
    """把上游 HTTP 错误**统一**翻译成领域错误 —— 绝不让裸异常冒到 MCP 客户端。

    以前只有 503 会被翻译，其它 4xx 直接 `raise`：客户端拿到的是
    `Error executing tool …: Notfound: (http_status: 404, error_status: DestinyPGCRNotFound…)`，
    没有 `ok`/`error.code`，因此分不清「这个 ID 查不到」和「服务坏了」（真机复现：
    `pgcr` 传一个数字但不存在的活动 ID；`clan_leaderboards` 传不存在的 group_id）。

    404 单独映射为 `UpstreamNotFoundError`（码 `upstream_not_found_error`），
    其余状态码归 `APIError`（码 `a_p_i_error`）并在消息里带上 HTTP 状态与 Bungie 原文。
    """
    _raise_bungie_unavailable(exc, operation)
    status = _http_status_of(exc)
    error_code = _http_error_code(exc)
    error_status = str(getattr(exc, "error_status", "") or "")
    message = str(getattr(exc, "message", "") or exc).strip()
    if status == 404:
        logger.warning("Upstream 404 during %s: %s", operation, exc)
        detail = f"Bungie 原文：{message}" if message else ""
        if error_status:
            detail = f"{detail}（{error_status}）" if detail else f"（{error_status}）"
        raise UpstreamNotFoundError(operation, detail) from exc
    logger.warning("Upstream HTTP %s (code=%s) during %s: %s", status, error_code, operation, exc)
    raise APIError(
        operation,
        f"Bungie 返回 HTTP {status or '未知状态'}"
        + (f"（{error_status}，code={error_code}）" if error_status or error_code else "")
        + (f"：{message}" if message else "。")
        + "这不是你的账号问题；若是临时故障可稍后重试，若是参数问题请检查 ID。",
    ) from exc


def _http_status_of(exc: aiobungie.HTTPError) -> int:
    """真正的 HTTP 状态码。

    不能拿 `error_code` 顶替：Bungie 的 `error_code` 是**业务错误码**
    （例如 1653 = DestinyPGCRNotFound），`aiobungie.error.NotFound` 的 error_code
    是 1653 而 http_status 才是 404 —— 按 error_code 判断会把 404 漏掉。
    """
    status = getattr(exc, "http_status", 0)
    return int(getattr(status, "value", status) or 0)


def _http_error_code(exc: aiobungie.HTTPError) -> int:
    code = getattr(exc, "error_code", None)
    if code:
        return int(code)
    status = getattr(exc, "http_status", 0)
    return int(getattr(status, "value", status) or 0)


def _bungie_unavailable_result(exc: aiobungie.HTTPError, operation: str) -> dict | None:
    if not _is_bungie_service_unavailable(exc):
        return None
    logger.warning("Bungie unavailable during %s: %s", operation, exc)
    return {
        "ErrorCode": 503,
        "Message": "Bungie 官方接口暂时不可用，可能正在维护或限流。请稍后重试。",
    }
