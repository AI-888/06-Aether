"""RCA 路由控制器。

根据故障类型调度对应的专用分析 Agent 或 Skill，
汇聚结果生成统一的 RCA 报告。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from loguru import logger

from nanobot.rca.audit import AuditLogger
from nanobot.rca.engine import RCAEngine
from nanobot.rca.loader import RCASkillLoader
from nanobot.rca.report import RCAReport
from nanobot.rca.security import SecurityGuard
from nanobot.metrics import RCA_SKILL_MATCH_TOTAL


@dataclass
class FaultInput:
    """故障输入数据。

    Attributes:
        fault_type: 故障类型（如 "log", "metric", "trace"）
        description: 故障描述文本
        data: 附加数据（如日志文本、指标值等）
    """
    fault_type: str = ""
    description: str = ""
    data: dict[str, Any] = field(default_factory=dict)


class RCARouter:
    """RCA 路由控制器。

    根据故障类型调度对应的专用分析 Agent 或 Skill。
    """

    # 故障维度 -> Agent 映射（预留）
    AGENT_ROUTES = {
        "log": "log_analysis_agent",
        "metric": "metric_analysis_agent",
        "trace": "trace_analysis_agent",
    }

    def __init__(
        self,
        skill_loader: RCASkillLoader,
        engine: RCAEngine,
        intent_store: Any | None = None,
    ):
        """初始化路由控制器。

        Args:
            skill_loader: RCA Skill 加载器
            engine: RCA 执行引擎
            intent_store: IntentRoutingStore（用于 RAG 检索 Skill）
        """
        self.skill_loader = skill_loader
        self.engine = engine
        self.intent_store = intent_store

    async def route(self, fault_input: FaultInput) -> RCAReport:
        """路由故障到合适的处理器。

        流程:
        1. 尝试 RAG 检索匹配 Skill
        2. 如果有精确匹配的 Skill → 使用 RCAEngine 执行
        3. 如果无匹配 → 返回降级报告

        Args:
            fault_input: 故障输入数据

        Returns:
            RCA 报告
        """
        logger.info(
            f"[RCA-ROUTER] 收到故障: type={fault_input.fault_type}, "
            f"description={fault_input.description[:100]}..."
        )

        # 1. 尝试 RAG 检索
        skill = await self._search_skill(fault_input)

        if skill:
            # 2. 使用 RCAEngine 执行匹配到的 Skill
            logger.info(f"[RCA-ROUTER] 匹配到 Skill: {skill.name}")
            RCA_SKILL_MATCH_TOTAL.labels(matched="true").inc()
            inputs = {**fault_input.data}
            # 将 description 映射到 Skill 的 input_schema 字段
            for key in skill.input_schema:
                if key not in inputs:
                    inputs[key] = fault_input.description

            return await self.engine.execute(skill, inputs)

        # 3. 无匹配 → 降级报告
        logger.warning("[RCA-ROUTER] 未找到匹配的 Skill，返回降级报告")
        RCA_SKILL_MATCH_TOTAL.labels(matched="false").inc()
        return RCAReport(
            fault_summary=fault_input.description,
            root_cause="未能自动分析根因（无匹配的排障 Skill）",
            confidence=0.0,
            recommendations=["建议人工介入排查", "考虑为此场景创建新的 RCA Skill"],
        )

    async def _search_skill(self, fault_input: FaultInput) -> Any:
        """搜索匹配的 Skill。

        优先通过 RAG 检索，回退到按名称精确匹配。
        """
        query = fault_input.description

        # 1. 尝试 RAG 向量检索
        if self.intent_store and hasattr(self.intent_store, "search_rca_skill"):
            try:
                results = self.intent_store.search_rca_skill(query, limit=1)
                if results:
                    skill_name = results[0].get("metadata", {}).get("skill_name", "")
                    if skill_name:
                        skill = self.skill_loader.get_skill(skill_name)
                        if skill:
                            return skill
            except Exception as e:
                logger.warning(f"[RCA-ROUTER] RAG 检索失败: {e}")

        # 2. 回退：遍历所有 Skill，按描述关键词匹配
        all_skills = self.skill_loader.get_all_skills()
        query_lower = query.lower()

        for name, skill in all_skills.items():
            # 简单关键词匹配
            if (
                name.lower() in query_lower
                or skill.description.lower() in query_lower
                or any(kw in query_lower for kw in name.lower().split("_"))
            ):
                return skill

        # 3. 如果只有一个 Skill，直接返回
        if len(all_skills) == 1:
            return next(iter(all_skills.values()))

        return None

    async def route_by_skill_name(
        self,
        skill_name: str,
        inputs: dict[str, Any],
    ) -> RCAReport:
        """按 Skill 名称直接路由执行。

        Args:
            skill_name: Skill 名称
            inputs: 输入参数

        Returns:
            RCA 报告
        """
        skill = self.skill_loader.get_skill(skill_name)
        if not skill:
            return RCAReport(
                fault_summary=f"未找到 Skill: {skill_name}",
                root_cause=f"Skill '{skill_name}' 不存在",
                confidence=0.0,
                recommendations=[
                    f"请检查 Skill 名称是否正确",
                    f"已加载的 Skill: {[s['name'] for s in self.skill_loader.list_skills()]}",
                ],
            )

        return await self.engine.execute(skill, inputs)
