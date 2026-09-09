"""Declarative MCP tool registry used by the server factory."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable


@dataclass(frozen=True)
class ToolDefinition:
    function: Callable[..., Any]
    args: tuple[Any, ...]
    kwargs: dict[str, Any]


class ToolRegistry:
    """Collect decorators at module import, then bind them to an MCP instance."""

    def __init__(self) -> None:
        self._definitions: list[ToolDefinition] = []

    def tool(self, *args: Any, **kwargs: Any) -> Callable:
        def decorator(function: Callable[..., Any]) -> Callable[..., Any]:
            definition = ToolDefinition(function, args, kwargs)
            if definition not in self._definitions:
                self._definitions.append(definition)
            return function

        return decorator

    def register(self, server: Any, module_names: set[str]) -> None:
        for definition in self._definitions:
            if definition.function.__module__.rsplit(".", 1)[-1] in module_names:
                server.tool(*definition.args, **definition.kwargs)(definition.function)


mcp = ToolRegistry()
