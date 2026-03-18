"""kubectl 查询日志工具 —— 根据组件名字关键字查找 Pod 并搜索日志。"""

import re
from typing import Any

from nanobot.agent.tools.base import Tool
from nanobot.agent.tools.k8s._utils import run_command
from nanobot.agent.tools.k8s.kubectl_get_pods import KubectlGetPodsTool


class KubectlQueryLogTool(Tool):
    """根据组件名字关键字、日志路径、日志关键字，查询匹配 Pod 中的日志。"""

    def __init__(self) -> None:
        self._get_pods_tool = KubectlGetPodsTool()

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
                "namespace": {
                    "type": "string",
                    "description": "（可选）指定命名空间，不填则查询所有命名空间",
                },
                "exclude_keywords": {
                    "type": "string",
                    "description": "（可选）排除关键字，多个用逗号分隔，例如 'cmq,test' 表示排除包含 cmq 或 test 的 Pod",
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
        namespace: str | None = None,
        exclude_keywords: str | None = None,
        container: str | None = None,
        lines: int = 50,
        **kwargs: Any,
    ) -> str:
        # 安全过滤，防止 shell 注入
        safe_log_kw = log_keyword.replace("'", "'\\''")  # 单引号转义
        safe_log_path = re.sub(r"[;|&`$<>\\]", "", log_path)

        # 步骤1：使用 kubectl_get_pods 工具获取 Pod 列表
        pods = await self._get_pods_tool.get_pod_list(
            component_keyword=component_keyword,
            namespace=namespace,
            exclude_keywords=exclude_keywords,
        )

        if not pods:
            filter_desc = f"关键字 '{component_keyword}'"
            if namespace:
                filter_desc += f" 命名空间 '{namespace}'"
            if exclude_keywords:
                filter_desc += f" 排除 '{exclude_keywords}'"
            return f"未找到匹配 {filter_desc} 的 Pod。"

        results = []
        filter_info = f"关键字 '{component_keyword}'"
        if namespace:
            filter_info += f"，命名空间 '{namespace}'"
        if exclude_keywords:
            filter_info += f"，排除 '{exclude_keywords}'"
        results.append(
            f"找到 {len(pods)} 个匹配的 Pod（{filter_info}），开始搜索日志关键字 '{log_keyword}'...\n"
        )

        for pod_info in pods:
            ns = pod_info["namespace"]
            pod_name = pod_info["pod_name"]

            # 确定容器名称
            target_container = container
            if not target_container:
                # 自动获取第一个非 sidecar 容器
                container_cmd = (
                    f"kubectl get pod {pod_name} -n {ns} "
                    r"-o jsonpath='{.spec.containers[*].name}'"
                )
                container_output = await run_command(container_cmd)
                if container_output.startswith("Error"):
                    results.append(f"[{ns}/{pod_name}] 获取容器列表失败: {container_output}\n")
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
                results.append(f"[{ns}/{pod_name}] 无法确定目标容器，跳过。\n")
                continue

            # 步骤2：在 Pod 内执行 find + grep 搜索日志
            exec_cmd = (
                f"kubectl exec {pod_name} -n {ns} -c {target_container} -- "
                f"sh -c 'find {safe_log_path} -name \"*.log\" ! -name \"metrics.log\" "
                f"-exec grep -l \"{safe_log_kw}\" {{}} \\; 2>/dev/null | head -10 | "
                f"xargs -I {{}} sh -c \"echo \\\"=== {{}} ===\"; "
                f"grep -n \\\"{safe_log_kw}\\\" {{}} | head -{lines}\"'"
            )
            exec_output = await run_command(exec_cmd)

            results.append(f"=== Pod: {ns}/{pod_name} (容器: {target_container}) ===")
            results.append(exec_output)
            results.append("")

        return "\n".join(results)
