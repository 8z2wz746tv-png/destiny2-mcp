"""Centralized logging configuration for Destiny MCP.

Uses stderr for all log output because stdout is reserved for the MCP JSON-RPC
protocol when running under stdio transport.
"""

from __future__ import annotations

import logging
import os
import re
import sys


class _RedactingFilter(logging.Filter):
    """Scrub sensitive tokens from log output.

    Replaces API keys, access tokens, and Bearer headers with [REDACTED]
    so they never appear in stderr / audit logs.
    """

    # Patterns ordered from most specific to least specific
    _PATTERNS: list[tuple[re.Pattern[str], str]] = [
        # Bearer tokens (e.g. "Bearer CI3HCBKGAgA...")
        (re.compile(r"Bearer\s+[A-Za-z0-9\-_\.]{20,}"), "Bearer [REDACTED]"),
        # Explicit API key assignments in logs (e.g. "X-API-Key: abc123...")
        (re.compile(r"(X-API-Key[=:\s]+)\S{20,}", re.IGNORECASE), r"\1[REDACTED]"),
        # "api_key=..." or "API_KEY=..." in env dumps
        (re.compile(r"(api_key|API_KEY|access_token|refresh_token)([=:\s]+)\S{20,}", re.IGNORECASE),
         r"\1\2[REDACTED]"),
        # Standalone 32-char hex strings that look like Bungie API keys
        (re.compile(r"\b[a-f0-9]{32}\b"), "[REDACTED_API_KEY]"),
    ]

    def filter(self, record: logging.LogRecord) -> bool:
        if record.msg and isinstance(record.msg, str):
            for pattern, replacement in self._PATTERNS:
                record.msg = pattern.sub(replacement, record.msg)
        if record.args:
            # Also scrub args that are strings (used in %s formatting)
            if isinstance(record.args, dict):
                record.args = {
                    k: self._scrub(v) if isinstance(v, str) else v
                    for k, v in record.args.items()
                }
            elif isinstance(record.args, tuple):
                record.args = tuple(
                    self._scrub(a) if isinstance(a, str) else a
                    for a in record.args
                )
        return True

    @staticmethod
    def _scrub(text: str) -> str:
        for pattern, replacement in _RedactingFilter._PATTERNS:
            text = pattern.sub(replacement, text)
        return text


def setup_logging(level: int = logging.INFO) -> None:
    """Configure root logger to write structured logs to stderr.

    Call once at application startup. Subsequent calls are no-ops.
    """
    root = logging.getLogger()
    if root.handlers:
        return  # Already configured

    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(
        logging.Formatter(
            "%(asctime)s [%(levelname)-7s] %(name)s | %(message)s",
            datefmt="%Y-%m-%dT%H:%M:%S",
        )
    )
    root.setLevel(level)
    root.addHandler(handler)
    root.addFilter(_RedactingFilter())

    # Silence noisy third-party loggers
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("aiobungie").setLevel(logging.WARNING)


def get_logger(name: str) -> logging.Logger:
    """Get a logger for the given module name."""
    return logging.getLogger(name)
