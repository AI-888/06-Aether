"""RCA 触发工具 - 注册到 ToolRegistry 的 RCA 入口。

允许 Agent 通过工具调用触发 RCA 根因分析流程。
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

from loguru import logger

from nanobot.agent.tools.base import Tool
from nanobot.rca.router import FaultInput


class RCATriggerTool(Tool):
    """RCA 分析触发工具。

    注册到 ToolRegistry 后，Agent 可以通过调用此工具触发
    RCA 根因分析流程。
    """

    def __init__(self, rca_router: Any = None):
        """初始化 RCA 触发工具。

        Args:
            rca_router: RCARouter 实例
        """
        self._router = rca_router

    @property
    def name(self) -> str:
        return "rca_analyze"

    @property
    def description(self) -> str:
        return (
            "触发 RCA（根因分析）流程。根据故障描述和类型，"
            "自动匹配对应的排障 Skill 并执行分步诊断，"
            "最终输出包含根因判断和修复建议的分析报告。"
        )

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "description": {
                    "type": "string",
                    "description": "故障描述文本，如日志内容、错误信息或问题描述",
                },
                "fault_type": {
                    "type": "string",
                    "description": "故障类型，可选值: log, metric, trace",
                    "enum": ["log", "metric", "trace", "general"],
                    "default": "general",
                },
                "skill_name": {
                    "type": "string",
                    "description": (
                        "指定要使用的 RCA Skill 名称（可选）。"
                        "不指定时将自动匹配最合适的 Skill。"
                    ),
                },
                "data": {
                    "type": "object",
                    "description": "附加数据（可选），如 log_text、metric_name 等",
                },
            },
            "required": ["description"],
        }

    async def execute(self, **kwargs: Any) -> str:
        """执行 RCA 分析。

        Args:
            description: 故障描述
            fault_type: 故障类型
            skill_name: 指定 Skill 名称（可选）
            data: 附加数据

        Returns:
            RCA 报告的 Markdown 格式字符串
        """
        if not self._router:
            return "错误: RCA 路由器未初始化，请检查 RCA 配置是否已启用"

        description = kwargs.get("description", "")
        fault_type = kwargs.get("fault_type", "general")
        skill_name = kwargs.get("skill_name")
        data = kwargs.get("data", {})

        if not description:
            return "错误: 缺少故障描述 (description)"

        try:
            if skill_name:
                # 按指定 Skill 名称执行
                report = await self._router.route_by_skill_name(
                    skill_name=skill_name,
                    inputs={"description": description, **(data or {})},
                )
            else:
                # 自动路由
                fault_input = FaultInput(
                    fault_type=fault_type,
                    description=description,
                    data=data or {},
                )
                report = await self._router.route(fault_input)

            # 返回 Markdown 报告
            return report.to_markdown()

        except Exception as e:
            logger.error(f"[RCA-TOOL] 执行失败: {e}")
            return f"RCA 分析执行失败: {str(e)}"


class RCAListSkillsTool(Tool):
    """列出已加载的 RCA Skill 工具。"""

    def __init__(self, skill_loader: Any = None):
        self._loader = skill_loader

    @property
    def name(self) -> str:
        return "rca_list_skills"

    @property
    def description(self) -> str:
        return "列出所有已加载的 RCA 排障 Skill 及其摘要信息。"

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {},
        }

    async def execute(self, **kwargs: Any) -> str:
        if not self._loader:
            return "RCA Skill 加载器未初始化"

        skills = self._loader.list_skills()
        if not skills:
            return "当前没有已加载的 RCA Skill"

        lines = [f"已加载 {len(skills)} 个 RCA Skill:\n"]
        for skill in skills:
            lines.append(
                f"- **{skill['name']}** v{skill['version']}: "
                f"{skill['description']} ({skill['steps_count']} 步骤)"
            )

        return "\n".join(lines)
