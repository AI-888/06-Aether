from dataclasses import dataclass
from typing import Callable, Dict, List, Optional

from tools.kubectl_tools import run_kubectl


@dataclass(frozen=True)
class ToolDef:
    """工具定义：统一管理工具名称、描述与执行方法。"""
    name: str
    description: str
    runner: Callable[..., Dict[str, str]]
    include_in_prompt: bool = True
    category: str = "kubectl"
    params: Optional[List[str]] = None



TOOL_DEFS: Dict[str, ToolDef] = {}


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


def get_admin_param_descs(name: str) -> Dict[str, str]:
    """从 MCP 服务获取 admin 命令的参数描述映射。"""
    try:
        # 从 MCP 服务获取参数描述
        from tools.mcp_client import call_mcp_tool
        
        # 调用 MCP 服务获取工具参数信息
        response = call_mcp_tool("get_tool_parameters", {
            "tool_name": name,
            "action": "describe_parameters"
        })
        
        # 解析 MCP 响应
        if isinstance(response, dict) and "parameters" in response:
            return response["parameters"]
        
        # MCP 服务没有返回参数信息，返回空字典
        return {}
        
    except Exception:
        # MCP 调用失败，返回空字典
        return {}
