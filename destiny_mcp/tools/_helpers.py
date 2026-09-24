"""Shared helpers for MCP tool modules."""

from __future__ import annotations

import functools
from collections.abc import Callable
from typing import Any, cast

from mcp.server.fastmcp import Context

from .. import config
from ..exceptions import DestinyMCPError
from ..logging_config import get_logger
from ..error_codes import code_for_exception
from ..service_context import ServiceContext
from ..error_codes import ErrorCode
from ._responses import error_response

logger = get_logger(__name__)


_CURRENT_OAUTH_PLAYER = "__destiny_current_oauth_player__"


def resolve_player_name(*args: Any) -> str:
    """Return an explicit player, configured default, or current OAuth player.

    Some legacy tools call this as resolve_player_name(player_name), while
    newer aggregate tools used resolve_player_name(ctx, player_name). Accepting
    both keeps full/expert profiles compatible during the tool-layer rewrite.
    """
    if len(args) == 1:
        player_name = args[0]
    elif len(args) == 2:
        player_name = args[1]
    else:
        player_name = None

    if player_name:
        return player_name
    default = config.DESTINY_DEFAULT_PLAYER
    return default or _CURRENT_OAUTH_PLAYER


def get_ctx(ctx: Context) -> ServiceContext:
    """Extract services from lifespan context.

    纯个人版：直接返回 lifespan context，不走用户身份解析。
    """
    return cast(ServiceContext, ctx.request_context.lifespan_context)


def handle_tool_error(func: Callable) -> Callable:
    """Decorator that catches DestinyMCPError and returns a user-friendly message.

    Use on MCP tool functions so domain errors don't leak raw tracebacks to the LLM.
    """
    @functools.wraps(func)
    async def wrapper(*args: Any, **kwargs: Any) -> Any:
        try:
            return await func(*args, **kwargs)
        except DestinyMCPError as e:
            logger.warning("Tool %s error: %s", func.__name__, e)
            if func.__name__.endswith("_assistant"):
                # 异常类名 → code 的推导只有一处（error_codes.code_for_exception）
                return error_response(code_for_exception(e), str(e))
            return f"⚠️ {e}"
        except TimeoutError as e:
            # aiohttp/asyncio 的超时在 3.11+ 就是内置 TimeoutError，而且**没有 message**：
            # 以前它直接冒到框架层，调用方只看到一句 `Error executing tool …: `（空的），
            # 分不清是网络慢、上游挂了、还是自己参数写错（真机配装时踩到）。
            logger.warning("Tool %s 超时: %s", func.__name__, e)
            return error_response(
                ErrorCode.API_ERROR,
                "访问 Bungie 超时（网络慢或上游没响应），这不是参数问题：稍后重试即可。",
            )
        except Exception:
            logger.exception("Tool %s unexpected error", func.__name__)
            raise
    return wrapper


def positive_or_default(value: int | None, default: int) -> int:
    """条数类参数的哨兵规则：`None` 或 **≤0** 都算"没指定" → 该入口的默认值。

    为什么把 0 与负数也算"没指定"：`_param_docs.Limit` 一直这么承诺
    （"传 0 或负数等于没指定"），但代码只判 `None` —— 传 0 会一路走到服务层的
    `max(1, min(...))`，静默变成"要 1 条/1 场"。这类"文档承诺了、代码没做"的差距
    正是这个仓库最不能留的东西，所以 count/limit/maxtop/top_n/max_replacements/
    slot_number 全部走这一个函数：规则只有一处，改也只改这里。
    """
    if value is None or value <= 0:
        return default
    return value

