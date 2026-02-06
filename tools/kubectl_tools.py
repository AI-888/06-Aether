"""
Kubernetes kubectl tools.
"""

from typing import Dict, Optional
from datetime import datetime

from tools.shell_mysql_tool import run_shell


def run_kubectl(
    subcommand: str,
    execute: bool = False,
) -> Dict[str, str]:
    """Build (and optionally run) kubectl command."""
    cmd = f"kubectl {subcommand}".strip()
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{ts}] [Tool Call] kubectl input: {{'cmd': '{cmd}', 'execute': {execute}}}")
    if not execute:
        result = {"command": cmd, "output": "", "executed": "false"}
        ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        print(f"[{ts}] [Tool Call] kubectl output: {result}")
        return result

    output = run_shell(cmd)
    result = {"command": cmd, "output": output, "executed": "true"}
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{ts}] [Tool Call] kubectl output: {result}")
    return result


def run_kubectl_exec(
    namespace: str,
    pod: str,
    container: str,
    command: str,
    execute: bool = False,
) -> Dict[str, str]:
    """Build (and optionally run) kubectl exec command."""
    subcmd = f"exec -n {namespace} {pod} -c {container} -- {command}"
    return run_kubectl(subcmd, execute=execute)


def run_kubectl_logs(
    namespace: str,
    pod: str,
    container: str,
    tail: int = 200,
    execute: bool = False,
) -> Dict[str, str]:
    """Build (and optionally run) kubectl logs command."""
    subcmd = f"logs -n {namespace} {pod} -c {container} --tail {tail}"
    return run_kubectl(subcmd, execute=execute)


def run_kubectl_pods(
    namespace: Optional[str],
    keyword: Optional[str] = None,
    execute: bool = False,
) -> Dict[str, str]:
    if namespace:
        subcmd = f"get pods -n {namespace} -o wide"
    else:
        subcmd = "get pods -A -o wide"
    if keyword:
        subcmd = f"{subcmd} | grep {keyword}"
    return run_kubectl(subcmd, execute=execute)


def run_kubectl_svc(
    namespace: Optional[str],
    keyword: Optional[str] = None,
    execute: bool = False,
) -> Dict[str, str]:
    if namespace:
        subcmd = f"get svc -n {namespace}"
    else:
        subcmd = "get svc -A"
    if keyword:
        subcmd = f"{subcmd} | grep {keyword}"
    return run_kubectl(subcmd, execute=execute)


def run_list_broker_pods(execute: bool = False) -> Dict[str, str]:
    """List all RocketMQ broker pods."""
    subcmd = "get pods -A -o wide | grep rocketmq5-broker"
    return run_kubectl(subcmd, execute=execute)


def run_list_namesrv_pods(execute: bool = False) -> Dict[str, str]:
    """List all RocketMQ namesrv pods."""
    subcmd = "get pods -A -o wide | grep rocketmq5-namesrv"
    return run_kubectl(subcmd, execute=execute)


def run_list_proxy_pods(execute: bool = False) -> Dict[str, str]:
    """List all RocketMQ proxy pods."""
    subcmd = "get pods -A -o wide | grep rocketmq5-proxy"
    return run_kubectl(subcmd, execute=execute)


def run_topic_exists_check(
    k8s_namespace: str,
    real_topic: str,
    execute: bool = False,
) -> Dict[str, str]:
    """Check whether a RocketMQ topic exists via mqadmin in namesrv pod."""
    subcmd = (
        f"exec -n {k8s_namespace} ocloud-tdmq-rocketmq5-namesrv-0 "
        f"-c ocloud-tdmq-rocketmq5-namesrv -- "
        f"bin/mqadmin topicList -n 127.0.0.1:9876 | grep {real_topic}"
    )
    return run_kubectl(subcmd, execute=execute)
