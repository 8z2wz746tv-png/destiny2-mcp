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


def resolve_community_data_dir() -> Path:
    """Resolve the author Markdown bundle in a checkout or wheel install."""

    candidates = (
        PROJECT_ROOT / "share",
        Path(__file__).resolve().parents[1] / "share" / "destiny-mcp" / "community",
        Path(sys.prefix) / "share" / "destiny-mcp" / "community",
    )
    return next(
        (
            path
            for path in candidates
            if (path / "weapon-perks.md").is_file()
            and (path / "armor-sets.md").is_file()
        ),
        candidates[0],
    )

# Prefer the user's current checkout .env, then package-adjacent .env for
# editable installs launched from another working directory.
load_dotenv(_project_root / ".env")
load_dotenv(Path(__file__).resolve().parents[1] / ".env", override=False)


# Bungie API credentials
BUNGIE_API_KEY: str = os.getenv("BUNGIE_API_KEY", "")
BUNGIE_CLIENT_ID: str = os.getenv("BUNGIE_CLIENT_ID", "")
BUNGIE_CLIENT_SECRET: str = os.getenv("BUNGIE_CLIENT_SECRET", "")
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
STARSIDE_SHARE_PATH: Path = Path(
    os.path.expanduser(
        os.getenv("STARSIDE_SHARE_PATH", str(resolve_community_data_dir()))
    )
)

# Default player name — set this to skip typing your Bungie name every time
DESTINY_DEFAULT_PLAYER: str | None = os.getenv("DESTINY_DEFAULT_PLAYER")

# MCP transport and tool surface. Read here so every env lookup lives in one module.
MCP_HOST: str = os.getenv("MCP_HOST", "127.0.0.1")
MCP_PORT: int = int(os.getenv("MCP_PORT", "8000"))
MCP_TRANSPORT: str = os.getenv("MCP_TRANSPORT", "stdio")
TOOL_PROFILE: str = os.getenv("DESTINY_MCP_TOOL_PROFILE", "normal")

# DIM wish list: an explicit path wins, otherwise the fallback order below decides.
WISHLIST_PATH: Path | None = (
    Path(os.path.expanduser(value))
    if (value := os.getenv("DESTINY_WISHLIST_PATH"))
    else None
)
# These only say whether the environment set the value; the paths themselves are
# derived above, so the fallback order cannot drift from DATA_PATH / PROJECT_ROOT.
DATA_PATH_CONFIGURED: bool = bool(os.getenv("DATA_PATH"))
PROJECT_ROOT_CONFIGURED: bool = bool(os.getenv("DESTINY_MCP_ROOT"))


def validate_credentials() -> None:
    """Validate at startup, so importing tools/models requires no credentials."""
    for key in ("BUNGIE_API_KEY", "BUNGIE_CLIENT_ID", "BUNGIE_CLIENT_SECRET"):
        if not globals()[key]:
            raise ConfigError(f"Missing required environment variable: {key}. Configure it locally in .env.")
    if not BUNGIE_CLIENT_ID.isascii() or not BUNGIE_CLIENT_ID.isdigit():
        raise ConfigError("BUNGIE_CLIENT_ID must be a numeric client ID.")
