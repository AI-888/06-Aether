"""RCA 步骤上下文管理。

管理各步骤的输出数据，支持 input_from 引用解析、
{{stepId.field}} 模板映射解析和 prompt 模板渲染。
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from typing import Any


@dataclass
class StepTrace:
    """步骤执行轨迹记录。

    Attributes:
        step_id: 步骤唯一标识
        step_type: 步骤类型 (skill / llm / tool / root_cause_definition)
        start_time: 开始执行时间戳
        end_time: 结束执行时间戳
        input_data: 步骤输入数据
        output_data: 步骤输出数据
        status: 执行状态 ("success" / "error" / "skipped")
        error_message: 错误信息（仅在 status="error" 时有值）
    """
    step_id: str = ""
    step_type: str = ""
    start_time: float = 0.0
    end_time: float = 0.0
    input_data: dict[str, Any] = field(default_factory=dict)
    output_data: dict[str, Any] = field(default_factory=dict)
    status: str = "success"
    error_message: str | None = None

    @property
    def duration(self) -> float:
        """执行耗时（秒）。"""
        return self.end_time - self.start_time

    def to_dict(self) -> dict[str, Any]:
        """转换为字典格式。"""
        return {
            "step_id": self.step_id,
            "step_type": self.step_type,
            "start_time": self.start_time,
            "end_time": self.end_time,
            "duration": round(self.duration, 3),
            "input_data": self.input_data,
            "output_data": self.output_data,
            "status": self.status,
            "error_message": self.error_message,
        }


class InputFromResolveError(Exception):
    """input_from 引用解析错误。"""
    pass


class TemplateResolveError(Exception):
    """模板变量解析错误。"""
    pass


class StepContext:
    """步骤执行上下文。

    管理各步骤的输出数据，支持 input_from 引用解析和 prompt 模板渲染。

    Attributes:
        _inputs: 外部输入参数（对应 Skill 的 input_schema）
        _outputs: 各步骤的输出数据 {step_id: {field: value}}
        _traces: 执行轨迹列表
    """

    def __init__(self, inputs: dict[str, Any] | None = None):
        self._inputs: dict[str, Any] = inputs or {}
        self._outputs: dict[str, dict[str, Any]] = {}
        self._traces: list[StepTrace] = []

    @property
    def inputs(self) -> dict[str, Any]:
        """获取外部输入参数。"""
        return self._inputs

    def set_output(self, step_id: str, output: dict[str, Any]) -> None:
        """存储步骤输出。

        Args:
            step_id: 步骤唯一标识
            output: 步骤输出的字段字典
        """
        self._outputs[step_id] = output

    def get_output(self, step_id: str) -> dict[str, Any] | None:
        """获取指定步骤的输出。"""
        return self._outputs.get(step_id)

    def resolve_input_from(self, refs: list[str]) -> dict[str, Any]:
        """解析 input_from 引用。

        将 ["step_id.field_name", ...] 格式的引用解析为实际值字典。

        Args:
            refs: 引用列表，每项格式为 "step_id.field_name"

        Returns:
            解析后的字段值字典 {field_name: value}

        Raises:
            InputFromResolveError: 引用的步骤或字段不存在时抛出
        """
        result: dict[str, Any] = {}

        for ref in refs:
            if "." not in ref:
                raise InputFromResolveError(
                    f"引用格式错误: '{ref}'，应为 'step_id.field_name'"
                )

            step_id, field_name = ref.split(".", 1)

            step_output = self._outputs.get(step_id)
            if step_output is None:
                raise InputFromResolveError(
                    f"引用的步骤 '{step_id}' 不存在或未执行"
                )

            if field_name not in step_output:
                raise InputFromResolveError(
                    f"步骤 '{step_id}' 的输出中不存在字段 '{field_name}'，"
                    f"可用字段: {list(step_output.keys())}"
                )

            result[field_name] = step_output[field_name]

        return result

    def resolve_input_template(self, input_map: dict[str, Any]) -> dict[str, Any]:
        """解析 input 模板映射。

        将 {"pod_list": "{{step1.pods}}"} 格式的模板映射解析为实际值字典。
        支持两种格式：
        - {{stepId.fieldName}}: 引用前置步骤的输出字段
        - {{inputParam}}: 引用外部输入参数

        Args:
            input_map: 输入参数映射，值可以是 {{}} 模板字符串或普通值

        Returns:
            解析后的实际值字典

        Raises:
            TemplateResolveError: 引用的步骤或字段不存在时抛出
        """
        result: dict[str, Any] = {}

        for key, value in input_map.items():
            if isinstance(value, str) and "{{" in value:
                resolved = self._resolve_single_template(value)
                result[key] = resolved
            else:
                # 非模板值直接透传
                result[key] = value

        return result

    def _resolve_single_template(self, template: str) -> Any:
        """解析单个模板字符串。

        如果模板是纯变量引用（如 "{{step1.pods}}"），直接返回原始值（保持类型）。
        如果模板包含混合文本（如 "prefix_{{step1.name}}_suffix"），返回字符串。

        Args:
            template: 包含 {{}} 占位符的模板字符串

        Returns:
            解析后的值

        Raises:
            TemplateResolveError: 变量无法解析时抛出
        """
        template = template.strip()

        # 纯变量引用（整个字符串就是一个 {{...}}）：保持原始类型
        pure_match = re.fullmatch(r"\{\{\s*([\w]+(?:\.[\w]+)?)\s*\}\}", template)
        if pure_match:
            var_expr = pure_match.group(1).strip()
            return self._lookup_variable(var_expr)

        # 混合模板：替换所有 {{...}} 为字符串值
        def _replace(match: re.Match) -> str:
            var_expr = match.group(1).strip()
            val = self._lookup_variable(var_expr)
            return str(val)

        return re.sub(r"\{\{\s*([\w]+(?:\.[\w]+)?)\s*\}\}", _replace, template)

    def _lookup_variable(self, var_expr: str) -> Any:
        """查找变量值。

        查找顺序:
        1. stepId.fieldName → 从 _outputs 中获取
        2. simpleVar → 从 _inputs 中获取

        Args:
            var_expr: 变量表达式，如 "step1.pods" 或 "namespace"

        Returns:
            变量的实际值

        Raises:
            TemplateResolveError: 变量不存在时抛出
        """
        if "." in var_expr:
            step_id, field_name = var_expr.split(".", 1)

            step_output = self._outputs.get(step_id)
            if step_output is None:
                raise TemplateResolveError(
                    f"模板变量 '{{{{{var_expr}}}}}' 引用的步骤 '{step_id}' 不存在或未执行"
                )

            if field_name not in step_output:
                raise TemplateResolveError(
                    f"模板变量 '{{{{{var_expr}}}}}' 引用的字段 '{field_name}' "
                    f"在步骤 '{step_id}' 的输出中不存在，"
                    f"可用字段: {list(step_output.keys())}"
                )

            return step_output[field_name]

        # 简单变量：从外部输入中获取
        if var_expr in self._inputs:
            return self._inputs[var_expr]

        raise TemplateResolveError(
            f"模板变量 '{{{{{var_expr}}}}}' 无法解析，"
            f"既不是 'stepId.field' 格式，也不在外部输入参数中"
        )

    def resolve_template(
        self,
        template: str,
        extra_vars: dict[str, Any] | None = None,
    ) -> str:
        """渲染 prompt 模板。

        将 {{变量名}} 替换为实际值。
        查找顺序: extra_vars → _inputs（外部输入）

        Args:
            template: 包含 {{变量名}} 占位符的模板字符串
            extra_vars: 额外变量字典（通常来自 input_from 解析结果）

        Returns:
            渲染后的字符串
        """
        merged_vars: dict[str, Any] = {}
        # 先放 _inputs 作为基底
        merged_vars.update(self._inputs)
        # extra_vars 优先级高于 _inputs
        if extra_vars:
            merged_vars.update(extra_vars)

        def _replace(match: re.Match) -> str:
            var_name = match.group(1).strip()
            if var_name in merged_vars:
                return str(merged_vars[var_name])
            # 未找到变量时保留原文，记录警告
            return match.group(0)

        # 支持 {{变量名}} 格式
        return re.sub(r"\{\{(\s*\w+\s*)\}\}", _replace, template)

    def add_trace(self, trace: StepTrace) -> None:
        """记录步骤执行轨迹。"""
        self._traces.append(trace)

    def get_all_traces(self) -> list[StepTrace]:
        """获取完整执行轨迹。"""
        return list(self._traces)

    def get_all_outputs(self) -> dict[str, dict[str, Any]]:
        """获取所有步骤的输出数据。"""
        return dict(self._outputs)