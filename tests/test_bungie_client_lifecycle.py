from __future__ import annotations

import asyncio
import json
import stat
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from destiny_mcp.bungie_client import BungieClient
from destiny_mcp.exceptions import AuthenticationError


async def test_expired_token_refresh_is_single_flight() -> None:
    client = BungieClient()
    client._access_token = "expired"
    client._refresh_token = "refresh"
    client._token_expires_at = 0
    refresh_count = 0

    async def refresh() -> bool:
        nonlocal refresh_count
        refresh_count += 1
        await asyncio.sleep(0)
        client._access_token = "fresh"
        client._token_expires_at = 9_999_999_999
        return True

    client._refresh_access_token = refresh

    first, second = await asyncio.gather(
        client.get_access_token(),
        client.get_access_token(),
    )

    assert (first, second) == ("fresh", "fresh")
    assert refresh_count == 1


async def test_loading_tokens_repairs_private_file_mode(tmp_path: Path) -> None:
    token_path = tmp_path / "tokens.json"
    token_path.write_text(
        json.dumps({
            "access_token": "access",
            "refresh_token": "refresh",
            "expires_at": 9_999_999_999,
        }),
        encoding="utf-8",
    )
    token_path.chmod(0o644)
    client = BungieClient(token_dir=tmp_path)

    assert await client._load_tokens() is True
    assert stat.S_IMODE(token_path.stat().st_mode) == 0o600


async def test_start_closes_partially_open_client_when_authentication_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    rest = MagicMock()
    rest.close = AsyncMock()
    monkeypatch.setattr(
        "destiny_mcp.bungie_client.aiobungie.RESTClient",
        lambda *_args, **_kwargs: rest,
    )
    client = BungieClient()
    client._load_tokens = AsyncMock(return_value=False)

    with pytest.raises(AuthenticationError, match="No valid Destiny OAuth tokens"):
        await client.start()

    rest.open.assert_called_once_with()
    rest.close.assert_awaited_once_with()

    await client.close()
    rest.close.assert_awaited_once_with()


def test_saving_tokens_creates_private_atomic_file(tmp_path: Path) -> None:
    client = BungieClient(token_dir=tmp_path / "nested")
    client._access_token = "access"
    client._refresh_token = "refresh"
    client._token_expires_at = 123.0

    client._save_tokens()

    token_path = tmp_path / "nested" / "tokens.json"
    assert json.loads(token_path.read_text(encoding="utf-8"))["access_token"] == "access"
    assert stat.S_IMODE(token_path.stat().st_mode) == 0o600
