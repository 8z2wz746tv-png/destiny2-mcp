"""Audit logger — records every MCP tool call to disk.

Inspired by d2-skill's audit trail design. Each tool invocation is logged
to ~/.destiny_mcp/audit/YYYYMMDD/HHMMSS-<tool_name>.json for debugging
and traceability.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .logging_config import get_logger

logger = get_logger(__name__)

_DEFAULT_AUDIT_DIR = Path.home() / ".destiny_mcp" / "audit"
_MAX_RESULT_CHARS = 50_000  # Truncate large results


def client_label(session: Any) -> str:
    """这次调用来自哪个客户端，形如 `名字/版本`；读不到给**空串**。

    MCP 握手时客户端自报家门（`InitializeRequestParams.clientInfo`），会话上取得到
    （`ServerSession.client_params`）。取不到的常见原因：握手还没完成，或者根本不是经协议调用
    （单测、脚本直调）。**不编名字** —— 塞一个 "unknown" 会让人以为那是观测到的事实。
    """
    params = getattr(session, "client_params", None)
    info = getattr(params, "clientInfo", None)
    name = str(getattr(info, "name", "") or "").strip()
    if not name:
        return ""
    version = str(getattr(info, "version", "") or "").strip()
    return f"{name}/{version}" if version else name


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
        client: str = "",
    ) -> None:
        """Write an audit record for a tool invocation.

        Args:
            tool_name: Name of the MCP tool.
            arguments: Arguments passed to the tool.
            result: Tool result (will be summarized if too large).
            duration_ms: Execution time in milliseconds.
            error: Error message if the call failed.
            client: Which MCP client made the call (`名字/版本`), empty when unknown.
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
                # 哪个宿主调的。旧的审计条目没有这个字段 —— 读取侧按缺失处理，不要回填。
                "client": client,
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
