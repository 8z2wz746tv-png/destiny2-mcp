"""Custom exceptions for Destiny MCP.

All exceptions inherit from DestinyMCPError so callers can catch a single
base type. Specific exception classes carry the context needed for error
messages and logging.
"""

from __future__ import annotations


class DestinyMCPError(Exception):
    """Base exception for all Destiny MCP errors."""


class ConfigError(DestinyMCPError):
    """Configuration is missing or invalid (env vars, tokens, etc.)."""


class PlayerNotFoundError(DestinyMCPError):
    """A Bungie player name could not be resolved."""

    def __init__(self, player_name: str) -> None:
        self.player_name = player_name
        super().__init__(
            f"找不到玩家 '{player_name}'。请检查 Bungie 名称格式是否正确（如 '名字#1234'）。"
        )


class CharacterNotFoundError(DestinyMCPError):
    """A character (hunter/warlock/titan) was not found on the account."""

    def __init__(self, character_name: str, available: list[str] | None = None) -> None:
        self.character_name = character_name
        self.available = available or []
        msg = f"该账号上没有 '{character_name}' 角色。"
        if self.available:
            msg += f"可用角色：{', '.join(self.available)}"
        super().__init__(msg)


class ItemNotFoundError(DestinyMCPError):
    """An item instance or name could not be found."""

    def __init__(self, identifier: str, detail: str = "") -> None:
        self.identifier = identifier
        super().__init__(
            f"找不到物品 '{identifier}'。它可能已被分解或移走。"
            + (f" {detail}" if detail else "")
        )


class TransferError(DestinyMCPError):
    """An item transfer or equip operation failed."""

    def __init__(self, operation: str, detail: str = "") -> None:
        self.operation = operation
        super().__init__(
            f"操作失败：{operation}。"
            + (f"{detail}" if detail else "请稍后重试。")
        )


class ManifestError(DestinyMCPError):
    """Manifest is not loaded or not found."""


class BuildValidationError(DestinyMCPError):
    """A build request or exact execution contract is invalid."""


class WeaponPopularityDataError(DestinyMCPError):
    """A bundled weapon popularity snapshot is malformed."""


class AuthenticationError(DestinyMCPError):
    """OAuth token is missing, expired, or invalid."""


class SubclassError(DestinyMCPError):
    """A subclass modification operation failed."""

    def __init__(self, operation: str, detail: str = "") -> None:
        self.operation = operation
        super().__init__(
            f"子职业操作失败：{operation}。"
            + (f"{detail}" if detail else "")
        )


class APIError(DestinyMCPError):
    """A Bungie API call returned an error or unexpected response."""

    def __init__(self, operation: str, detail: str = "") -> None:
        self.operation = operation
        super().__init__(
            f"API 调用失败：{operation}。"
            + (f"{detail}" if detail else "")
        )


class BungieServiceUnavailableError(APIError):
    """Bungie.net is temporarily unavailable or under maintenance."""

    def __init__(self, operation: str, detail: str = "") -> None:
        super().__init__(
            operation,
            detail
            or "Bungie 官方接口暂时不可用，可能正在维护或限流。请稍后重试。",
        )
