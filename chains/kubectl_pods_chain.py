from typing import Dict, Any

from tools.kubectl_tools import run_kubectl_pods


def run_kubectl_pods_chain(context: Dict[str, Any]) -> Dict[str, Any]:
    """查看 RocketMQ 相关 Pod 列表。"""
    execute = bool(context.get("execute", False))
    namespace = context.get("namespace")

    result = run_kubectl_pods(namespace=namespace, execute=execute)
    return {
        "scope": "kubectl_pods",
        **result,
    }
