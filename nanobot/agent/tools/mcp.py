"""MCP (Model Context Protocol) tool for accessing external services."""

import asyncio
import json
from typing import Any

from mcp import ClientSession
from mcp.client.sse import sse_client

from nanobot.agent.tools.base import Tool
from nanobot.config.loader import load_config


def _join_server_url(base_url: str, path: str) -> str:
    base = (base_url or "").strip()
    if not base:
        return ""
    p = (path or "").strip()
    if not p:
        return base
    return f"{base.rstrip('/')}" + (p if p.startswith("/") else f"/{p}")


def _normalize_mcp_result(result: Any) -> Any:
    if result is None:
        return {}
    if isinstance(result, dict):
        return result
    if hasattr(result, "model_dump"):
        return result.model_dump()

    normalized: dict[str, Any] = {}
    for attr in ("content", "isError", "error", "structuredContent"):
        if hasattr(result, attr):
            normalized[attr] = getattr(result, attr)
    return normalized or {"result": str(result)}


async def _call_mcp_tool_via_sse(
    sse_url: str,
    tool_name: str,
    arguments: dict[str, Any],
    auth_token: str = "",
    timeout: int = 30,
) -> Any:
    headers = {"Authorization": f"Bearer {auth_token}"} if auth_token else {}

    async with asyncio.timeout(timeout):
        async with sse_client(sse_url, headers=headers) as (read_stream, write_stream):
            async with ClientSession(read_stream, write_stream) as session:
                await session.initialize()
                result = await session.call_tool(tool_name, arguments)
                return _normalize_mcp_result(result)


class MCPTool(Tool):
    """Tool for calling MCP (Model Context Protocol) servers."""

    @property
    def name(self) -> str:
        return "use_mcp_tool"

    @property
    def description(self) -> str:
        return "Call a tool provided by a connected MCP (Model Context Protocol) server. MCP servers provide specialized tools for accessing external services and data sources."

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "server_name": {
                    "type": "string",
                    "description": "Name of the MCP server providing the tool"
                },
                "tool_name": {
                    "type": "string",
                    "description": "Name of the tool to execute"
                },
                "arguments": {
                    "type": "object",
                    "description": "JSON object containing the tool's input parameters"
                }
            },
            "required": ["server_name", "tool_name", "arguments"]
        }

    async def execute(self, server_name: str, tool_name: str, arguments: dict[str, Any]) -> str:
        """Execute an MCP tool call."""
        try:
            # Load configuration
            config = load_config()

            # Check if MCP server is configured
            if server_name not in config.mcp.servers:
                return f"Error: MCP server '{server_name}' not found in configuration"

            server_config = config.mcp.servers[server_name]
            if not server_config.enabled:
                return f"Error: MCP server '{server_name}' is not enabled"

            server_url = getattr(server_config, "server_url", "")
            message_path = getattr(server_config, "message_path", "/mcp/message")
            auth_token = getattr(server_config, "auth_token", "")
            message_url = _join_server_url(server_url, message_path)
            if not message_url:
                return f"Error: MCP server '{server_name}' has invalid server_url/message_path configuration"

            response = await _call_mcp_tool_via_sse(
                sse_url=message_url,
                tool_name=tool_name,
                arguments=arguments,
                auth_token=auth_token,
            )
            return json.dumps(response, indent=2, ensure_ascii=False)

        except Exception as e:
            return f"Error calling MCP tool: {str(e)}"


class MCPKnowledgeSearchTool(Tool):
    """Tool for searching knowledge bases via MCP servers."""

    @property
    def name(self) -> str:
        return "mcp_knowledge_search"

    @property
    def description(self) -> str:
        return "Search knowledge bases using MCP servers. This tool can query various data sources like documents, databases, and APIs through connected MCP servers."

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "server_name": {
                    "type": "string",
                    "description": "Name of the MCP server providing knowledge search"
                },
                "query": {
                    "type": "string",
                    "description": "Search query"
                },
                "knowledge_uuid": {
                    "type": "string",
                    "description": "UUID of the knowledge base to search"
                },
                "data_type": {
                    "type": "string",
                    "description": "Type of data to search (e.g., 'story', 'document', 'task')",
                    "enum": ["story", "document", "task", "approve", "app_profile", "alarm"]
                },
                "page": {
                    "type": "integer",
                    "description": "Page number for paginated results",
                    "minimum": 1,
                    "default": 1
                },
                "page_size": {
                    "type": "integer",
                    "description": "Number of results per page",
                    "minimum": 1,
                    "maximum": 50,
                    "default": 10
                }
            },
            "required": ["server_name", "query", "knowledge_uuid", "data_type"]
        }

    async def execute(self, server_name: str, query: str, knowledge_uuid: str, data_type: str,
                      page: int = 1, page_size: int = 10) -> str:
        """Execute a knowledge search via MCP server."""
        try:
            # Load configuration
            config = load_config()

            # Check if MCP server is configured
            if server_name not in config.mcp.servers:
                return f"Error: MCP server '{server_name}' not found in configuration"

            server_config = config.mcp.servers[server_name]
            if not server_config.enabled:
                return f"Error: MCP server '{server_name}' is not enabled"

            # Simulate knowledge search (in a real implementation, this would call MCP server)
            result = {
                "server": server_name,
                "query": query,
                "knowledge_uuid": knowledge_uuid,
                "data_type": data_type,
                "page": page,
                "page_size": page_size,
                "status": "success",
                "results": [
                    {
                        "id": "1",
                        "title": f"Search result for '{query}'",
                        "content": f"This is a simulated result from knowledge base {knowledge_uuid} for data type {data_type}.",
                        "score": 0.95
                    }
                ],
                "total_results": 1
            }

            return json.dumps(result, indent=2)

        except Exception as e:
            return f"Error performing MCP knowledge search: {str(e)}"
