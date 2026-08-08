#!/usr/bin/env python3
"""Compatibility wrapper for source-checkout OAuth setup."""

from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from destiny_mcp.oauth_setup import main


if __name__ == "__main__":
    main()
