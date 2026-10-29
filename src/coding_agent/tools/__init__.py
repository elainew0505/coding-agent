"""Tool results and the handler interface the agent loop calls."""

from collections.abc import Callable
from dataclasses import dataclass


@dataclass(frozen=True)
class ToolResult:
    content: str
    is_error: bool = False

    @classmethod
    def ok(cls, content: str) -> "ToolResult":
        return cls(content)

    @classmethod
    def error(cls, content: str) -> "ToolResult":
        return cls(content, is_error=True)


ToolHandler = Callable[[str, dict[str, object]], ToolResult]
"""Runs one tool call: (tool name, tool input) -> result."""
