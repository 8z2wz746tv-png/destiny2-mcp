"""Shared helpers for MCP tool modules."""

from __future__ import annotations

import functools
import re
from collections.abc import Callable
from typing import Any, cast

from mcp.server.fastmcp import Context

from .. import config
from ..exceptions import DestinyMCPError
from ..logging_config import get_logger
from ..service_context import ServiceContext
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
                code = re.sub(
                    r"(?<!^)(?=[A-Z])", "_", type(e).__name__
                ).lower()
                return error_response(code, str(e))
            return f"⚠️ {e}"
        except Exception:
            logger.exception("Tool %s unexpected error", func.__name__)
            raise
    return wrapper
