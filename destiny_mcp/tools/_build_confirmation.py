"""金装求解的解析与确认：无状态确认凭据 + "要不要先问一句"的判定。

搬出 `assistants.py` 的原因与其它 `_*_branches` 一样：那边贴着体积上限，而这段
（名字解析 → 精确匹配直接用 / 模糊匹配列候选 → 校验回传的凭据）本来就是一整件事。
"""

from __future__ import annotations

import hashlib
import hmac
import json
import secrets
import time
from collections.abc import Mapping
from typing import Any, NamedTuple

from .. import config
from ..error_codes import ErrorCode
from ._responses import error_response


_TOKEN_PREFIX = "d2bc1"
_TOKEN_TTL_SECONDS = 10 * 60
_MAX_TOKEN_LENGTH = 512
def _signing_key() -> bytes:
    config.validate_credentials()
    return hmac.new(
        config.BUNGIE_CLIENT_SECRET.encode("utf-8"),
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
    signature = hmac.new(_signing_key(), payload, hashlib.sha256).hexdigest()
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
    expected_signature = hmac.new(_signing_key(), payload, hashlib.sha256).digest()
    return hmac.compare_digest(supplied_signature, expected_signature)


class ExoticStep(NamedTuple):
    """解析结果：要么给一个错误信封，要么给"用来求解的规范名 + 给调用方看的说明"。"""

    exotic_name: str = ""
    # 唯一精确匹配时填：写进响应的 query.exotic_resolution，说明用的是哪件、为什么不确认
    resolution: dict[str, Any] | None = None
    error: dict[str, Any] | None = None


def resolve_exotic(
    svc: Any,
    *,
    exotic_name: str,
    character: str,
    player_name: str,
    build_arguments: Mapping[str, Any],
    confirmed_exotic_hash: int | None,
) -> ExoticStep:
    """金装名字 → 用来求解的规范名（必要时先让玩家确认）。

    **只有"要挑一件"时才停下来问**：解析器把唯一精确匹配单列为 `exact`（`matches` 只有一条），
    那种情况没有选择可做，直接用它求解（2026-09-23 用户拍板，见 ADR-017）。
    模糊/多个命中仍然返回 `exotic_confirmation_required` + 每个候选的 arguments 与凭据。
    """
    resolution = svc["build_svc"].resolve_exotic_armor(exotic_name, character, limit=5)
    status = resolution.get("status")
    matches = resolution.get("matches") or []

    if status == "confirmation_required" and confirmed_exotic_hash is None:
        candidates = []
        for index, match in enumerate(matches, 1):
            arguments = dict(build_arguments)
            arguments["exotic_name"] = match.get("name")
            arguments["confirmed_exotic_hash"] = match.get("item_hash")
            arguments["exotic_confirmation_token"] = issue_exotic_confirmation_token(
                arguments,
                user_id=None,
                player_name=player_name,
            )
            candidates.append({
                "selection": index,
                "name": match.get("name"),
                "name_en": match.get("name_en"),
                "item_hash": match.get("item_hash"),
                "icon_url": match.get("icon_url"),
                "character": match.get("character") or character,
                "arguments": arguments,
            })
        return ExoticStep(error=error_response(
            ErrorCode.EXOTIC_CONFIRMATION_REQUIRED,
            f"“{exotic_name}”匹配到以下金装。请确认你指的是哪件。"
            "确认后我会保留原来的职业、属性目标、优先级和碎片设置继续配装。",
            candidates=candidates,
        ))

    if status == "not_found":
        return ExoticStep(error=error_response(
            ErrorCode.EXOTIC_NOT_FOUND,
            f"没有找到与“{exotic_name}”匹配的{character or '目标职业'}金装。"
            "请换一个更短或更完整的名称后重试，原属性目标不会被降低。",
        ))

    if status not in {"exact", "confirmation_required"}:
        return ExoticStep(error=error_response(
            ErrorCode.EXOTIC_RESOLUTION_FAILED,
            "金装名称解析失败，未启动配装求解。请稍后重试。",
        ))

    if confirmed_exotic_hash is None:
        # 只剩 exact：名字唯一定中一件，没有"选哪件"的问题。把用的是哪件写进响应，
        # 让调用方（和玩家）看得见，而不是默默换了一件。
        exact_match = matches[0] if matches else {}
        resolved_name = resolution.get("canonical_name") or exotic_name
        return ExoticStep(
            exotic_name=resolved_name,
            resolution={
                "status": "exact_match",
                "name": resolved_name,
                "name_en": exact_match.get("name_en"),
                "item_hash": exact_match.get("item_hash"),
                "note": "名字唯一精确匹配，已直接用它求解；写入仍要玩家确认后传 confirmed=true。",
            },
        )

    confirmed_match = next(
        (
            match
            for match in matches
            if int(match.get("item_hash") or 0) == confirmed_exotic_hash
        ),
        None,
    )
    if confirmed_match is None:
        return ExoticStep(error=error_response(
            ErrorCode.INVALID_EXOTIC_CONFIRMATION,
            "金装确认信息无效或已与当前候选不一致，未启动配装求解。"
            "请重新搜索并让玩家确认候选。",
        ))
    resolved_name = confirmed_match.get("name") or resolution.get("canonical_name")
    if not resolved_name:
        return ExoticStep(error=error_response(
            ErrorCode.EXOTIC_RESOLUTION_FAILED,
            "金装名称解析失败，未启动配装求解。请稍后重试。",
        ))
    return ExoticStep(exotic_name=resolved_name)
