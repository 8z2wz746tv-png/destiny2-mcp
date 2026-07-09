"""Shared helpers for MCP tool modules."""

from __future__ import annotations

import functools
from collections.abc import Callable
from typing import Any

from mcp.server.fastmcp import Context

from .. import config
from ..exceptions import ConfigError, DestinyMCPError
from ..logging_config import get_logger

logger = get_logger(__name__)


def resolve_player_name(player_name: str | None) -> str:
    """Return the given player_name, or fall back to DESTINY_DEFAULT_PLAYER env var."""
    if player_name:
        return player_name
    default = config.DESTINY_DEFAULT_PLAYER
    if not default:
        raise ConfigError(
            "No player_name provided and DESTINY_DEFAULT_PLAYER is not set. "
            "Either pass player_name or set DESTINY_DEFAULT_PLAYER in .env."
        )
    return default


def get_ctx(ctx: Context) -> dict:
    """Extract services from lifespan context.

    纯个人版：直接返回 lifespan context，不走用户身份解析。
    """
    return ctx.request_context.lifespan_context


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
            return f"⚠️ {e}"
        except Exception:
            logger.exception("Tool %s unexpected error", func.__name__)
            raise
    return wrapper
