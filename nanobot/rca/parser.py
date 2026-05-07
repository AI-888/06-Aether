"""RCA Skill YAML 解析与校验。

负责将原始 YAML 字典解析为 AtomicSkill 或 SOPSkill 数据结构，
按 type 字段区分两类 Skill，并提供完整的格式校验能力。
"""

from __future__ import annotations

import re
from typing import Any

from loguru import logger

from nanobot.rca.schema import (
    AtomicSkill,
    OutputSchema,
    RootCauseRule,
    SkillStep,
    SkillType,
    SOPSkill,
    StepType,
)

# Atomic Skill 顶层必需字段
_ATOMIC_REQUIRED_FIELDS = {"name", "version", "description", "type", "output_schema"}

# SOP Skill 顶层必需字段
_SOP_REQUIRED_FIELDS = {"name", "version", "description", "type", "execution"}

# 各步骤类型必需的专用字段
_STEP_REQUIRED_FIELDS: dict[StepType, set[str]] = {
    StepType.SKILL: {"skill"},
    StepType.LLM: {"prompt"},
    StepType.TOOL: {"tool"},
    StepType.ROOT_CAUSE_DEFINITION: {"logic"},
}

# {{stepId.field}} 模板变量正则
_TEMPLATE_VAR_PATTERN = re.compile(r"\{\{\s*([\w]+)\.([\w]+)\s*\}\}")
# {{simpleVar}} 简单变量正则
_SIMPLE_VAR_PATTERN = re.compile(r"\{\{\s*([\w]+)\s*\}\}")


class SkillValidationError(Exception):
    """Skill 校验错误异常。"""

    def __init__(self, errors: list[str]):
        self.errors = errors
        super().__init__(f"Skill 校验失败: {'; '.join(errors)}")


def _extract_skill_data(raw: dict[str, Any]) -> dict[str, Any]:
    """从原始字典中提取 skill 数据节点。"""
    data = raw.get("skill", raw) if isinstance(raw, dict) else raw
    if not isinstance(data, dict):
        raise SkillValidationError(["Skill 内容必须是字典类型"])
    return data


def validate_atomic(raw: dict[str, Any]) -> list[str]:
    """校验 Atomic Skill 原始 YAML 字典。

    Args:
        raw: 从 YAML 文件解析出的字典

    Returns:
        错误列表，空列表表示校验通过
    """
    errors: list[str] = []
    data = raw.get("skill", raw) if isinstance(raw, dict) else raw

    if not isinstance(data, dict):
        return ["Skill 内容必须是字典类型"]

    # 1. 顶层字段完整性
    missing = _ATOMIC_REQUIRED_FIELDS - set(data.keys())
    if missing:
        errors.append(f"缺少必需的顶层字段: {', '.join(sorted(missing))}")

    # 2. output_schema 必需且非空
    output_schema = data.get("output_schema")
    if not output_schema:
        errors.append("Atomic Skill 必须定义非空的 output_schema")
    elif not isinstance(output_schema, dict):
        errors.append("output_schema 必须是字典类型")
    elif len(output_schema) == 0:
        errors.append("Atomic Skill 的 output_schema 不能为空")

    # 3. type 字段值校验
    skill_type = data.get("type", "")
    if skill_type != SkillType.ATOMIC.value:
        errors.append(f"Atomic Skill 的 type 必须为 'atomic'，当前值: '{skill_type}'")

    return errors


