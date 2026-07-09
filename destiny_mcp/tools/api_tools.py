"""API tools — raw Bungie API fallback.

Provides a direct passthrough to Bungie API endpoints that aren't
covered by dedicated MCP tools. Inspired by d2-skill's d2-api command.
"""

from __future__ import annotations

import json

import aiobungie
from mcp.server.fastmcp import Context

from ..exceptions import AuthenticationError
from ..logging_config import get_logger
from ..server import mcp
from ._helpers import get_ctx, handle_tool_error, resolve_player_name

logger = get_logger(__name__)

# Allowed HTTP methods
_ALLOWED_METHODS = {"GET", "POST"}

# Blocked path prefixes (security: don't expose API key management)
_BLOCKED_PREFIXES = {"/App/", "App/"}

# Max response size to return (50KB)
_MAX_RESPONSE_CHARS = 50_000


@mcp.tool()
@handle_tool_error
async def raw_api_call(
    method: str = "GET",
    path: str = "",
    body: str | None = None,
    params: str | dict | None = None,
    player_name: str | None = None,
    ctx: Context = None,
) -> dict:
    """直接调用 Bungie API 任意端点（兜底工具）。

    Intentionally bypasses the service layer (Rule 1 exception):
    this is an escape hatch for endpoints not yet covered by dedicated tools.
    All validation/security lives here in the tool — there is no service to extract.

    当其他工具无法满足需求时使用此工具。适用于：
    - 查询活动历史、PGCR、统计数据
    - 查询公会信息
    - 查询未被其他工具覆盖的 Bungie API 端点

    path 是相对于 https://www.bungie.net/Platform/ 的路径。
    需要认证的端点会自动使用 OAuth token。

    Examples:
        raw_api_call("GET", "Destiny2/Milestones/")
        raw_api_call("GET", "Destiny2/Stats/PostGameCarnageReport/12345678901/")
        raw_api_call("GET", "Destiny2/3/Profile/4611686018467260756/Character/2305843009754046315/Stats/Activities/", params={"mode": "4", "count": "10"})
        raw_api_call("GET", "GroupV2/4611686018467260756/Members/")

    Args:
        method: HTTP method ("GET" or "POST").
        path: API path relative to /Platform/ (e.g. "Destiny2/Milestones/").
        body: JSON body string for POST requests (optional).
        params: Query params as JSON string or dict (optional, e.g. '{"count":"10"}' or {"count":"10"}).
        player_name: Bungie 名称。不填则使用默认玩家（用于需要认证的端点）。
    """
    svc = get_ctx(ctx)
    bungie = svc["bungie"]

    # Validate method
    method = method.upper()
    if method not in _ALLOWED_METHODS:
        return {"error": f"不支持的方法 '{method}'，只允许 GET 和 POST。"}

    # Validate path
    if not path:
        return {"error": "path 不能为空。请提供 API 路径，如 'Destiny2/Milestones/'。"}

    # Clean path
    path = path.lstrip("/")

    # Security check
    for blocked in _BLOCKED_PREFIXES:
        if path.startswith(blocked):
            return {"error": f"安全限制：不允许调用 {blocked} 端点。"}

    # Parse body and params
    parsed_body = None
    if body:
        try:
            parsed_body = json.loads(body)
        except json.JSONDecodeError:
            return {"error": f"body 不是有效的 JSON: {body[:200]}"}

    parsed_params = None
    if params:
        if isinstance(params, dict):
            parsed_params = params
        elif isinstance(params, str):
            try:
                parsed_params = json.loads(params)
            except json.JSONDecodeError:
                return {"error": f"params 不是有效的 JSON: {params[:200]}"}
        else:
            return {"error": f"params 类型不支持: {type(params)}"}

    # Get auth token if available
    auth_token = None
    try:
        auth_token = await bungie.get_access_token()
    except AuthenticationError:
        logger.debug("No auth token available for raw_api_call")

    # Make the request
    logger.info("raw_api_call: %s %s", method, path)
    try:
        result = await bungie.rest.static_request(
            method,
            path,
            auth=auth_token,
            json=parsed_body,
            params=parsed_params,
        )
    except aiobungie.HTTPError as e:
        logger.error("raw_api_call failed: %s %s → %s", method, path, e)
        return {"error": f"API 调用失败: {str(e)[:500]}"}

    # Truncate large responses
    result_str = json.dumps(result, ensure_ascii=False) if isinstance(result, (dict, list)) else str(result)
    if len(result_str) > _MAX_RESPONSE_CHARS:
        result = {"_truncated": True, "_original_size": len(result_str), "_preview": result_str[:_MAX_RESPONSE_CHARS]}

    return result
