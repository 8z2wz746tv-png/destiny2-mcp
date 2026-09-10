#!/usr/bin/env python3
"""Platform-neutral entry point for the repository's real MCP verification."""

from __future__ import annotations

import runpy
from pathlib import Path


def main() -> int:
    verifier = (
        Path(__file__).resolve().parents[1]
        / "skills"
        / "destiny-mcp-setup"
        / "scripts"
        / "verify_mcp.py"
    )
    namespace = runpy.run_path(str(verifier))
    return int(namespace["main"]())


if __name__ == "__main__":
    raise SystemExit(main())
