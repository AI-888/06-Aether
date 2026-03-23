"""RCA（根因分析）模块 - 基于SLM的结构化排障执行引擎。"""

from nanobot.rca.audit import AuditLogger
from nanobot.rca.engine import RCAEngine, RCAExecutionError, SkillNotFoundError, ToolNotFoundError
from nanobot.rca.intent import IntentClassifier, IntentResult
from nanobot.rca.loader import RCASkillLoader
from nanobot.rca.router import FaultInput, RCARouter
from nanobot.rca.rule_engine import RuleMatchEngine
from nanobot.rca.schema import (
    AtomicSkill,
    OutputSchema,
    RCASkill,
    RootCauseRule,
    SkillStep,
    SkillType,
    SOPSkill,
    StepType,
)
from nanobot.rca.security import SecurityGuard

__all__ = [
    # 核心引擎
    "RCAEngine",
    "RCAExecutionError",
    "SkillNotFoundError",
    "ToolNotFoundError",
    # 路由
    "RCARouter",
    "FaultInput",
    # 意图分类
    "IntentClassifier",
    "IntentResult",
    "RuleMatchEngine",
    # 加载器
    "RCASkillLoader",
    # 数据模型
    "SkillType",
    "StepType",
    "AtomicSkill",
    "SOPSkill",
    "RCASkill",
    "SkillStep",
    "OutputSchema",
    "RootCauseRule",
    # 安全与审计
    "SecurityGuard",
    "AuditLogger",
]
