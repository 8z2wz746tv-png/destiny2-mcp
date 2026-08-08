"""Stateless confirmation tokens for exact exotic build requests."""

from __future__ import annotations

import hashlib
import hmac
import json
import secrets
import time
from collections.abc import Mapping
from typing import Any

from ..config import BUNGIE_CLIENT_SECRET


_TOKEN_PREFIX = "d2bc1"
_TOKEN_TTL_SECONDS = 10 * 60
_MAX_TOKEN_LENGTH = 512
_SIGNING_KEY = hmac.new(
    BUNGIE_CLIENT_SECRET.encode("utf-8"),
    b"destiny-mcp/exotic-build-confirmation/v1",
    hashlib.sha256,
).digest()


def _canonical_json(value: Any) -> str:
    return json.dumps(
        value,
        allow_nan=False,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    )


def _signed_payload(
    request_arguments: Mapping[str, Any],
    *,
    user_id: str | None,
    player_name: str | None,
    expires_at: int,
    nonce: str,
) -> bytes:
    return _canonical_json(
        {
            "expires_at": expires_at,
            "nonce": nonce,
            "principal": {
                "player_name": str(player_name or ""),
                "user_id": str(user_id or ""),
            },
            "request": dict(request_arguments),
        }
    ).encode("ascii")


def issue_exotic_confirmation_token(
    request_arguments: Mapping[str, Any],
    *,
    player_name: str | None,
    user_id: str | None = None,
) -> str:
    """Sign one exact candidate and its complete build request."""
    expires_at = int(time.time()) + _TOKEN_TTL_SECONDS
    nonce = secrets.token_urlsafe(12)
    payload = _signed_payload(
        request_arguments,
        user_id=user_id,
        player_name=player_name,
        expires_at=expires_at,
        nonce=nonce,
    )
    signature = hmac.new(_SIGNING_KEY, payload, hashlib.sha256).hexdigest()
    return f"{_TOKEN_PREFIX}.{expires_at}.{nonce}.{signature}"


def verify_exotic_confirmation_token(
    token: str | None,
    request_arguments: Mapping[str, Any],
    *,
    player_name: str | None,
    user_id: str | None = None,
) -> bool:
    """Verify signature, expiry, principal scope, and exact request contents."""
    if not isinstance(token, str) or not token or len(token) > _MAX_TOKEN_LENGTH:
        return False
    try:
        prefix, raw_expires_at, nonce, raw_signature = token.split(".")
        expires_at = int(raw_expires_at)
        supplied_signature = bytes.fromhex(raw_signature)
    except (TypeError, ValueError):
        return False
    if prefix != _TOKEN_PREFIX or raw_expires_at != str(expires_at):
        return False
    if (
        not nonce
        or len(nonce) > 64
        or raw_signature != supplied_signature.hex()
        or len(supplied_signature) != hashlib.sha256().digest_size
        or expires_at <= int(time.time())
    ):
        return False

    try:
        payload = _signed_payload(
            request_arguments,
            user_id=user_id,
            player_name=player_name,
            expires_at=expires_at,
            nonce=nonce,
        )
    except (TypeError, ValueError, UnicodeError):
        return False
    expected_signature = hmac.new(_SIGNING_KEY, payload, hashlib.sha256).digest()
    return hmac.compare_digest(supplied_signature, expected_signature)