def validate_sop(raw: dict[str, Any]) -> list[str]:
    """校验 SOP Skill 原始 YAML 字典。

    Args:
        raw: 从 YAML 文件解析出的字典

    Returns:
        错误列表，空列表表示校验通过
    """
    errors: list[str] = []
    data = raw.get("skill", raw) if isinstance(raw, dict) else raw

    if not isinstance(data, dict):
        return ["Skill 内容必须是字典类型"]

    # 1. 顶层字段完整性
    missing = _SOP_REQUIRED_FIELDS - set(data.keys())
    if missing:
        errors.append(f"缺少必需的顶层字段: {', '.join(sorted(missing))}")

    # 2. type 字段值校验
    skill_type = data.get("type", "")
    if skill_type != SkillType.SOP.value:
        errors.append(f"SOP Skill 的 type 必须为 'sop'，当前值: '{skill_type}'")

    # 3. execution.steps 校验
    execution = data.get("execution")
    if not isinstance(execution, dict):
        errors.append("SOP Skill 必须包含 execution 字段且为字典类型")
        return errors

    steps = execution.get("steps")
    if steps is not None and not isinstance(steps, list):
        errors.append("execution.steps 必须是列表类型")
        return errors

    if not steps:
        errors.append("execution.steps 列表不能为空")
        return errors

    # 4. 步骤级校验
    seen_ids: set[str] = set()
    valid_types = {t.value for t in StepType}

    for i, step in enumerate(steps):
        if not isinstance(step, dict):
            errors.append(f"steps[{i}] 必须是字典类型")
            continue

        step_id = step.get("id", "")
        prefix = f"steps[{i}](id={step_id})"

        # 步骤 ID 必需且唯一
        if not step_id:
            errors.append(f"steps[{i}] 缺少 id 字段")
        elif step_id in seen_ids:
            errors.append(f"{prefix}: 步骤 ID '{step_id}' 重复")
        else:
            seen_ids.add(step_id)

        # 步骤类型合法性
        step_type_str = step.get("type", "")
        if step_type_str not in valid_types:
            errors.append(
                f"{prefix}: 非法步骤类型 '{step_type_str}'，"
                f"允许值: {', '.join(sorted(valid_types))}"
            )
            continue

        # 按类型校验必需字段
        step_type = StepType(step_type_str)
        required = _STEP_REQUIRED_FIELDS.get(step_type, set())
        for field_name in required:
            if not step.get(field_name):
                errors.append(
                    f"{prefix}: type={step_type_str} 时必须包含 '{field_name}' 字段"
                )

        # input_from 引用有效性
        input_from = step.get("input_from")
        if input_from:
            if not isinstance(input_from, list):
                errors.append(f"{prefix}: input_from 必须是列表类型")
            else:
                for ref in input_from:
                    if "." not in str(ref):
                        errors.append(
                            f"{prefix}: input_from 引用 '{ref}' 格式错误，"
                            "应为 'step_id.field_name'"
                        )
                    else:
                        ref_step_id = str(ref).split(".", 1)[0]
                        if ref_step_id not in seen_ids:
                            errors.append(
                                f"{prefix}: input_from 引用的步骤 '{ref_step_id}' "
                                "未定义或不是前置步骤"
                            )

        # input 模板变量引用有效性（{{stepId.field}} 格式）
        step_input = step.get("input")
        if isinstance(step_input, dict):
            input_schema_keys = set(data.get("input_schema", {}).keys())
            for key, value in step_input.items():
                if isinstance(value, str):
                    # 检查 {{stepId.field}} 引用
                    for match in _TEMPLATE_VAR_PATTERN.finditer(value):
                        ref_step_id = match.group(1)
                        # 如果引用的不是外部输入参数，则必须是前置步骤
                        if ref_step_id not in seen_ids and ref_step_id not in input_schema_keys:
                            logger.warning(
                                f"{prefix}: input 模板变量 '{match.group(0)}' "
                                f"引用的 '{ref_step_id}' 未定义或不是前置步骤"
                            )

    return errors


def validate(raw: dict[str, Any]) -> list[str]:
    """校验原始 YAML 字典内容（自动检测类型）。

    兼容旧代码，自动根据 type 字段分派到对应的校验函数。

    Args:
        raw: 从 YAML 文件中解析出的字典

    Returns:
        错误列表，空列表表示校验通过
    """
    data = raw.get("skill", raw) if isinstance(raw, dict) else raw
    if not isinstance(data, dict):
        return ["Skill 内容必须是字典类型"]

    skill_type = data.get("type", "")
    if skill_type == SkillType.ATOMIC.value:
        return validate_atomic(raw)
    elif skill_type == SkillType.SOP.value:
        return validate_sop(raw)
    else:
        # 未知类型时给出提示
        return [f"未知的 Skill type: '{skill_type}'，允许值: atomic, sop"]


def _extract_atomic_tools(data: dict[str, Any]) -> list[str]:
    """从 Atomic Skill 数据中提取绑定的 Tool 名称列表。

    Atomic Skill YAML 中通过 execution.steps 声明底层工具绑定，
    提取所有步骤中的 tool 字段作为绑定的 ToolRegistry 工具名（去重、保序）。

    Args:
        data: Skill 数据字典（已去除顶层 "skill" 包装）

    Returns:
        绑定的工具名称列表，若未声明则返回空列表
    """
    execution = data.get("execution")
    if not isinstance(execution, dict):
        return []

    steps = execution.get("steps")
    if not isinstance(steps, list) or not steps:
        return []

    tools: list[str] = []
    seen: set[str] = set()
    for step in steps:
        if not isinstance(step, dict):
            continue
        tool = step.get("tool")
        if not isinstance(tool, str):
            continue
        tool_name = tool.strip()
        if not tool_name or tool_name in seen:
            continue
        seen.add(tool_name)
        tools.append(tool_name)

    return tools


