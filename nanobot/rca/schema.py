"""RCA Skill 数据模型定义。

定义了 RCA Skill YAML 文件的完整数据结构，
包括步骤类型枚举、输出 Schema、根因匹配规则、执行步骤和完整的 Skill 定义。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class StepType(str, Enum):
    """Skill 步骤类型枚举。"""
    LLM = "llm"
    TOOL = "tool"
    ROOT_CAUSE_DEFINITION = "root_cause_definition"


@dataclass
class OutputSchema:
    """步骤输出字段定义。

    Attributes:
        fields: 输出字段名到类型字符串的映射，如 {"error_message": "string"}
    """
    fields: dict[str, str] = field(default_factory=dict)


@dataclass
class RootCauseRule:
    """根因匹配规则。

    用于 root_cause_definition 类型步骤中的规则匹配逻辑。

    Attributes:
        when: 匹配条件，键值对形式，支持比较运算符如 ">90"
        root_cause: 命中规则时的根因描述
        solution: 命中规则时的修复建议
    """
    when: dict[str, str] = field(default_factory=dict)
    root_cause: str = ""
    solution: str = ""


@dataclass
class SkillStep:
    """Skill 执行步骤定义。

    每个步骤包含唯一标识、类型以及根据类型不同的必需字段。

    Attributes:
        id: 步骤唯一标识
        type: 步骤类型 (llm / tool / root_cause_definition)
        prompt: LLM 提示词模板（type=llm 时必需）
        tool: 工具名称（type=tool 时必需）
        input: 工具输入参数（type=tool 时使用）
        input_from: 前置步骤输出引用列表，格式 ["step_id.field_name"]
        output_schema: 输出字段声明
        logic: 根因规则列表（type=root_cause_definition 时必需）
    """
    id: str = ""
    type: StepType = StepType.LLM
    prompt: str | None = None
    tool: str | None = None
    input: dict[str, Any] | None = None
    input_from: list[str] | None = None
    output_schema: OutputSchema | None = None
    logic: list[RootCauseRule] | None = None


@dataclass
class RCASkill:
    """完整的 RCA Skill 定义。

    对应一个 YAML Skill 文件的解析结果，包含技能的所有元数据和步骤列表。

    Attributes:
        name: 技能名称
        version: 版本号
        description: 技能描述
        type: 技能类型（如 workflow）
        input_schema: 输入参数定义 {参数名: 类型}
        steps: 步骤列表
        file_path: 源文件路径（运行时元数据）
        loaded_at: 加载时间（运行时元数据）
    """
    name: str = ""
    version: str = ""
    description: str = ""
    type: str = "workflow"
    input_schema: dict[str, str] = field(default_factory=dict)
    steps: list[SkillStep] = field(default_factory=list)

    # 运行时元数据（非 YAML 字段）
    file_path: str | None = None
    loaded_at: str | None = None
