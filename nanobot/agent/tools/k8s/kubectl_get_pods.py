"""kubectl get pods 工具 —— 根据组件名字关键字查询匹配的 Pod。"""

import re
from typing import Any

from nanobot.agent.tools.base import Tool
from nanobot.agent.tools.k8s._utils import run_command


class KubectlGetPodsTool(Tool):
    """根据组件名字关键字，从全部命名空间中查询匹配的 Pod。"""

    @property
    def name(self) -> str:
        return "kubectl_get_pods"

    @property
    def description(self) -> str:
        return (
            "根据组件名字关键字，从全部命名空间中查询匹配的 Pod，"
            "返回 Pod 名称、命名空间、状态、节点等信息。"
            "等价于：kubectl get pods -Ao wide | grep <component_keyword>"
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
            },
            "required": ["component_keyword"],
        }

    async def execute(self, component_keyword: str, **kwargs: Any) -> str:
        # 对关键字做基本安全过滤，防止 shell 注入
        safe_keyword = re.sub(r"[;|&`$<>\\]", "", component_keyword)
        cmd = f"kubectl get pods -Ao wide | grep {safe_keyword}"
        return await run_command(cmd)
