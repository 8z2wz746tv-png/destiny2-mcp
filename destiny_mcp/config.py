"""Configuration management — reads from env vars and dotenv files."""

import os
from pathlib import Path

from dotenv import load_dotenv

from .exceptions import ConfigError

# Load .env from project root if present
_project_root = Path(__file__).resolve().parents[2]
load_dotenv(_project_root / ".env")


def _require(key: str) -> str:
    value = os.getenv(key)
    if not value:
        raise ConfigError(
            f"Missing required environment variable: {key}. "
            f"Copy .env.example to .env and fill in your credentials."
        )
    return value


# Bungie API credentials
BUNGIE_API_KEY: str = _require("BUNGIE_API_KEY")
BUNGIE_CLIENT_ID: str = _require("BUNGIE_CLIENT_ID")
BUNGIE_CLIENT_SECRET: str = _require("BUNGIE_CLIENT_SECRET")

# Paths
DESTINY_TOKEN_PATH: Path = Path(
    os.path.expanduser(os.getenv("DESTINY_TOKEN_PATH", "~/.destiny_mcp"))
)
DESTINY_MANIFEST_PATH: Path = Path(
    os.path.expanduser(os.getenv("DESTINY_MANIFEST_PATH", str(_project_root / "manifest")))
)
DATA_PATH: Path = Path(
    os.path.expanduser(os.getenv("DATA_PATH", str(_project_root / "data")))
)

# Default player name — set this to skip typing your Bungie name every time
DESTINY_DEFAULT_PLAYER: str | None = os.getenv("DESTINY_DEFAULT_PLAYER")

# Ensure directories exist
DESTINY_TOKEN_PATH.mkdir(parents=True, exist_ok=True)
DESTINY_MANIFEST_PATH.mkdir(parents=True, exist_ok=True)
