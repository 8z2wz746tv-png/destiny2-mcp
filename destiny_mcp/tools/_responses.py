"""Structured response helpers for MCP aggregate tools."""

from __future__ import annotations

from typing import Any


def ok_response(
    summary: str,
    data: dict[str, Any] | None = None,
    *,
    candidates: list[dict[str, Any]] | None = None,
    next_actions: list[dict[str, Any] | str] | None = None,
    warnings: list[str] | None = None,
) -> dict[str, Any]:
    """Build the success shape used by new aggregate MCP tools."""
    return {
        "ok": True,
        "summary": summary,
        "data": data or {},
        "candidates": candidates or [],
        "next_actions": next_actions or [],
        "warnings": warnings or [],
    }


def error_response(
    code: str,
    message: str,
    *,
    recoverable: bool = True,
    candidates: list[dict[str, Any]] | None = None,
    next_actions: list[dict[str, Any] | str] | None = None,
    warnings: list[str] | None = None,
) -> dict[str, Any]:
    """Build the error shape used by new aggregate MCP tools."""
    return {
        "ok": False,
        "error": {
            "code": code,
            "message": message,
            "recoverable": recoverable,
        },
        "candidates": candidates or [],
        "next_actions": next_actions or [],
        "warnings": warnings or [],
    }
