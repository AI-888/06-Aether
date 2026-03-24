"""RCA 路由控制器。

集成 IntentClassifier 实现 A/D 两级意图分类路由：
- A 类（知识问答）：交由 LLM 直接回答
- D 类（操作/排查请求）：进入 Skill 执行流程

路由流程：
1. IntentClassifier 进行意图分类（规则优先 → LLM 备用）
2. A 类 → 直接返回知识问答报告
3. D 类 + 有匹配 Skill → RCAEngine 执行
4. D 类 + 无匹配 → 降级报告
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from loguru import logger

from nanobot.rca.audit import AuditLogger
from nanobot.rca.engine import RCAEngine
from nanobot.rca.intent import IntentClassifier, IntentResult
from nanobot.rca.loader import RCASkillLoader
from nanobot.rca.report import RCAReport
from nanobot.rca.rule_engine import RuleMatchEngine
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

    集成 IntentClassifier 实现 A/D 两级意图分类路由。

    - A 类（知识问答）：交由 LLM 直接回答
    - D 类（操作/排查请求）：进入 Skill 执行流程
    """

    def __init__(
        self,
        skill_loader: RCASkillLoader,
        engine: RCAEngine,
        intent_classifier: IntentClassifier,
        intent_store: Any | None = None,
        provider: Any | None = None,
        model: str | None = None,
    ):
        """初始化路由控制器。

        Args:
            skill_loader: RCA Skill 加载器
            engine: RCA 执行引擎
            intent_classifier: 意图分类器（A/D 两级分类）
            intent_store: IntentRoutingStore（用于 RAG 检索 Skill）
            provider: LLMProvider 实例（用于 A 类知识问答回答）
            model: LLM 模型名称
        """
        self.skill_loader = skill_loader
        self.engine = engine
        self.classifier = intent_classifier
        self.intent_store = intent_store
        self.provider = provider
        self.model = model

    async def route(self, fault_input: FaultInput) -> RCAReport:
        """路由故障到合适的处理器。

        流程:
        1. IntentClassifier 进行 A/D 两级意图分类
        2. A 类 → 直接调用 LLM 回答知识问答
        3. D 类 + 有匹配 Skill → 使用 RCAEngine 执行
        4. D 类 + 无匹配 Skill → RAG 检索备用
        5. 均未命中 → 返回降级报告

        Args:
            fault_input: 故障输入数据

        Returns:
            RCA 报告
        """
        query = fault_input.description
        logger.info(
            f"[RCA-ROUTER] 收到请求: type={fault_input.fault_type}, "
            f"query={query[:100]}..."
        )

        # 1. 意图分类
        intent = await self.classifier.classify(query)
        logger.info(
            f"[RCA-ROUTER] 意图分类结果: "
            f"type={intent.intent_type}, skill={intent.skill_name}, "
            f"method={intent.match_method}, confidence={intent.confidence}"
        )

        # 2. A 类 → 知识问答
        if intent.intent_type == "A":
            return await self._handle_knowledge_query(query)

        # 3. D 类 → 尝试执行 Skill
        return await self._handle_diagnostic_query(fault_input, intent)

    async def _handle_knowledge_query(self, query: str) -> RCAReport:
        """处理 A 类意图（知识问答）。

        直接调用 LLM 生成回答，不走 Skill 执行流程。

        Args:
            query: 用户查询文本

        Returns:
            包含 LLM 回答的 RCA 报告
        """
        logger.info(f"[RCA-ROUTER] A 类意图 → 知识问答: {query[:80]}...")

        if not self.provider:
            return RCAReport(
                fault_summary=query,
                root_cause="当前为知识问答类问题，但 LLM Provider 未配置",
                confidence=0.0,
                recommendations=["请配置 LLM Provider 以支持知识问答"],
            )

        try:
            messages = [
                {
                    "role": "system",
                    "content": (
                        "你是一个运维领域专家。请直接回答用户的技术问题，"
                        "回答要准确、简洁、实用。"
                    ),
                },
                {"role": "user", "content": query},
            ]

            response = await self.provider.chat(
                messages=messages,
                model=self.model,
            )

            answer = self._extract_content(response)

            return RCAReport(
                fault_summary=query,
                root_cause=f"[知识问答] {answer}",
                confidence=0.8,
                recommendations=[],
            )

        except Exception as e:
            logger.error(f"[RCA-ROUTER] 知识问答 LLM 调用失败: {e}")
            return RCAReport(
                fault_summary=query,
                root_cause=f"知识问答失败: {e}",
                confidence=0.0,
                recommendations=["LLM 调用异常，请稍后重试"],
            )

    async def _handle_diagnostic_query(
        self,
        fault_input: FaultInput,
        intent: IntentResult,
    ) -> RCAReport:
        """处理 D 类意图（操作/排查请求）。

        优先使用意图分类匹配的 Skill，
        回退到 RAG 检索，最终降级。

        Args:
            fault_input: 故障输入数据
            intent: 意图分类结果

        Returns:
            RCA 报告
        """
        # 3a. 意图分类已匹配到 Skill
        if intent.skill_name and intent.skill_name != "unsupported":
            skill = self.skill_loader.get_skill(intent.skill_name)
            if skill:
                logger.info(
                    f"[RCA-ROUTER] D 类意图 → Skill '{skill.name}' "
                    f"(method={intent.match_method})"
                )
                RCA_SKILL_MATCH_TOTAL.labels(matched="true").inc()
                inputs = self._build_skill_inputs(fault_input, skill)
                logger.debug(
                    f"[RCA-ROUTER][user_input追踪] 意图分类 → engine.execute, "
                    f"user_input={fault_input.description!r}"
                )
                return await self.engine.execute(
                    skill, inputs,
                    context={"user_input": fault_input.description},
                )
            else:
                logger.warning(
                    f"[RCA-ROUTER] 意图分类命中 '{intent.skill_name}'，"
                    f"但 Skill 未加载"
                )

        # 3b. 回退：RAG 检索
        skill = await self._search_skill_by_rag(fault_input)
        if skill:
            logger.info(f"[RCA-ROUTER] D 类意图 → RAG 匹配 Skill '{skill.name}'")
            RCA_SKILL_MATCH_TOTAL.labels(matched="true").inc()
            inputs = self._build_skill_inputs(fault_input, skill)
            logger.debug(
                f"[RCA-ROUTER][user_input追踪] RAG 匹配 → engine.execute, "
                f"user_input={fault_input.description!r}"
            )
            return await self.engine.execute(
                skill, inputs,
                context={"user_input": fault_input.description},
            )

        # 3c. 降级报告
        logger.warning("[RCA-ROUTER] D 类意图 → 未找到匹配的 Skill，返回降级报告")
        RCA_SKILL_MATCH_TOTAL.labels(matched="false").inc()
        return RCAReport(
            fault_summary=fault_input.description,
            root_cause="未能自动分析根因（无匹配的排障 Skill）",
            confidence=0.0,
            recommendations=[
                "建议人工介入排查",
                "考虑为此场景创建新的 RCA Skill",
            ],
        )

    def _build_skill_inputs(
        self,
        fault_input: FaultInput,
        skill: Any,
    ) -> dict[str, Any]:
        """构建 Skill 执行输入参数。

        将 FaultInput 的 data 映射到 Skill 的 input_schema。

        Args:
            fault_input: 故障输入数据
            skill: Skill 对象

        Returns:
            输入参数字典
        """
        inputs: dict[str, Any] = {**fault_input.data}
        # 对 input_schema 中未提供的字段，按类型填充默认空值
        # 避免设置 None 导致工具参数校验失败
        for key, schema_type in skill.input_schema.items():
            if key not in inputs:
                inputs[key] = self._get_default_value_by_schema_type(schema_type)
        return inputs

    @staticmethod
    def _get_default_value_by_schema_type(schema_type: Any) -> Any:
        """根据 schema 类型返回默认空值。"""
        if not isinstance(schema_type, str):
            return ""

        normalized_type = schema_type.strip().lower()

        if normalized_type in {"string", "str"}:
            return ""
        if normalized_type in {"list", "array"}:
            return []
        if normalized_type in {"dict", "object", "map"}:
            return {}
        if normalized_type in {"int", "integer"}:
            return 0
        if normalized_type in {"float", "number", "double"}:
            return 0.0
        if normalized_type in {"bool", "boolean"}:
            return False

        return ""

    async def _search_skill_by_rag(self, fault_input: FaultInput) -> Any:
        """通过 RAG 向量检索匹配 Skill。

        优先通过 IntentRoutingStore 检索，回退到关键词匹配。

        Args:
            fault_input: 故障输入数据

        Returns:
            匹配到的 Skill 对象，或 None
        """
        query = fault_input.description

        # 1. 尝试 RAG 向量检索
        if self.intent_store and hasattr(self.intent_store, "search_skills"):
            try:
                results = self.intent_store.search_skills(query, limit=4)
                if results:
                    # filter: 移除被 SOP 包含的 Atomic Skill
                    from nanobot.rca.skill_filter import filter_redundant_atomic_skills
                    filtered = filter_redundant_atomic_skills(results, self.skill_loader)
                    logger.info(
                        f"[RCA-ROUTER] Skill filter: {len(results)} → {len(filtered)} "
                        f"(移除 {len(results) - len(filtered)} 个冗余 Atomic)"
                    )
                    # 条件 rerank：结果 >= 2 条才 rerank
                    if len(filtered) >= 2:
                        filtered = self._distance_sort(filtered)
                    if filtered:
                        skill_name = filtered[0].get("metadata", {}).get(
                            "skill_name", ""
                        )
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
            if (
                name.lower() in query_lower
                or skill.description.lower() in query_lower
                or any(kw in query_lower for kw in name.lower().split("_"))
            ):
                return skill

        # 3. 如果只有一个 SOP Skill，直接返回
        sop_skills = {
            n: s for n, s in all_skills.items()
            if hasattr(s, "steps")
        }
        if len(sop_skills) == 1:
            return next(iter(sop_skills.values()))

        return None

    async def route_by_skill_name(
        self,
        skill_name: str,
        inputs: dict[str, Any],
    ) -> RCAReport:
        """按 Skill 名称直接路由执行（跳过意图分类）。

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
                    "请检查 Skill 名称是否正确",
                    f"已加载的 Skill: "
                    f"{[s['name'] for s in self.skill_loader.list_skills()]}",
                ],
            )

        RCA_SKILL_MATCH_TOTAL.labels(matched="true").inc()
        return await self.engine.execute(skill, inputs)

    @staticmethod
    def _distance_sort(results: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """按向量距离排序（距离越小越相关）。"""
        return sorted(
            results,
            key=lambda x: x.get("distance") if x.get("distance") is not None else 1e9,
        )

    @staticmethod
    def _extract_content(response: Any) -> str:
        """从 LLM 响应中提取文本内容。"""
        if isinstance(response, str):
            return response
        if hasattr(response, "content"):
            return str(response.content)
        if isinstance(response, dict):
            if "content" in response:
                return str(response["content"])
            if "choices" in response:
                choices = response["choices"]
                if choices and isinstance(choices, list):
                    msg = choices[0].get("message", {})
                    return str(msg.get("content", ""))
        return str(response)