def parse_atomic_skill(raw: dict[str, Any]) -> AtomicSkill:
    """将原始 YAML 字典解析为 AtomicSkill 数据结构。

    Args:
        raw: 从 YAML 文件中解析出的字典

    Returns:
        解析后的 AtomicSkill 对象

    Raises:
        SkillValidationError: 校验不通过时抛出
    """
    errors = validate_atomic(raw)
    if errors:
        raise SkillValidationError(errors)

    data = raw.get("skill", raw) if isinstance(raw, dict) else raw

    # 从 execution.steps 中提取绑定的 Tool 名称列表
    tool_names = _extract_atomic_tools(data)

    return AtomicSkill(
        name=str(data.get("name", "")),
        version=str(data.get("version", "")),
        description=str(data.get("description", "")),
        type="atomic",
        input_schema=dict(data.get("input_schema", {})),
        output_schema=dict(data.get("output_schema", {})),
        tools=tool_names,
    )


def parse_sop_skill(raw: dict[str, Any]) -> SOPSkill:
    """将原始 YAML 字典解析为 SOPSkill 数据结构。

    Args:
        raw: 从 YAML 文件中解析出的字典

    Returns:
        解析后的 SOPSkill 对象

    Raises:
        SkillValidationError: 校验不通过时抛出
    """
    errors = validate_sop(raw)
    if errors:
        raise SkillValidationError(errors)

    data = raw.get("skill", raw) if isinstance(raw, dict) else raw

    # 从 execution.steps 解析步骤列表
    execution = data.get("execution", {})
    steps: list[SkillStep] = []
    for step_raw in execution.get("steps", []):
        step = _parse_step(step_raw)
        steps.append(step)

    return SOPSkill(
        name=str(data.get("name", "")),
        version=str(data.get("version", "")),
        description=str(data.get("description", "")),
        type="sop",
        input_schema=dict(data.get("input_schema", {})),
        steps=steps,
    )


def parse_yaml(raw: dict[str, Any]) -> AtomicSkill | SOPSkill:
    """将原始 YAML 字典解析为 Skill 数据结构（自动检测类型）。

    按 type 字段区分，分派到 parse_atomic_skill 或 parse_sop_skill。

    Args:
        raw: 从 YAML 文件中解析出的字典

    Returns:
        解析后的 AtomicSkill 或 SOPSkill 对象

    Raises:
        SkillValidationError: 校验不通过时抛出
    """
    data = raw.get("skill", raw) if isinstance(raw, dict) else raw
    if not isinstance(data, dict):
        raise SkillValidationError(["Skill 内容必须是字典类型"])

    skill_type = data.get("type", "")

    if skill_type == SkillType.ATOMIC.value:
        return parse_atomic_skill(raw)
    elif skill_type == SkillType.SOP.value:
        return parse_sop_skill(raw)
    else:
        raise SkillValidationError(
            [f"未知的 Skill type: '{skill_type}'，允许值: atomic, sop"]
        )


def _parse_step(raw: dict[str, Any]) -> SkillStep:
    """解析单个步骤定义。"""
    step_type = StepType(raw.get("type", "llm"))

    # 解析 output_schema
    output_schema = None
    os_raw = raw.get("output_schema")
    if isinstance(os_raw, dict):
        output_schema = OutputSchema(fields=dict(os_raw))

    # 解析 logic（root_cause_definition 类型）
    logic = None
    logic_raw = raw.get("logic")
    if isinstance(logic_raw, list):
        logic = []
        for rule_raw in logic_raw:
            if isinstance(rule_raw, dict):
                logic.append(RootCauseRule(
                    when=dict(rule_raw.get("when", {})),
                    root_cause=str(rule_raw.get("root_cause", "")),
                    solution=str(rule_raw.get("solution", "")),
                ))

    return SkillStep(
        id=str(raw.get("id", "")),
        type=step_type,
        skill=raw.get("skill"),
        prompt=raw.get("prompt"),
        tool=raw.get("tool"),
        input=raw.get("input"),
        input_from=raw.get("input_from"),
        output_schema=output_schema,
        logic=logic,
    )