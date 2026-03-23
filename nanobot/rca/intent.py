"""RCA 意图分类器。

实现 A/D 两级意图分类：
- A 类（知识问答）：交由 LLM 直接回答
- D 类（操作/排查请求）：进入 Skill 执行流程

执行模式采用两阶段：
- 阶段一：规则匹配（优先，毫秒级）
- 阶段二：LLM 快速分类（备用，仅选 Skill）
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from loguru import logger

from nanobot.metrics import RCA_INTENT_CLASSIFY_TOTAL
from nanobot.rca.rule_engine import RuleMatchEngine


# A 类意图识别关键词（知识问答类请求特征）
_A_CLASS_PATTERNS: list[str] = [
    "是什么",
    "什么是",
    "介绍一下",
    "解释一下",
    "有什么区别",
    "原理",
    "概念",
    "怎么理解",
    "如何理解",
    "what is",
    "explain",
    "describe",
    "definition",
    "difference between",
]


@dataclass
class IntentResult:
    """意图分类结果。

    Attributes:
        intent_type: 意图类型 "A"（知识问答）或 "D"（操作/排查）
        skill_name: 匹配到的 Skill 名称（仅 D 类有值）
        match_method: 匹配方法 "rule" / "llm" / None
        confidence: 分类置信度（0.0 ~ 1.0）
    """
    intent_type: str = "D"
    skill_name: str | None = None
    match_method: str | None = None
    confidence: float = 1.0


# LLM 分类用 Prompt 模板
LLM_CLASSIFY_PROMPT = """你是一个运维助手，请从以下技能中选择最匹配的一项：
{skill_list}

如果都不匹配，回答 "unsupported"。
只输出技能名称或 "unsupported"，不要输出其他内容。

