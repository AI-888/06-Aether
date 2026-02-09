from dataclasses import dataclass
from typing import Callable, Dict, List, Optional

from tools.kubectl_tools import (
    run_list_broker_pods,
    run_list_namesrv_pods,
    run_list_proxy_pods,
    run_topic_exists_check,
)
from tools.rocketmq_admin_tools import run_mcp_admin_tool


@dataclass(frozen=True)
class ToolDef:
    """工具定义：统一管理工具名称、描述与执行方法。"""
    name: str
    description: str
    runner: Callable[..., Dict[str, str]]
    include_in_prompt: bool = True
    category: str = "kubectl"
    params: Optional[List[str]] = None


TOOL_LIST_BROKER_PODS = "list_broker_pods"
TOOL_LIST_NAMESRV_PODS = "list_namesrv_pods"
TOOL_LIST_PROXY_PODS = "list_proxy_pods"
TOOL_SEND_FAIL_CHECK = "send_fail_check"
TOOL_LIST_TOPICS = "list_topics"
TOOL_GET_BROKER_CONFIG = "getBrokerConfig"
TOOL_TOPIC_ROUTE = "topicRoute"
TOOL_TOPIC_STATUS = "topicStatus"
TOOL_TOPIC_CLUSTER_LIST = "topicClusterList"
TOOL_BROKER_STATUS = "brokerStatus"
TOOL_CONSUMER_PROGRESS = "consumerProgress"
TOOL_CONSUMER_STATUS = "consumerStatus"
TOOL_CONSUMER_CONNECTION = "consumerConnection"
TOOL_PRODUCER_CONNECTION = "producerConnection"


def _run_send_fail_check(
    k8s_namespace: str,
    real_topic: str,
    execute: bool = False,
) -> Dict[str, str]:
    """发送失败排查工具：通过 namesrv pod 校验 topic 是否存在。"""
    return run_topic_exists_check(k8s_namespace, real_topic, execute=execute)


TOOL_DEFS: Dict[str, ToolDef] = {
    TOOL_LIST_BROKER_PODS: ToolDef(
        name=TOOL_LIST_BROKER_PODS,
        description="列出所有 RocketMQ broker pods",
        runner=run_list_broker_pods,
        category="kubectl",
    ),
    TOOL_LIST_NAMESRV_PODS: ToolDef(
        name=TOOL_LIST_NAMESRV_PODS,
        description="列出所有 RocketMQ namesrv pods",
        runner=run_list_namesrv_pods,
        category="kubectl",
    ),
    TOOL_LIST_PROXY_PODS: ToolDef(
        name=TOOL_LIST_PROXY_PODS,
        description="列出所有 RocketMQ proxy pods",
        runner=run_list_proxy_pods,
        category="kubectl",
    ),
    TOOL_SEND_FAIL_CHECK: ToolDef(
        name=TOOL_SEND_FAIL_CHECK,
        description="发送消息失败排查：校验 topic 是否存在",
        runner=_run_send_fail_check,
        category="kubectl",
    ),
    TOOL_LIST_TOPICS: ToolDef(
        name=TOOL_LIST_TOPICS,
        description="列出 RocketMQ 全部主题（topicList）",
        runner=lambda **kwargs: run_mcp_admin_tool("fetchAllTopicList", kwargs),
        category="admin",
        params=["nameserverAddressList", "ak", "sk"],
    ),
    TOOL_GET_BROKER_CONFIG: ToolDef(
        name=TOOL_GET_BROKER_CONFIG,
        description="查询 RocketMQ Broker 配置（getBrokerConfig）",
        runner=lambda **kwargs: run_mcp_admin_tool("getBrokerConfig", kwargs),
        category="admin",
        params=["nameserverAddressList", "ak", "sk", "brokerAddr"],
    ),
    TOOL_TOPIC_ROUTE: ToolDef(
        name=TOOL_TOPIC_ROUTE,
        description="获取主题路由信息（MCP）",
        runner=lambda **kwargs: run_mcp_admin_tool(TOOL_TOPIC_ROUTE, kwargs),
        category="admin",
        params=["topic", "nameserverAddressList", "ak", "sk"],
    ),
    TOOL_TOPIC_STATUS: ToolDef(
        name=TOOL_TOPIC_STATUS,
        description="获取主题统计信息（MCP）",
        runner=lambda **kwargs: run_mcp_admin_tool(TOOL_TOPIC_STATUS, kwargs),
        category="admin",
        params=["topic", "nameserverAddressList", "ak", "sk"],
    ),
    TOOL_TOPIC_CLUSTER_LIST: ToolDef(
        name=TOOL_TOPIC_CLUSTER_LIST,
        description="获取主题集群列表（MCP）",
        runner=lambda **kwargs: run_mcp_admin_tool(TOOL_TOPIC_CLUSTER_LIST, kwargs),
        category="admin",
        params=["topic", "nameserverAddressList", "ak", "sk"],
    ),
    TOOL_BROKER_STATUS: ToolDef(
        name=TOOL_BROKER_STATUS,
        description="获取 Broker 运行状态（MCP）",
        runner=lambda **kwargs: run_mcp_admin_tool(TOOL_BROKER_STATUS, kwargs),
        category="admin",
        params=["brokerAddr", "nameserverAddressList", "ak", "sk"],
    ),
    TOOL_CONSUMER_PROGRESS: ToolDef(
        name=TOOL_CONSUMER_PROGRESS,
        description="获取消费者组消费进度（MCP）",
        runner=lambda **kwargs: run_mcp_admin_tool(TOOL_CONSUMER_PROGRESS, kwargs),
        category="admin",
        params=["group", "nameserverAddressList", "ak", "sk"],
    ),
    TOOL_CONSUMER_STATUS: ToolDef(
        name=TOOL_CONSUMER_STATUS,
        description="获取消费者连接信息（MCP）",
        runner=lambda **kwargs: run_mcp_admin_tool(TOOL_CONSUMER_STATUS, kwargs),
        category="admin",
        params=["consumerGroup", "nameserverAddressList", "ak", "sk"],
    ),
    TOOL_CONSUMER_CONNECTION: ToolDef(
        name=TOOL_CONSUMER_CONNECTION,
        description="获取消费者连接信息（MCP）",
        runner=lambda **kwargs: run_mcp_admin_tool(TOOL_CONSUMER_CONNECTION, kwargs),
        category="admin",
        params=["consumerGroup", "nameserverAddressList", "ak", "sk"],
    ),
    TOOL_PRODUCER_CONNECTION: ToolDef(
        name=TOOL_PRODUCER_CONNECTION,
        description="获取生产者连接信息（MCP）",
        runner=lambda **kwargs: run_mcp_admin_tool(TOOL_PRODUCER_CONNECTION, kwargs),
        category="admin",
        params=["producerGroup", "topic", "nameserverAddressList", "ak", "sk"],
    ),
}


