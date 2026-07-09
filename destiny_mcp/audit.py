"""Audit logger — records every MCP tool call to disk.

Inspired by d2-skill's audit trail design. Each tool invocation is logged
to ~/.destiny_mcp/audit/YYYYMMDD/HHMMSS-<tool_name>.json for debugging
and traceability.
"""

from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .logging_config import get_logger

logger = get_logger(__name__)

_DEFAULT_AUDIT_DIR = Path.home() / ".destiny_mcp" / "audit"
_MAX_RESULT_CHARS = 50_000  # Truncate large results


class AuditLogger:
    """Logs every MCP tool call to a JSON file on disk."""

    def __init__(self, base_dir: Path | None = None) -> None:
        self._base_dir = base_dir or _DEFAULT_AUDIT_DIR

    def log(
        self,
        tool_name: str,
        arguments: dict[str, Any],
        result: Any = None,
        duration_ms: float = 0,
        error: str | None = None,
    ) -> None:
        """Write an audit record for a tool invocation.

        Args:
            tool_name: Name of the MCP tool.
            arguments: Arguments passed to the tool.
            result: Tool result (will be summarized if too large).
            duration_ms: Execution time in milliseconds.
            error: Error message if the call failed.
        """
        try:
            now = datetime.now(timezone.utc)
            day_dir = self._base_dir / now.strftime("%Y%m%d")
            day_dir.mkdir(parents=True, exist_ok=True)

            # Build result summary (truncate if too large)
            result_summary = self._summarize_result(result)

            filename = f"{now.strftime('%H%M%S')}-{tool_name}.json"
            record = {
                "timestamp": now.isoformat(),
                "tool": tool_name,
                "arguments": arguments,
                "duration_ms": round(duration_ms, 1),
                "result_summary": result_summary,
                "error": error,
            }

            filepath = day_dir / filename
            filepath.write_text(
                json.dumps(record, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            logger.debug("Audit: %s (%.0fms) → %s", tool_name, duration_ms, filepath)
        except (OSError, TypeError):
            # Audit logging should never break the main flow
            logger.warning("Failed to write audit log for %s", tool_name, exc_info=True)

    @staticmethod
    def _summarize_result(result: Any) -> str:
        """Convert result to a string summary, truncating if necessary."""
        if result is None:
            return ""

        if isinstance(result, str):
            text = result
        elif isinstance(result, dict):
            text = json.dumps(result, ensure_ascii=False)
        else:
            text = str(result)

        if len(text) > _MAX_RESULT_CHARS:
            return text[:_MAX_RESULT_CHARS] + f"\n... [truncated, {len(text)} chars total]"
        return text
