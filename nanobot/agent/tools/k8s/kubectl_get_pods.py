"""kubectl get pods 工具 —— 根据组件名字关键字查询匹配的 Pod。"""

import re
from typing import Any

from nanobot.agent.tools.base import Tool
from nanobot.agent.tools.k8s._utils import run_command


class KubectlGetPodsTool(Tool):
    """根据组件名字关键字，从指定命名空间查询匹配的 Pod，支持排除关键字过滤。"""

    @property
    def name(self) -> str:
        return "kubectl_get_pods"

    @property
    def description(self) -> str:
        return (
            "根据组件名字关键字查询匹配的 Pod，返回 Pod 名称、命名空间、状态、节点等信息。"
            "支持指定命名空间和排除关键字过滤。"
        )

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "component_keyword": {
                    "type": "string",
                    "description": "组件名字关键字，用于 grep 过滤 Pod 列表，例如 'rocketmq-broker'",
                },
                "namespace": {
                    "type": "string",
                    "description": "（可选）指定命名空间，不填则查询所有命名空间 (-A)",
                },
                "exclude_keywords": {
                    "type": "string",
                    "description": "（可选）排除关键字，多个用逗号分隔，例如 'cmq,test' 表示排除包含 cmq 或 test 的行",
                },
            },
            "required": ["component_keyword"],
        }

    async def execute(
        self,
        component_keyword: str,
        namespace: str | None = None,
        exclude_keywords: str | None = None,
        **kwargs: Any,
    ) -> str:
        # 安全过滤，防止 shell 注入
        safe_keyword = re.sub(r"[;|&`$<>\\]", "", component_keyword)

        # 构建基础命令
        if namespace:
            safe_ns = re.sub(r"[;|&`$<>\\]", "", namespace)
            cmd = f"kubectl get pods -n {safe_ns} -o wide | grep {safe_keyword}"
        else:
            cmd = f"kubectl get pods -Ao wide | grep {safe_keyword}"

        # 添加排除关键字过滤
        if exclude_keywords:
            for exclude_kw in exclude_keywords.split(","):
                exclude_kw = exclude_kw.strip()
                if exclude_kw:
                    safe_exclude = re.sub(r"[;|&`$<>\\]", "", exclude_kw)
                    cmd += f" | grep -v {safe_exclude}"

        return await run_command(cmd)

    async def get_pod_list(
        self,
        component_keyword: str,
        namespace: str | None = None,
        exclude_keywords: str | None = None,
    ) -> list[dict[str, str]]:
        """获取 Pod 列表（供其他工具调用），返回 [{"namespace": str, "pod_name": str}, ...]"""
        output = await self.execute(component_keyword, namespace, exclude_keywords)

        if not output.strip() or output.startswith("Error"):
            return []

        pods = []
        for line in output.strip().splitlines():
            parts = line.strip().split()
            if len(parts) >= 2:
                # 如果指定了命名空间，第一列是 Pod 名称；否则第一列是命名空间，第二列是 Pod 名称
                if namespace:
                    pods.append({"namespace": namespace, "pod_name": parts[0]})
                else:
                    pods.append({"namespace": parts[0], "pod_name": parts[1]})
        return pods
