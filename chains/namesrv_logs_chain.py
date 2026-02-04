from typing import Dict, Any, List, Tuple

from tools.kubectl_tools import run_kubectl_logs, run_kubectl


def _extract_namesrv_pods(output: str) -> List[Tuple[str, str]]:
    pods: List[Tuple[str, str]] = []
    if not output:
        return pods
    for line in output.splitlines():
        if "namesrv" not in line.lower():
            continue
        parts = line.split()
        if len(parts) >= 2:
            namespace = parts[0]
            pod = parts[1]
            pods.append((namespace, pod))
    return pods


def run_namesrv_logs_chain(context: Dict[str, Any]) -> Dict[str, Any]:
    """
    NameServer 日志收集链：
    1) kubectl 查询全部 namesrv pod
    2) 逐个拉取 namesrv 容器日志
    """
    execute = bool(context.get("execute", False))
    namespace = context.get("namespace")
    tail = int(context.get("tail", 200))
    container = context.get("container", "namesrv")

    if namespace:
        list_cmd = f"get pods -n {namespace} -o wide"
    else:
        list_cmd = "get pods -A -o wide | grep rocketmq5"

    list_result = run_kubectl(list_cmd, execute=execute)
    pods = _extract_namesrv_pods(list_result.get("output", ""))

    logs_results: List[Dict[str, Any]] = []
    for ns, pod in pods:
        logs = run_kubectl_logs(
            namespace=ns,
            pod=pod,
            container=container,
            tail=tail,
            execute=execute,
        )
        logs_results.append({
            "namespace": ns,
            "pod": pod,
            "logs": logs,
        })

    return {
        "scope": "namesrv_logs",
        "pods_count": len(pods),
        "list_pods": list_result,
        "logs": logs_results,
    }