def list_tool_names() -> List[str]:
    """返回提示词中可公开的工具名称列表。"""
    return [name for name, tool in TOOL_DEFS.items() if tool.include_in_prompt]


def get_tool_def(name: str) -> ToolDef:
    """按名称获取工具定义。"""
    return TOOL_DEFS[name]


def build_tools_prompt() -> str:
    """生成提示词中的工具列表说明，确保与注册表一致。"""
    lines = ["可用工具列表（名称 + 说明）："]
    for tool in TOOL_DEFS.values():
        if not tool.include_in_prompt:
            continue
        if tool.params:
            lines.append(f"- {tool.name}: {tool.description} | params: {', '.join(tool.params)}")
        else:
            lines.append(f"- {tool.name}: {tool.description}")
    return "\n".join(lines)


def run_tool(name: str, **kwargs) -> Dict[str, str]:
    """统一工具执行入口，避免各处硬编码调用函数。"""
    tool = get_tool_def(name)
    return tool.runner(**kwargs)


def list_admin_tool_names() -> List[str]:
    """返回 admin 类工具名称列表。"""
    return [name for name, tool in TOOL_DEFS.items() if tool.category == "admin"]


def get_admin_required(name: str) -> List[str]:
    """Return required flags for admin command."""
    return []


def get_admin_param_descs(name: str) -> Dict[str, str]:
    """Return flag -> description mapping for admin command."""
    try:
        tool = get_tool_def(name)
    except Exception:
        return {}
    base_desc = {
        "topic": "topic name",
        "group": "consumer group",
        "consumerGroup": "consumer group",
        "producerGroup": "producer group",
        "brokerAddr": "broker address (IP:PORT)",
        "nameserverAddressList": "namesrv address list",
        "ak": "access key",
        "sk": "secret key",
        "msgId": "message id",
        "key": "message key",
    }
    descs: Dict[str, str] = {}
    for p in tool.params or []:
        if p in base_desc:
            descs[p] = base_desc[p]
    return descs
