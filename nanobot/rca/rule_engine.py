"""RCA 规则匹配引擎。

轻量级规则匹配引擎，使用关键词/正则快速匹配已知 Skill。
支持配置化新增规则，无需修改代码。
"""

from __future__ import annotations

import re
from typing import Any

from loguru import logger


class RuleMatchEngine:
    """轻量级规则匹配引擎。

    使用关键词/正则快速匹配已知 Skill。
    支持配置化新增规则，无需修改代码。

    规则配置格式：
        {
            "skill_name": ["regex_pattern1", "regex_pattern2"],
            ...
        }
    """

    def __init__(self) -> None:
        self._rules: dict[str, list[re.Pattern]] = {}

    def load_rules(self, rules_config: dict[str, list[str]]) -> None:
        """加载规则配置。

        Args:
            rules_config: Skill 名称到正则模式列表的映射
                例如：{"check_pod_status": ["查看.*pod", "pod.*状态"]}
        """
        self._rules.clear()
        for skill_name, patterns in rules_config.items():
            compiled: list[re.Pattern] = []
            for p in patterns:
                try:
                    compiled.append(re.compile(p, re.IGNORECASE))
                except re.error as e:
                    logger.warning(
                        f"[RCA-RULE] 正则编译失败: '{p}' (skill={skill_name}): {e}"
                    )
            if compiled:
                self._rules[skill_name] = compiled

        logger.info(
            f"[RCA-RULE] 已加载 {len(self._rules)} 条规则, "
            f"覆盖 Skill: {list(self._rules.keys())}"
        )

    def match(self, query: str) -> str | None:
        """匹配查询到 Skill。

        遍历所有规则，返回第一个匹配的 Skill 名称。
        匹配为毫秒级操作。

        Args:
            query: 用户查询文本

        Returns:
            匹配到的 skill_name，或 None（无匹配）
        """
        for skill_name, patterns in self._rules.items():
            for pattern in patterns:
                if pattern.search(query):
                    logger.debug(
                        f"[RCA-RULE] 规则命中: '{pattern.pattern}' → {skill_name}"
                    )
                    return skill_name
        return None

    def add_rule(self, skill_name: str, pattern: str) -> bool:
        """动态添加单条规则。

        Args:
            skill_name: Skill 名称
            pattern: 正则模式字符串

        Returns:
            是否添加成功
        """
        try:
            compiled = re.compile(pattern, re.IGNORECASE)
        except re.error as e:
            logger.warning(f"[RCA-RULE] 正则编译失败: '{pattern}': {e}")
            return False

        if skill_name not in self._rules:
            self._rules[skill_name] = []
        self._rules[skill_name].append(compiled)
        return True

    def remove_rules(self, skill_name: str) -> None:
        """移除指定 Skill 的所有规则。"""
        self._rules.pop(skill_name, None)

    @property
    def rule_count(self) -> int:
        """当前规则总数。"""
        return sum(len(patterns) for patterns in self._rules.values())

    @property
    def skill_names(self) -> list[str]:
        """已注册规则的 Skill 名称列表。"""
        return list(self._rules.keys())
