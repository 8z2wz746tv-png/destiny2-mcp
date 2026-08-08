"""Build Engine exceptions.

All exceptions inherit from BuildEngineError so callers can catch a single
base type.
"""

from __future__ import annotations


class BuildEngineError(Exception):
    """Base exception for all Build Engine errors."""


class NoSolutionError(BuildEngineError):
    """No armor combination satisfies the given constraints."""

    def __init__(self, reason: str = "") -> None:
        self.reason = reason
        super().__init__(f"No build satisfies constraints." + (f" {reason}" if reason else ""))


class InvalidConstraintsError(BuildEngineError):
    """Constraints are logically impossible or malformed."""

    def __init__(self, detail: str = "") -> None:
        self.detail = detail
        super().__init__(f"Invalid build constraints." + (f" {detail}" if detail else ""))
