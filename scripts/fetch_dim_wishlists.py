#!/usr/bin/env python3
"""Compatibility wrapper for fetching DIM wish list data."""

from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from destiny_mcp.wishlist_data import main


if __name__ == "__main__":
    main()
