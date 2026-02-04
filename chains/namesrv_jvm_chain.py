from typing import Dict, Any, List, Optional

from tools.kubectl_tools import run_kubectl_exec


def _pick_pid(jps_output: str) -> Optional[str]:
    for line in jps_output.splitlines():
        if "NamesrvStartup" in line or "namesrv" in line.lower():
            return line.strip().split()[0]
    for line in jps_output.splitlines():
        parts = line.strip().split()
        if parts:
            return parts[0]
    return None


def run_namesrv_jvm_chain(context: Dict[str, Any]) -> Dict[str, Any]:
    """
    NameServer JVM 诊断链：定位 Java 进程并采集 JVM 堆栈与进程信息。
    需要：namespace、pod、container（默认 namesrv）
    """
    namespace = context.get("namespace")
    pod = context.get("pod")
    container = context.get("container", "namesrv")
    execute = bool(context.get("execute", False))

    if not namespace or not pod:
        return {
            "scope": "namesrv_jvm",
            "error": "缺少 namespace 或 pod",
            "next_actions": [
                "提供 namespace 与 namesrv pod 名称",
                "可先执行 kubectl get pods -n <namespace> -o wide",
            ],
        }

    steps: List[Dict[str, Any]] = []

    jps = run_kubectl_exec(namespace, pod, container, "jps -l", execute=execute)
    steps.append({"step": "jps", "result": jps})

    pid = _pick_pid(jps.get("output", "")) if execute else None
    if not pid and execute:
        return {
            "scope": "namesrv_jvm",
            "error": "未识别到 NameServer Java 进程",
            "steps": steps,
            "next_actions": ["检查容器内是否安装 jps，或手动提供 pid"],
        }

    ps = run_kubectl_exec(namespace, pod, container, "ps -ef | grep -i java", execute=execute)
    steps.append({"step": "ps", "result": ps})

    if pid:
        jstack = run_kubectl_exec(namespace, pod, container, f"jstack -l {pid}", execute=execute)
        steps.append({"step": "jstack", "result": jstack})

    return {
        "scope": "namesrv_jvm",
        "pid": pid,
        "steps": steps,
        "next_actions": [],
    }
