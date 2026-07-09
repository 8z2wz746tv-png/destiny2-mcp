"""Web fetch utility — shared HTTP client for fetching arbitrary URLs.

Centralizes timeout, user-agent, and logging so all external HTTP
requests go through one place (Rule 3).
"""

from __future__ import annotations

import httpx

from ..logging_config import get_logger

logger = get_logger(__name__)

_DEFAULT_TIMEOUT = 30
_DEFAULT_USER_AGENT = "Mozilla/5.0 (compatible; DestinyMCP/1.0)"


async def fetch_url(
    url: str,
    *,
    timeout: float = _DEFAULT_TIMEOUT,
    user_agent: str = _DEFAULT_USER_AGENT,
) -> httpx.Response:
    """Fetch a URL with standard headers and error handling.

    Args:
        url: The URL to fetch.
        timeout: Request timeout in seconds.
        user_agent: User-Agent header value.

    Returns:
        httpx.Response on success.

    Raises:
        httpx.HTTPStatusError: On non-2xx status codes.
        httpx.RequestError: On connection/timeout errors.
    """
    logger.debug("Fetching URL: %s", url)
    async with httpx.AsyncClient(
        timeout=timeout,
        follow_redirects=True,
        headers={"User-Agent": user_agent},
    ) as client:
        resp = await client.get(url)
        resp.raise_for_status()
    logger.debug("Fetched %s — %d bytes, status %d", url, len(resp.text), resp.status_code)
    return resp
