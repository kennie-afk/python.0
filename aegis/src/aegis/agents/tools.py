from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol

from aegis.governance.actions import ActionType, ProposedAction


@dataclass(frozen=True, slots=True)
class ToolResult:
    succeeded: bool
    output: dict[str, Any] = field(default_factory=dict)
    detail: str | None = None

    @classmethod
    def ok(cls, **output: Any) -> ToolResult:
        return cls(succeeded=True, output=output)

    @classmethod
    def failed(cls, detail: str) -> ToolResult:
        return cls(succeeded=False, detail=detail)


class Tool(Protocol):
    def handles(self) -> frozenset[ActionType]: ...

    def execute(self, action: ProposedAction) -> ToolResult: ...


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[ActionType, Tool] = {}

    def register(self, tool: Tool) -> ToolRegistry:
        for action_type in tool.handles():
            if action_type in self._tools:
                raise ValueError(f"{action_type} already has a registered tool")
            self._tools[action_type] = tool
        return self

    def resolve(self, action_type: ActionType) -> Tool | None:
        return self._tools.get(action_type)

    def registered(self) -> frozenset[ActionType]:
        return frozenset(self._tools)


class RecordingTool:
    def __init__(self, action_types: frozenset[ActionType], output: dict[str, Any] | None = None):
        self._action_types = action_types
        self._output = output or {}
        self.calls: list[ProposedAction] = []

    def handles(self) -> frozenset[ActionType]:
        return self._action_types

    def execute(self, action: ProposedAction) -> ToolResult:
        self.calls.append(action)
        return ToolResult(succeeded=True, output=dict(self._output))


class FailingTool:
    def __init__(self, action_types: frozenset[ActionType], detail: str = "upstream unavailable"):
        self._action_types = action_types
        self._detail = detail

    def handles(self) -> frozenset[ActionType]:
        return self._action_types

    def execute(self, action: ProposedAction) -> ToolResult:
        return ToolResult.failed(self._detail)
