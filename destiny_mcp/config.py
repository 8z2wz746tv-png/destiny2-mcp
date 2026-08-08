"""Configuration management — reads from env vars and dotenv files."""

import os
import sys
from pathlib import Path

from dotenv import load_dotenv

from .exceptions import ConfigError


def _resolve_project_root() -> Path:
    env_root = os.getenv("DESTINY_MCP_ROOT")
    if env_root:
        return Path(env_root).expanduser().resolve()

    cwd = Path.cwd().resolve()
    if (cwd / ".env").exists() or (cwd / "destiny_mcp").is_dir():
        return cwd

    # Source checkout layout:
    #   destiny2-mcp/destiny_mcp/config.py
    return Path(__file__).resolve().parents[1]


_project_root = _resolve_project_root()
PROJECT_ROOT: Path = _project_root


def resolve_resource_dir(name: str) -> Path:
    """Resolve source-checkout or wheel-installed prompt/skill directories."""
    candidates = (
        PROJECT_ROOT / name,
        Path(__file__).resolve().parents[1] / "share" / "destiny-mcp" / name,
        Path(sys.prefix) / "share" / "destiny-mcp" / name,
    )
    return next((path for path in candidates if path.is_dir()), candidates[0])

# Prefer the user's current checkout .env, then package-adjacent .env for
# editable installs launched from another working directory.
load_dotenv(_project_root / ".env")
load_dotenv(Path(__file__).resolve().parents[1] / ".env", override=False)


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
DESTINY_OAUTH_REDIRECT_URI: str | None = os.getenv("DESTINY_OAUTH_REDIRECT_URI")

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
