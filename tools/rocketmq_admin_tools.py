"""
RocketMQ admin tools via MCP service.
"""

from typing import Any, Dict, Optional

from tools.mcp_client import call_mcp_tool, get_mcp_defaults


# Map legacy intent/tool names to MCP tool names
TOOL_NAME_MAP: Dict[str, str] = {
    "topicRoute": "examineTopicRouteInfo",
    "topicStatus": "examineTopicStats",
    "topicClusterList": "getTopicClusterList",
    "brokerStatus": "getBrokerRuntimeStats",
    "consumerProgress": "examineConsumeStats",
    "consumerStatus": "examineConsumerConnectionInfo",
    "producerConnection": "examineProducerConnectionInfo",
    "consumerConnection": "examineConsumerConnectionInfo",
    "getBrokerConfig": "getBrokerConfig",
}


def run_mcp_admin_tool(tool_name: str, params: Dict[str, Any]) -> Dict[str, Any]:
    """Call MCP admin tool with merged defaults."""
    defaults = get_mcp_defaults()
    merged = {**defaults, **params}
    mapped_name = TOOL_NAME_MAP.get(tool_name, tool_name)
    return call_mcp_tool(mapped_name, merged)
