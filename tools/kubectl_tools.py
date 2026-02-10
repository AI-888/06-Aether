"""
Kubernetes kubectl tools.
"""

from datetime import datetime
from typing import Dict, Optional

from tools.shell_mysql_tool import run_shell


def run_kubectl(
        subcommand: str,
        execute: bool = False,
) -> Dict[str, str]:
    """Build (and optionally run) kubectl command based on skill file patterns."""
    # 使用技能文件中的通用命令模式，而不是硬编码
    cmd = f"kubectl {subcommand}".strip()
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{ts}] [Tool Call] kubectl input: {{'cmd': '{cmd}', 'execute': {execute}}}")
    if not execute:
        result = {"command": cmd, "output": "", "executed": "false"}
        ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        print(f"[{ts}] [Tool Call] kubectl output: {{'executed': 'false', 'command': '{cmd}'}}")
        return result

    output = run_shell(cmd)
    result = {"command": cmd, "output": output, "executed": "true"}
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    preview = output.replace("\n", " ")[:300]
    print(
        f"[{ts}] [Tool Call] kubectl output: {{'executed': 'true', 'command': '{cmd}', 'output_preview': '{preview}'}}")
    return result


__all__ = [
    "run_kubectl",
]