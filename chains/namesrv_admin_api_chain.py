from typing import Dict, Any

from tools.admin_api_tools import namesrv_admin_api


def run_namesrv_admin_api_chain(context: Dict[str, Any]) -> Dict[str, Any]:
    """通过 MCP 工具调用 NameServer Admin API。"""
    namesrv = context.get("namesrv")
    action = context.get("action", "clusterList")
    execute = bool(context.get("execute", False))
    extra_args = context.get("extra_args")
    topic = context.get("topic")
    ak = context.get("ak")
    sk = context.get("sk")
    namespace = context.get("namespace")
    namesrv_pod = context.get("namesrv_pod")

    if not namesrv:
        return {
            "scope": "namesrv_admin_api",
            "error": "缺少 namesrv",
            "next_actions": ["提供 NameServer 地址（namesrv）"],
        }

    result = namesrv_admin_api(
        action=action,
        namesrv=namesrv,
        topic=topic,
        ak=ak,
        sk=sk,
        namespace=namespace,
        namesrv_pod=namesrv_pod,
        extra_args=extra_args,
        execute=execute,
    )
    return {
        "scope": "namesrv_admin_api",
        **result,
    }