用户问题："{query}"
"""


class IntentClassifier:
    """两级意图分类器。

    阶段一：规则匹配（毫秒级）
    阶段二：LLM 快速分类（备用）

    LLM 仅用于分类，不参与执行、不生成步骤、不推理根因。
    """

    def __init__(
        self,
        rule_engine: RuleMatchEngine,
        provider: Any = None,
        skill_names: list[str] | None = None,
        model: str | None = None,
        a_class_patterns: list[str] | None = None,
    ):
        """初始化意图分类器。

        Args:
            rule_engine: 规则匹配引擎实例
            provider: LLMProvider 实例（可选，不提供则禁用 LLM 分类）
            skill_names: 所有已注册的 Skill 名称列表
            model: LLM 分类使用的模型名称
            a_class_patterns: 自定义 A 类意图识别关键词列表
        """
        self.rule_engine = rule_engine
        self.provider = provider
        self.skill_names = skill_names or []
        self.model = model
        self._a_patterns = a_class_patterns or _A_CLASS_PATTERNS

    def update_skill_names(self, skill_names: list[str]) -> None:
        """更新已注册的 Skill 名称列表（热加载时调用）。

        Args:
            skill_names: 最新的 Skill 名称列表
        """
        self.skill_names = list(skill_names)
        logger.debug(
            f"[RCA-INTENT] 更新 Skill 列表: {len(self.skill_names)} 个"
        )

    async def classify(self, query: str) -> IntentResult:
        """意图分类主方法。

        分类流程：
        1. 判断是否为 A 类（知识问答）
        2. D 类 → 阶段一：规则匹配
        3. D 类 → 阶段二：LLM 分类（备用）
        4. 均未命中 → 返回 unsupported

        Args:
            query: 用户查询文本

        Returns:
            IntentResult 分类结果
        """
        # 1. A 类识别
        if self._is_a_class(query):
            logger.info(f"[RCA-INTENT] A 类意图（知识问答）: {query[:50]}...")
            return IntentResult(
                intent_type="A",
                skill_name=None,
                match_method=None,
                confidence=0.8,
            )

        # 2. D 类 → 阶段一：规则匹配
        matched_skill = self._rule_match(query)
        if matched_skill:
            logger.info(
                f"[RCA-INTENT] D 类意图（规则命中）: "
                f"{query[:50]}... → {matched_skill}"
            )
            RCA_INTENT_CLASSIFY_TOTAL.labels(method="rule").inc()
            return IntentResult(
                intent_type="D",
                skill_name=matched_skill,
                match_method="rule",
                confidence=1.0,
            )

        # 3. D 类 → 阶段二：LLM 分类
        llm_skill = await self._llm_classify(query)
        if llm_skill and llm_skill != "unsupported":
            logger.info(
                f"[RCA-INTENT] D 类意图（LLM 分类）: "
                f"{query[:50]}... → {llm_skill}"
            )
            RCA_INTENT_CLASSIFY_TOTAL.labels(method="llm").inc()
            return IntentResult(
                intent_type="D",
                skill_name=llm_skill,
                match_method="llm",
                confidence=0.7,
            )

        # 4. 均未命中 → 不支持
        logger.info(
            f"[RCA-INTENT] 意图未匹配（unsupported）: {query[:50]}..."
        )
        return IntentResult(
            intent_type="D",
            skill_name=None,
            match_method=None,
            confidence=0.0,
        )

    def _is_a_class(self, query: str) -> bool:
        """判断是否为 A 类意图（知识问答）。

        通过关键词模式匹配识别知识类问题。

        Args:
            query: 用户查询文本

        Returns:
            True 表示 A 类意图
        """
        query_lower = query.lower().strip()
        for pattern in self._a_patterns:
            if pattern.lower() in query_lower:
                return True
        return False

    def _rule_match(self, query: str) -> str | None:
        """阶段一：规则匹配。

        使用 RuleMatchEngine 进行正则/关键词快速匹配。
        毫秒级响应。

        Args:
            query: 用户查询文本

        Returns:
            匹配到的 skill_name 或 None
        """
        return self.rule_engine.match(query)

    async def _llm_classify(self, query: str) -> str | None:
        """阶段二：LLM 快速分类。

        将用户 Query 提交给 LLM，从预定义的 Skill 列表中选择
        最匹配的一项，或返回 "unsupported"。

        LLM 在此阶段仅用于分类，不参与执行、不生成步骤、不推理根因。

        Args:
            query: 用户查询文本

        Returns:
            skill_name / "unsupported" / None（LLM 不可用时）
        """
        if not self.provider:
            logger.debug("[RCA-INTENT] LLM provider 未配置，跳过 LLM 分类")
            return None

        if not self.skill_names:
            logger.debug("[RCA-INTENT] Skill 列表为空，跳过 LLM 分类")
            return None

        try:
            # 构建 Skill 列表文本
            skill_list_text = "\n".join(
                f"- {name}" for name in self.skill_names
            )

            prompt = LLM_CLASSIFY_PROMPT.format(
                skill_list=skill_list_text,
                query=query,
            )

            messages = [
                {"role": "user", "content": prompt},
            ]

            response = await self.provider.chat(
                messages=messages,
                model=self.model,
            )

            # 提取响应文本
            result_text = self._extract_content(response).strip()

            # 清理可能的引号和空白
            result_text = result_text.strip("\"'`\n\r\t ")

            logger.debug(f"[RCA-INTENT] LLM 分类结果: '{result_text}'")

            # 校验结果是否为已知 Skill
            if result_text == "unsupported":
                return "unsupported"

            # 精确匹配
            if result_text in self.skill_names:
                return result_text

            # 忽略大小写匹配
            result_lower = result_text.lower()
            for name in self.skill_names:
                if name.lower() == result_lower:
                    return name

            # LLM 返回了无法识别的内容
            logger.warning(
                f"[RCA-INTENT] LLM 返回了未知结果: '{result_text}'，"
                f"已知 Skill: {self.skill_names}"
            )
            return "unsupported"

        except Exception as e:
            logger.warning(f"[RCA-INTENT] LLM 分类失败: {e}")
            return None

    @staticmethod
    def _extract_content(response: Any) -> str:
        """从 LLM 响应中提取文本内容。

        兼容多种 LLM 响应格式。

        Args:
            response: LLM 响应对象

        Returns:
            提取的文本内容
        """
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
