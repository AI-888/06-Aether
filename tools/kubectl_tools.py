"""
Kubernetes kubectl tools.
"""

from typing import Dict, Optional

from tools.shell_mysql_tool import run_shell


def run_kubectl(
    subcommand: str,
    execute: bool = False,
) -> Dict[str, str]:
    """Build (and optionally run) kubectl command."""
    cmd = f"kubectl {subcommand}".strip()
    if not execute:
        return {"command": cmd, "output": "", "executed": "false"}

    output = run_shell(cmd)
    return {"command": cmd, "output": output, "executed": "true"}


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
    execute: bool = False,
) -> Dict[str, str]:
    if namespace:
        subcmd = f"get pods -n {namespace} -o wide"
    else:
        subcmd = "get pods -A -o wide | grep rocketmq5"
    return run_kubectl(subcmd, execute=execute)


def run_kubectl_svc(
    namespace: Optional[str],
    execute: bool = False,
) -> Dict[str, str]:
    if namespace:
        subcmd = f"get svc -n {namespace}"
    else:
        subcmd = "get svc -A | grep rocketmq5"
    return run_kubectl(subcmd, execute=execute)
