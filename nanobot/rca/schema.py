"""RCA Skill 数据模型定义。

定义了 RCA Skill YAML 文件的完整数据结构，
包括 Skill 类型枚举、步骤类型枚举、输出 Schema、根因匹配规则、
执行步骤和 Atomic/SOP 两类 Skill 定义。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class SkillType(str, Enum):
    """Skill 类型枚举。"""
    ATOMIC = "atomic"
    SOP = "sop"


class StepType(str, Enum):
    """SOP Skill 步骤类型枚举。"""
    SKILL = "skill"                          # 调用 Atomic Skill
    LLM = "llm"                              # LLM 总结（仅用于总结/报告）
    TOOL = "tool"                            # 直接工具调用（必要时）
    ROOT_CAUSE_DEFINITION = "root_cause_definition"  # 确定性规则引擎


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
    """SOP Skill 执行步骤定义。

    每个步骤包含唯一标识、类型以及根据类型不同的必需字段。

    Attributes:
        id: 步骤唯一标识
        type: 步骤类型 (skill / llm / tool / root_cause_definition)
        skill: 调用的 Atomic Skill 名称（type=skill 时必需）
        prompt: LLM 提示词模板（type=llm 时必需）
        tool: 工具名称（type=tool 时必需）
        input: 输入参数，支持 {{stepId.field}} 模板映射
        input_from: 前置步骤输出引用列表，格式 ["step_id.field_name"]
        output_schema: 输出字段声明
        logic: 根因规则列表（type=root_cause_definition 时必需）
    """
    id: str = ""
    type: StepType = StepType.LLM
    # skill 类型专用
    skill: str | None = None
    # llm 类型专用
    prompt: str | None = None
    # tool 类型专用
    tool: str | None = None
    # 通用字段
    input: dict[str, Any] | None = None
    input_from: list[str] | None = None
    output_schema: OutputSchema | None = None
    logic: list[RootCauseRule] | None = None


@dataclass
class AtomicSkill:
    """Atomic Skill 定义。

    原子技能：对单次工具调用的结构化封装。
    本身不包含业务逻辑，通过 output_schema 约束输出格式。
    通过 execution.steps 中的 tool 字段绑定底层 ToolRegistry 工具。

    Attributes:
        name: 技能名称
        version: 版本号
        description: 技能描述
        type: 固定为 "atomic"
        input_schema: 输入参数定义 {参数名: 类型}
        output_schema: 输出字段定义 {字段名: 类型}（必需，不可为空）
        tool: 绑定的 ToolRegistry 工具名称（从 execution.steps[0].tool 提取）
        file_path: 源文件路径（运行时元数据）
        loaded_at: 加载时间（运行时元数据）
    """
    name: str = ""
    version: str = ""
    description: str = ""
    type: str = "atomic"
    input_schema: dict[str, str] = field(default_factory=dict)
    output_schema: dict[str, str] = field(default_factory=dict)
    tool: str | None = None  # 绑定的 ToolRegistry 工具名称

    # 运行时元数据（非 YAML 字段）
    file_path: str | None = None
    loaded_at: str | None = None


@dataclass
class SOPSkill:
    """SOP Skill 定义。

    标准操作流程技能：编排多个 Atomic Skill + 规则引擎 + LLM 总结。
    步骤间数据传递通过 input/input_from 显式声明。

    Attributes:
        name: 技能名称
        version: 版本号
        description: 技能描述
        type: 固定为 "sop"
        input_schema: 输入参数定义 {参数名: 类型}
        steps: 步骤列表
        file_path: 源文件路径（运行时元数据）
        loaded_at: 加载时间（运行时元数据）
    """
    name: str = ""
    version: str = ""
    description: str = ""
    type: str = "sop"
    input_schema: dict[str, str] = field(default_factory=dict)
    steps: list[SkillStep] = field(default_factory=list)

    # 运行时元数据（非 YAML 字段）
    file_path: str | None = None
    loaded_at: str | None = None


# 兼容旧代码：RCASkill 作为 SOPSkill 的别名
RCASkill = SOPSkill