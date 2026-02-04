from typing import Dict, Any

from tools.admin_api_tools import broker_admin_api


def run_broker_admin_api_chain(context: Dict[str, Any]) -> Dict[str, Any]:
    """通过 MCP 工具调用 Broker Admin API。"""
    namesrv = context.get("namesrv")
    broker = context.get("broker")
    action = context.get("action", "brokerStatus")
    execute = bool(context.get("execute", False))
    extra_args = context.get("extra_args")
    ak = context.get("ak")
    sk = context.get("sk")
    namespace = context.get("namespace")
    namesrv_pod = context.get("namesrv_pod")

    if not namesrv:
        return {
            "scope": "broker_admin_api",
            "error": "缺少 namesrv",
            "next_actions": ["提供 NameServer 地址（namesrv）"],
        }

    if action == "getBrokerConfig" and not broker:
        return {
            "scope": "broker_admin_api",
            "error": "缺少 broker",
            "next_actions": ["提供 Broker 地址或名称（broker）"],
        }

    result = broker_admin_api(
        action=action,
        namesrv=namesrv,
        broker=broker,
        ak=ak,
        sk=sk,
        namespace=namespace,
        namesrv_pod=namesrv_pod,
        extra_args=extra_args,
        execute=execute,
    )
    return {
        "scope": "broker_admin_api",
        **result,
    }
