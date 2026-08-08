#!/usr/bin/env python3
"""Verify Destiny MCP configuration and perform a real stdio handshake."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import stat
import sys
from pathlib import Path
from urllib.parse import urlparse

from dotenv import dotenv_values
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


EXPECTED_TOOLS = {
    "player_assistant",
    "inventory_assistant",
    "weapon_assistant",
    "build_assistant",
    "loadout_assistant",
    "subclass_assistant",
    "activity_assistant",
    "world_assistant",
}
REQUIRED_CREDENTIALS = (
    "BUNGIE_API_KEY",
    "BUNGIE_CLIENT_ID",
    "BUNGIE_CLIENT_SECRET",
)


def _default_root() -> Path:
    configured_root = os.getenv("DESTINY_MCP_ROOT")
    if configured_root:
        return Path(configured_root).expanduser().resolve()

    cwd = Path.cwd().resolve()
    if (cwd / ".env").is_file() or (cwd / "destiny_mcp").is_dir():
        return cwd

    for parent in Path(__file__).resolve().parents:
        if (parent / "pyproject.toml").is_file() and (parent / "destiny_mcp").is_dir():
            return parent
    return cwd


def _preflight(root: Path) -> Path:
    errors: list[str] = []
    env_path = root / ".env"
    command = root / ".venv" / "bin" / "destiny-mcp"

    if not env_path.is_file():
        raise RuntimeError(f"Missing environment file: {env_path}")
    if not command.is_file():
        raise RuntimeError(f"Missing MCP executable: {command}")

    values = {
        key: str(value).strip() if value is not None else ""
        for key, value in dotenv_values(env_path).items()
    }
    for key in REQUIRED_CREDENTIALS:
        status = "set" if values.get(key) else "missing"
        print(f"{key}={status}")
        if status == "missing":
            errors.append(f"{key} is missing")

    client_id = values.get("BUNGIE_CLIENT_ID", "")
    if client_id and not client_id.isdigit():
        errors.append("BUNGIE_CLIENT_ID must be numeric")

    redirect_uri = values.get("DESTINY_OAUTH_REDIRECT_URI", "")
    parsed = urlparse(redirect_uri)
    redirect_valid = (
        parsed.scheme == "https"
        and parsed.hostname == "localhost"
        and parsed.port is not None
        and parsed.path == "/callback"
    )
    print(f"DESTINY_OAUTH_REDIRECT_URI={'valid' if redirect_valid else 'invalid'}")
    if not redirect_valid:
        errors.append(
            "DESTINY_OAUTH_REDIRECT_URI must match "
            "https://localhost:<port>/callback"
        )

    token_dir = Path(values.get("DESTINY_TOKEN_PATH") or "~/.destiny_mcp").expanduser()
    token_path = token_dir / "tokens.json"
    if not token_path.is_file():
        errors.append(f"OAuth token file is missing: {token_path}")
    else:
        try:
            token_data = json.loads(token_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            errors.append(f"OAuth token file is unreadable: {type(exc).__name__}")
        else:
            has_access = bool(token_data.get("access_token"))
            has_refresh = bool(token_data.get("refresh_token"))
            print(f"OAUTH_ACCESS_TOKEN={'set' if has_access else 'missing'}")
            print(f"OAUTH_REFRESH_TOKEN={'set' if has_refresh else 'missing'}")
            if not has_access or not has_refresh:
                errors.append("OAuth token file lacks access or refresh token data")

        mode = stat.S_IMODE(token_path.stat().st_mode)
        print(f"OAUTH_TOKEN_PERMISSIONS={mode:04o}")
        if mode & 0o077:
            errors.append("OAuth token file must not be accessible by group or others")

    if errors:
        raise RuntimeError("; ".join(errors))
    return command


def _tool_payload(result: object) -> dict:
    structured = getattr(result, "structuredContent", None)
    if isinstance(structured, dict) and structured:
        return structured

    for block in getattr(result, "content", []):
        text = getattr(block, "text", None)
        if not isinstance(text, str):
            continue
        try:
            payload = json.loads(text)
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict):
            return payload
    raise RuntimeError("MCP tool response did not contain a JSON object")


async def _verify(root: Path, command: Path, timeout: float) -> None:
    params = StdioServerParameters(
        command=str(command),
        args=[],
        env={
            "DESTINY_MCP_ROOT": str(root),
            "DESTINY_MCP_TOOL_PROFILE": "normal",
        },
    )
    async with asyncio.timeout(timeout):
        async with stdio_client(params) as streams:
            async with ClientSession(*streams) as session:
                await session.initialize()
                profile_result = await session.call_tool(
                    "player_assistant", {"intent": "profile"}
                )
                if getattr(profile_result, "isError", False):
                    raise RuntimeError("player_assistant returned an MCP protocol error")
                profile_payload = _tool_payload(profile_result)
                if profile_payload.get("ok") is not True:
                    error = profile_payload.get("error") or {}
                    code = error.get("code", "unknown") if isinstance(error, dict) else "unknown"
                    raise RuntimeError(f"Bungie profile check failed: {code}")
                tools_result = await session.list_tools()

    print("BUNGIE_PROFILE_CHECK=ok")
    actual = {tool.name for tool in tools_result.tools}
    missing = sorted(EXPECTED_TOOLS - actual)
    unexpected = sorted(actual - EXPECTED_TOOLS)
    print(f"MCP_TOOL_COUNT={len(actual)}")
    print("MCP_TOOLS=" + ",".join(sorted(actual)))
    if missing or unexpected:
        details = []
        if missing:
            details.append("missing=" + ",".join(missing))
        if unexpected:
            details.append("unexpected=" + ",".join(unexpected))
        raise RuntimeError("Normal tool profile mismatch: " + "; ".join(details))


def _exception_summary(exc: BaseException) -> str:
    if isinstance(exc, BaseExceptionGroup):
        return " | ".join(_exception_summary(child) for child in exc.exceptions)
    return f"{type(exc).__name__}: {exc}"


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Validate Destiny MCP credentials, OAuth token, and stdio tools."
    )
    parser.add_argument("--root", type=Path, default=_default_root())
    parser.add_argument("--timeout", type=float, default=90.0)
    args = parser.parse_args()
    root = args.root.expanduser().resolve()

    try:
        command = _preflight(root)
        asyncio.run(_verify(root, command, args.timeout))
    except Exception as exc:
        print(f"VERIFY_FAILED={_exception_summary(exc)}", file=sys.stderr)
        return 1

    print("VERIFY_OK=Destiny MCP is ready")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
