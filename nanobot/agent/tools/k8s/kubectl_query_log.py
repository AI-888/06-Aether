from __future__ import annotations

"""kubectl 查询日志工具 —— 根据组件名字关键字查找 Pod 并搜索日志。"""

import json
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
                "pod_keyword": {
                    "type": "string",
                    "description": "pod名关键字",
                },
                "log_paths": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Pod 内日志文件的搜索路径，支持多个路径用空格分隔，例如 '/usr/local/services/app/ /root/logs/'",
                },
                "log_keywords": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "日志内容关键字，用于在日志文件中 grep 搜索，例如 'ERROR' 或 'OutOfMemory'",
                },
                "namespace": {
                    "type": "string",
                    "description": "（可选）指定命名空间，不填则查询所有命名空间",
                },
                "exclude_keywords": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "（可选）排除关键字，表示排除包含 cmq 和 test",
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
            "required": ["pod_keyword", "log_paths", "log_keywords"],
        }

    async def execute(
            self,
            pod_keyword: str,
            log_paths: list[str],
            log_keywords: list[str],
            namespace: str | None = None,
            exclude_keywords: list[str] | None = None,
            container: str | None = None,
            lines: int = 50,
            **kwargs: Any,
    ) -> dict[str, Any]:
        """执行日志查询工具。

        Returns:
            dict: {"result": str, "commands": list[str]}
        """
        commands_executed: list[str] = []

        # 参数清洗
        safe_lines = lines if isinstance(lines, int) and lines > 0 else 50

        safe_log_paths = [
            re.sub(r"[;|&`$<>\\\"']", "", (p or "")).strip()
            for p in log_paths
            if isinstance(p, str) and p.strip()
        ]
        if not safe_log_paths:
            return self.make_result("log_paths 不能为空，且至少包含一个有效路径。", commands_executed)

        normalized_keywords = [
            re.sub(r"[;|&`$<>\"']", "", (kw or "")).strip()
            for kw in log_keywords
            if isinstance(kw, str) and kw.strip()
        ]
        if not normalized_keywords:
            return self.make_result("log_keywords 不能为空，且至少包含一个有效关键字。", commands_executed)

        safe_kw_pattern = "|".join(re.escape(kw) for kw in normalized_keywords)
        safe_kw_pattern = safe_kw_pattern.replace("\\", "\\\\").replace('"', '\\"')

        normalized_excludes = [
            re.sub(r"[;|&`$<>\"']", "", (kw or "")).strip()
            for kw in (exclude_keywords or [])
            if isinstance(kw, str) and kw.strip()
        ]
        exclude_text = ",".join(normalized_excludes) if normalized_excludes else None

        # 步骤1：使用 kubectl_get_pods 工具获取 Pod 列表
        pods = await self._get_pods_tool.get_pod_list(
            pod_keyword=pod_keyword,
            namespace=namespace,
            exclude_keywords=exclude_text,
        )

        if not pods:
            filter_desc = f"关键字 '{pod_keyword}'"
            if namespace:
                filter_desc += f" 命名空间 '{namespace}'"
            if exclude_text:
                filter_desc += f" 排除 '{exclude_text}'"
            return self.make_result(f"未找到匹配 {filter_desc} 的 Pod。", commands_executed)

        structured_results: list[dict[str, Any]] = []

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
                commands_executed.append(container_cmd)
                container_output = await run_command(container_cmd)
                if container_output.startswith("Error"):
                    continue

                # 过滤 sidecar 容器（istio-proxy、envoy、filebeat 等）
                sidecar_patterns = re.compile(
                    r"(istio-proxy|envoy|filebeat|fluentd|logrotate|sidecar|agent)",
                    re.IGNORECASE,
                )
                all_containers = container_output.strip().split()
                main_containers = [c for c in all_containers if not sidecar_patterns.search(c)]
                target_container = main_containers[0] if main_containers else (
                    all_containers[0] if all_containers else None)

            if not target_container:
                continue

            # 步骤2：在 Pod 内执行 find + grep 搜索日志（遍历多个路径）
            for path in safe_log_paths:
                inner_cmd = (
                    f"find {path} -type f -name \"*.log\" ! -name \"metrics.log\" 2>/dev/null | head -10 | "
                    f"xargs -I {{}} sh -c \"echo \\\"=== {{}} ===\\\"; "
                    f"grep -n -E \\\"{safe_kw_pattern}\\\" \\\"{{}}\\\" | head -{safe_lines}\""
                )
                exec_cmd = (
                    f"kubectl exec {pod_name} -n {ns} -c {target_container} -- "
                    f"sh -c '{inner_cmd}'"
                )
                commands_executed.append(exec_cmd)
                exec_output = await run_command(exec_cmd)

                current_file_path: str | None = None
                current_matches: list[dict[str, Any]] = []

                for line in (exec_output or "").splitlines():
                    line = line.rstrip()
                    if not line:
                        continue

                    file_header_match = re.match(r"^===\s*(.*?)\s*===\s*$", line)
                    if file_header_match:
                        if current_file_path:
                            structured_results.append(
                                {
                                    "pod": f"{ns}/{pod_name}",
                                    "container": target_container,
                                    "search_path": path,
                                    "log_file_path": current_file_path,
                                    "matched_keywords": normalized_keywords,
                                    "matches": current_matches,
                                }
                            )
                        current_file_path = file_header_match.group(1)
                        current_matches = []
                        continue

                    match_line = re.match(r"^(\d+):(.*)$", line)
                    if match_line and current_file_path:
                        current_matches.append(
                            {
                                "line_number": int(match_line.group(1)),
                                "content": match_line.group(2).lstrip(),
                            }
                        )

                if current_file_path:
                    structured_results.append(
                        {
                            "pod": f"{ns}/{pod_name}",
                            "container": target_container,
                            "search_path": path,
                            "log_file_path": current_file_path,
                            "matched_keywords": normalized_keywords,
                            "matches": current_matches,
                        }
                    )

        return self.make_result(
            json.dumps(structured_results, ensure_ascii=False),
            commands_executed,
        )
