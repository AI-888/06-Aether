from dataclasses import dataclass
from typing import Callable, Dict, List, Optional

from tools.kubectl_tools import (
    run_list_broker_pods,
    run_list_namesrv_pods,
    run_list_proxy_pods,
    run_topic_exists_check,
)
from tools.rocketmq_admin_tools import (
    ADMIN_TOOL_RUNNERS,
    get_admin_command_specs,
    get_admin_required_flags,
    get_admin_param_desc,
    run_mqadmin_command,
    run_mqadmin_topic_list,
)


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
TOOL_ROCKETMQ_ADMIN_COMMAND = "rocketmq_admin_command"
TOOL_LIST_TOPICS = "list_topics"
TOOL_GET_BROKER_CONFIG = "get_broker_config"


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
    TOOL_ROCKETMQ_ADMIN_COMMAND: ToolDef(
        name=TOOL_ROCKETMQ_ADMIN_COMMAND,
        description="在 namesrv pod 内执行 mqadmin 命令",
        runner=run_mqadmin_command,
        include_in_prompt=False,
        category="admin",
    ),
    TOOL_LIST_TOPICS: ToolDef(
        name=TOOL_LIST_TOPICS,
        description="列出 RocketMQ 全部主题（topicList）",
        runner=run_mqadmin_topic_list,
        category="admin",
        params=["k8s_namespace", "keyword", "namesrv_addr", "namesrv_pod", "namesrv_container"],
    ),
    TOOL_GET_BROKER_CONFIG: ToolDef(
        name=TOOL_GET_BROKER_CONFIG,
        description="查询 RocketMQ Broker 配置（getBrokerConfig）",
        runner=run_mqadmin_command,
        category="admin",
        params=["k8s_namespace", "namesrv_addr", "namesrv_pod", "namesrv_container", "admin_subcommand"],
    ),
}

# Append admin commands as first-class tools (with per-command params)
for cmd_name, spec in get_admin_command_specs().items():
    if cmd_name in TOOL_DEFS:
        continue
    TOOL_DEFS[cmd_name] = ToolDef(
        name=cmd_name,
        description=spec.get("desc", ""),
        runner=ADMIN_TOOL_RUNNERS.get(cmd_name),
        category="admin",
        params=spec.get("params", []) + ["k8s_namespace", "namesrv_addr", "namesrv_pod", "namesrv_container"],
    )


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
    return get_admin_required_flags(name)


def get_admin_param_descs(name: str) -> Dict[str, str]:
    """Return flag -> description mapping for admin command."""
    return get_admin_param_desc(name)
