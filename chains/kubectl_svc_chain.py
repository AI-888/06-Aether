from typing import Dict, Any

from tools.kubectl_tools import run_kubectl_svc


def run_kubectl_svc_chain(context: Dict[str, Any]) -> Dict[str, Any]:
    """查看 RocketMQ 相关 Service 列表。"""
    execute = bool(context.get("execute", False))
    namespace = context.get("namespace")

    result = run_kubectl_svc(namespace=namespace, execute=execute)
    return {
        "scope": "kubectl_svc",
        **result,
    }
