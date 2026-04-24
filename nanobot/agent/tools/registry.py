from __future__ import annotations
"""Tool registry for dynamic tool management."""

from typing import Any

from nanobot.agent.tools.base import Tool


class ToolRegistry:
    """
    Registry for agent tools.
    
    Allows dynamic registration and execution of tools.
    """

    def __init__(self):
        self._tools: dict[str, Tool] = {}

    def register(self, tool: Tool) -> None:
        """Register a tool."""
        self._tools[tool.name] = tool

    def unregister(self, name: str) -> None:
        """Unregister a tool by name."""
        self._tools.pop(name, None)

    def get(self, name: str) -> Tool | None:
        """Get a tool by name."""
        return self._tools.get(name)

    def has(self, name: str) -> bool:
        """Check if a tool is registered."""
        return name in self._tools

    def get_definitions(self) -> list[dict[str, Any]]:
        """Get all tool definitions in OpenAI format."""
        return [tool.to_schema() for tool in self._tools.values()]

    def get_definitions_by_names(self, names: list[str]) -> list[dict[str, Any]]:
        """按名称列表筛选工具定义，只返回匹配的工具 schema。"""
        return [
            tool.to_schema()
            for tool in self._tools.values()
            if tool.name in names
        ]

    async def execute(self, name: str, params: dict[str, Any]) -> dict[str, Any]:
        """
        Execute a tool by name with given parameters.
        
        Args:
            name: Tool name.
            params: Tool parameters.
        
        Returns:
            Dict with keys:
                - result: 工具执行结果字符串
                - commands: 本次执行的命令列表
        
        Raises:
            KeyError: If tool not found.
        """
        tool = self._tools.get(name)
        if not tool:
            return {"result": f"Error: Tool '{name}' not found", "commands": []}

        try:
            errors = tool.validate_params(params)
            if errors:
                return {
                    "result": f"Error: Invalid parameters for tool '{name}': " + "; ".join(errors),
                    "commands": [],
                    "tool_param_validation_failed": True,
                    "tool_param_validation_errors": errors,
                    "tool_param_validation_tag": "参数校验失败",
                    "should_refill_last_user_input": True,
                }
            raw = await tool.execute(**params)
            # 兼容旧工具仍返回 str 的情况
            if isinstance(raw, str):
                return {"result": raw, "commands": []}
            if isinstance(raw, dict):
                raw.setdefault("result", "")
                raw.setdefault("commands", [])
                return raw
            return {"result": str(raw), "commands": []}
        except Exception as e:
            return {"result": f"Error executing {name}: {str(e)}", "commands": []}

    @property
    def tool_names(self) -> list[str]:
        """Get list of registered tool names."""
        return list(self._tools.keys())

    def __len__(self) -> int:
        return len(self._tools)

    def __contains__(self, name: str) -> bool:
        return name in self._tools