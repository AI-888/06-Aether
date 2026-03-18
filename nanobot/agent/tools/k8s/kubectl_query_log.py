"""kubectl 查询日志工具 —— 根据组件名字关键字查找 Pod 并搜索日志。"""

import re
from typing import Any

from nanobot.agent.tools.base import Tool
from nanobot.agent.tools.k8s._utils import run_command


class KubectlQueryLogTool(Tool):
    """根据组件名字关键字、日志路径、日志关键字，查询匹配 Pod 中的日志。"""

    @property
    def name(self) -> str:
        return "kubectl_query_log"

    @property
    def description(self) -> str:
        return (
            "根据组件名字关键字查找匹配的 Pod，然后在 Pod 内执行 find+grep 搜索日志文件中的关键字，"
            "返回匹配的日志行。会自动跳过 sidecar 容器（istio-proxy 等），使用业务主容器执行命令。"
            "适用于需要在 Pod 内部搜索日志文件的场景。"
        )

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "component_keyword": {
                    "type": "string",
                    "description": "组件名字关键字，用于 grep 过滤 Pod 列表，例如 'rocketmq-manager'",
                },
                "log_path": {
                    "type": "string",
                    "description": "Pod 内日志文件的搜索路径，支持多个路径用空格分隔，例如 '/usr/local/services/app/ /root/logs/'",
                },
                "log_keyword": {
                    "type": "string",
                    "description": "日志内容关键字，用于在日志文件中 grep 搜索，例如 'ERROR' 或 'OutOfMemory'",
                },
                "container": {
                    "type": "string",
                    "description": "（可选）指定容器名称。若不填，系统会自动获取 Pod 中第一个非 sidecar 容器",
                },
                "lines": {
                    "type": "integer",
                    "description": "每个日志文件最多返回的匹配行数，默认 50",
                    "default": 50,
                },
            },
            "required": ["component_keyword", "log_path", "log_keyword"],
        }

    async def execute(
        self,
        component_keyword: str,
        log_path: str,
        log_keyword: str,
        container: str | None = None,
        lines: int = 50,
        **kwargs: Any,
    ) -> str:
        # 安全过滤，防止 shell 注入
        safe_kw = re.sub(r"[;|&`$<>\\]", "", component_keyword)
        safe_log_kw = log_keyword.replace("'", "'\\''")  # 单引号转义
        safe_log_path = re.sub(r"[;|&`$<>\\]", "", log_path)

        # 步骤1：获取匹配的 Pod 列表（namespace + pod_name）
        list_cmd = (
            f"kubectl get pods -A | grep {safe_kw} | "
            r"awk -F ' ' '{ print $1 \" \" $2 }'"
        )
        pod_list_output = await run_command(list_cmd)

        if not pod_list_output.strip() or pod_list_output.startswith("Error"):
            return f"未找到匹配关键字 '{component_keyword}' 的 Pod。\n{pod_list_output}"

        # 解析 Pod 列表
        pods = []
        for line in pod_list_output.strip().splitlines():
            parts = line.strip().split()
            if len(parts) >= 2:
                pods.append({"namespace": parts[0], "pod_name": parts[1]})

        if not pods:
            return f"未找到匹配关键字 '{component_keyword}' 的 Pod。"

        results = []
        results.append(
            f"找到 {len(pods)} 个匹配的 Pod，开始搜索日志关键字 '{log_keyword}'...\n"
        )

        for pod_info in pods:
            namespace = pod_info["namespace"]
            pod_name = pod_info["pod_name"]

            # 确定容器名称
            target_container = container
            if not target_container:
                # 自动获取第一个非 sidecar 容器
                container_cmd = (
                    f"kubectl get pod {pod_name} -n {namespace} "
                    r"-o jsonpath='{.spec.containers[*].name}'"
                )
                container_output = await run_command(container_cmd)
                if container_output.startswith("Error"):
                    results.append(f"[{namespace}/{pod_name}] 获取容器列表失败: {container_output}\n")
                    continue

                # 过滤 sidecar 容器（istio-proxy、envoy、filebeat 等）
                sidecar_patterns = re.compile(
                    r"(istio-proxy|envoy|filebeat|fluentd|logrotate|sidecar|agent)",
                    re.IGNORECASE,
                )
                all_containers = container_output.strip().split()
                main_containers = [c for c in all_containers if not sidecar_patterns.search(c)]
                target_container = main_containers[0] if main_containers else (all_containers[0] if all_containers else None)

            if not target_container:
                results.append(f"[{namespace}/{pod_name}] 无法确定目标容器，跳过。\n")
                continue

            # 步骤2：在 Pod 内执行 find + grep 搜索日志
            exec_cmd = (
                f"kubectl exec {pod_name} -n {namespace} -c {target_container} -- "
                f"sh -c 'find {safe_log_path} -name \"*.log\" ! -name \"metrics.log\" "
                f"-exec grep -l \"{safe_log_kw}\" {{}} \\; 2>/dev/null | head -10 | "
                f"xargs -I {{}} sh -c \"echo \\\"=== {{}} ===\"; "
                f"grep -n \\\"{safe_log_kw}\\\" {{}} | head -{lines}\"'"
            )
            exec_output = await run_command(exec_cmd)

            results.append(f"=== Pod: {namespace}/{pod_name} (容器: {target_container}) ===")
            results.append(exec_output)
            results.append("")

        return "\n".join(results)
