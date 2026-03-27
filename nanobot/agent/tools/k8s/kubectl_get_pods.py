from __future__ import annotations
"""kubectl get pods 工具 —— 根据组件名字关键字查询匹配的 Pod。"""

import asyncio
import json
import re
from typing import Any

from loguru import logger

from nanobot.agent.tools.base import Tool
from nanobot.agent.tools.k8s._utils import run_command

# 获取单个 Pod JSON 详情的并发上限
_MAX_CONCURRENT_POD_DETAIL = 10
# 最多获取 JSON 详情的 Pod 数量
_MAX_POD_DETAIL_COUNT = 20


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
    ) -> dict[str, Any]:
        """执行工具：先 grep 获取 Pod 列表，再逐个获取 JSON 详情。

        Returns:
            dict: {
                "result": str,       # 原始文本输出（供 LLM / 人类阅读）
                "commands": list,    # 本次执行的所有命令列表
                "pods": list,        # 结构化 Pod 列表
                "total": int,        # Pod 总数
            }
        """
        commands_executed: list[str] = []

        # ---------- 步骤1：用 grep 获取匹配的 Pod 列表 ----------
        safe_keyword = re.sub(r"[;|&`$<>\\]", "", component_keyword)

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

        logger.info(f"[{self.name}] 🔧 工具命令: {cmd}")
        commands_executed.append(cmd)

        grep_output = await run_command(cmd)
        logger.debug(f"[{self.name}] 📋 命令: {cmd}")
        logger.debug(f"[{self.name}] 📋 结果:\n{grep_output}")

        # 如果 grep 没有结果或出错，直接返回
        if not grep_output.strip() or grep_output.startswith("Error"):
            return {
                "result": grep_output or "(无匹配的 Pod)",
                "commands": commands_executed,
                "pods": [],
                "total": 0,
            }

        # ---------- 步骤2：解析 grep 输出，提取 Pod 名称列表 ----------
        pod_entries: list[dict[str, str]] = []
        for line in grep_output.strip().splitlines():
            parts = line.strip().split()
            if len(parts) >= 2:
                if namespace:
                    pod_entries.append({"namespace": namespace, "pod_name": parts[0]})
                else:
                    pod_entries.append({"namespace": parts[0], "pod_name": parts[1]})

        if not pod_entries:
            return {
                "result": grep_output,
                "commands": commands_executed,
                "pods": [],
                "total": 0,
            }

        # ---------- 步骤3：逐个 Pod 获取 JSON 详情 ----------
        truncated = False
        if len(pod_entries) > _MAX_POD_DETAIL_COUNT:
            logger.warning(
                f"[{self.name}] Pod 数量 {len(pod_entries)} 超过上限 {_MAX_POD_DETAIL_COUNT}，"
                f"仅获取前 {_MAX_POD_DETAIL_COUNT} 个 Pod 的 JSON 详情"
            )
            pod_entries = pod_entries[:_MAX_POD_DETAIL_COUNT]
            truncated = True

        semaphore = asyncio.Semaphore(_MAX_CONCURRENT_POD_DETAIL)

        async def _fetch_pod_json(entry: dict[str, str]) -> dict[str, Any] | None:
            """获取单个 Pod 的 JSON 详情并提取关键字段。"""
            ns = entry["namespace"]
            pod_name = entry["pod_name"]
            detail_cmd = f"kubectl get pod {pod_name} -n {ns} -o json"
            commands_executed.append(detail_cmd)
            logger.debug(f"[{self.name}] 📋 命令: {detail_cmd}")

            async with semaphore:
                raw = await run_command(detail_cmd)
            logger.debug(f"[{self.name}] 📋 结果(前500字符): {raw if raw else '(空)'}")

            if not raw or raw.startswith("Error"):
                logger.warning(f"[{self.name}] 获取 Pod {ns}/{pod_name} JSON 详情失败: {raw[:200]}")
                return None

            try:
                pod_json = json.loads(raw)
            except (json.JSONDecodeError, ValueError) as e:
                logger.warning(f"[{self.name}] 解析 Pod {ns}/{pod_name} JSON 失败: {e}")
                return None

            return self._extract_pod_info(pod_json)

        tasks = [_fetch_pod_json(entry) for entry in pod_entries]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        pods: list[dict[str, Any]] = []
        for r in results:
            if isinstance(r, dict):
                pods.append(r)
            elif isinstance(r, Exception):
                logger.warning(f"[{self.name}] 获取 Pod JSON 详情异常: {r}")

        # ---------- 构建返回结果 ----------
        result_text = grep_output
        if truncated:
            result_text += f"\n\n(注意: Pod 数量超过 {_MAX_POD_DETAIL_COUNT}，仅展示前 {_MAX_POD_DETAIL_COUNT} 个)"

        return {
            "result": result_text,
            "commands": commands_executed,
            "pods": pods,
            "total": len(pods),
        }

    @staticmethod
    def _extract_pod_info(pod_json: dict[str, Any]) -> dict[str, Any]:
        """从 kubectl -o json 输出中提取关键 Pod 信息。"""
        metadata = pod_json.get("metadata", {})
        spec = pod_json.get("spec", {})
        status = pod_json.get("status", {})

        # 容器状态
        container_statuses = status.get("containerStatuses", [])
        main_status = container_statuses[0] if container_statuses else {}

        # 计算总重启次数
        total_restarts = sum(cs.get("restartCount", 0) for cs in container_statuses)

        # 计算所有容器是否都 ready
        all_ready = all(cs.get("ready", False) for cs in container_statuses) if container_statuses else False
        ready_count = sum(1 for cs in container_statuses if cs.get("ready", False))
        total_containers = len(container_statuses)

        return {
            "name": metadata.get("name", ""),
            "namespace": metadata.get("namespace", ""),
            "status": status.get("phase", "Unknown"),
            "ip": status.get("podIP", ""),
            "node": spec.get("nodeName", ""),
            "restarts": total_restarts,
            "ready": all_ready,
            "ready_str": f"{ready_count}/{total_containers}",
            "start_time": status.get("startTime", ""),
            "containers": [
                {
                    "name": cs.get("name", ""),
                    "image": cs.get("image", ""),
                    "ready": cs.get("ready", False),
                    "restart_count": cs.get("restartCount", 0),
                }
                for cs in container_statuses
            ],
        }

    async def get_pod_list(
        self,
        component_keyword: str,
        namespace: str | None = None,
        exclude_keywords: str | None = None,
    ) -> list[dict[str, str]]:
        """获取 Pod 列表（供其他工具调用），返回 [{"namespace": str, "pod_name": str}, ...]"""
        result = await self.execute(component_keyword, namespace, exclude_keywords)

        # 从结构化结果中提取 Pod 名称列表
        pods_info = result.get("pods", [])
        if pods_info:
            return [
                {"namespace": p.get("namespace", ""), "pod_name": p.get("name", "")}
                for p in pods_info
            ]

        # 回退：从原始文本解析（兼容 JSON 详情获取失败的场景）
        raw_output = result.get("result", "")
        if not raw_output.strip() or raw_output.startswith("Error"):
            return []

        pods = []
        for line in raw_output.strip().splitlines():
            parts = line.strip().split()
            if len(parts) >= 2:
                if namespace:
                    pods.append({"namespace": namespace, "pod_name": parts[0]})
                else:
                    pods.append({"namespace": parts[0], "pod_name": parts[1]})
        return pods
