"""RCA Skill YAML 解析与校验。

负责将原始 YAML 字典解析为 RCASkill 数据结构，
并提供完整的格式校验能力。
"""

from __future__ import annotations

from typing import Any

from loguru import logger

from nanobot.rca.schema import (
    OutputSchema,
    RCASkill,
    RootCauseRule,
    SkillStep,
    StepType,
)

# 顶层必需字段
_REQUIRED_TOP_FIELDS = {"name", "version", "description", "type", "steps"}

# 各步骤类型必需的专用字段
_STEP_REQUIRED_FIELDS: dict[StepType, set[str]] = {
    StepType.LLM: {"prompt"},
    StepType.TOOL: {"tool"},
    StepType.ROOT_CAUSE_DEFINITION: {"logic"},
}


class SkillValidationError(Exception):
    """Skill 校验错误异常。"""

    def __init__(self, errors: list[str]):
        self.errors = errors
        super().__init__(f"Skill 校验失败: {'; '.join(errors)}")


def validate(raw: dict[str, Any]) -> list[str]:
    """校验原始 YAML 字典内容。

    Args:
        raw: 从 YAML 文件中解析出的字典，应包含 "skill" 根节点或直接是 skill 内容

    Returns:
        错误列表，空列表表示校验通过
    """
    errors: list[str] = []

    # 支持 skill 根节点或直接内容
    data = raw.get("skill", raw) if isinstance(raw, dict) else raw

    if not isinstance(data, dict):
        return ["Skill 内容必须是字典类型"]

    # 1. 顶层字段完整性
    missing = _REQUIRED_TOP_FIELDS - set(data.keys())
    if missing:
        errors.append(f"缺少必需的顶层字段: {', '.join(sorted(missing))}")

    # 2. steps 必须是列表
    steps = data.get("steps")
    if steps is not None and not isinstance(steps, list):
        errors.append("steps 必须是列表类型")
        return errors  # steps 格式错误时无法继续校验

    if not steps:
        errors.append("steps 列表不能为空")
        return errors

    # 3. 步骤级校验
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
                errors.append(f"{prefix}: type={step_type_str} 时必须包含 '{field_name}' 字段")

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

    return errors


def parse_yaml(raw: dict[str, Any]) -> RCASkill:
    """将原始 YAML 字典解析为 RCASkill 数据结构。

    Args:
        raw: 从 YAML 文件中解析出的字典

    Returns:
        解析后的 RCASkill 对象

    Raises:
        SkillValidationError: 校验不通过时抛出
    """
    # 校验
    errors = validate(raw)
    if errors:
        raise SkillValidationError(errors)

    # 提取 skill 数据
    data = raw.get("skill", raw) if isinstance(raw, dict) else raw

    # 解析步骤列表
    steps: list[SkillStep] = []
    for step_raw in data.get("steps", []):
        step = _parse_step(step_raw)
        steps.append(step)

    return RCASkill(
        name=str(data.get("name", "")),
        version=str(data.get("version", "")),
        description=str(data.get("description", "")),
        type=str(data.get("type", "workflow")),
        input_schema=dict(data.get("input_schema", {})),
        steps=steps,
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
        prompt=raw.get("prompt"),
        tool=raw.get("tool"),
        input=raw.get("input"),
        input_from=raw.get("input_from"),
        output_schema=output_schema,
        logic=logic,
    )
