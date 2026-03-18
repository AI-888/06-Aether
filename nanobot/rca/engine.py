"""RCA 分步执行引擎。

按 Skill YAML 中定义的 steps 列表顺序，逐步执行排障工作流。
每个 LLM 步骤为独立的单轮 SLM 调用，与 Nanobot 现有架构一致。
"""

from __future__ import annotations

import asyncio
import json
import re
import time
from typing import Any, Callable

from loguru import logger

from nanobot.rca.audit import AuditLogger
from nanobot.rca.context import InputFromResolveError, StepContext, StepTrace
from nanobot.rca.report import RCAReport, ReportGenerator
from nanobot.rca.schema import RCASkill, SkillStep, StepType
from nanobot.rca.security import SecurityGuard, SecurityViolationError
from nanobot.metrics import (
    RCA_EXECUTION_DURATION,
    RCA_STEP_DURATION,
    RCA_EXECUTION_TOTAL,
    RCA_SECURITY_REJECT_TOTAL,
)


class RCAExecutionError(Exception):
    """RCA 执行错误基类。"""

    def __init__(self, step_id: str, reason: str, context_snapshot: dict | None = None):
        self.step_id = step_id
        self.reason = reason
        self.context_snapshot = context_snapshot or {}
        super().__init__(f"RCA 执行错误 [步骤 '{step_id}']: {reason}")


class RCAEngine:
    """RCA 分步执行引擎。

    按 Skill YAML 中定义的 steps 列表顺序，逐步执行排障工作流。
    每个 LLM 步骤为独立的单轮 SLM 调用。
    """

    def __init__(
        self,
        provider: Any,
        tool_registry: Any,
        security_guard: SecurityGuard,
        audit_logger: AuditLogger,
        model: str | None = None,
        max_step_timeout: int = 30,
        max_total_timeout: int = 300,
    ):
        """初始化 RCA 执行引擎。

        Args:
            provider: LLMProvider 实例
            tool_registry: ToolRegistry 实例
            security_guard: 安全校验层
            audit_logger: 审计日志记录器
            model: 专用 SLM 模型名称
            max_step_timeout: 单步骤超时时间（秒）
            max_total_timeout: 整体超时时间（秒）
        """
        self.provider = provider
        self.tools = tool_registry
        self.security = security_guard
        self.audit = audit_logger
        self.model = model
        self.max_step_timeout = max_step_timeout
        self.max_total_timeout = max_total_timeout

    async def execute(
        self,
        skill: RCASkill,
        inputs: dict[str, Any],
        stream_callback: Callable | None = None,
        session_id: str | None = None,
    ) -> RCAReport:
        """执行完整的 RCA Skill 工作流。

        Args:
            skill: 已解析的 RCA Skill 对象
            inputs: 外部输入参数（对应 input_schema）
            stream_callback: 流式回调（可选）
            session_id: 会话 ID（可选，不提供时自动生成）

        Returns:
            完整的 RCA 报告
        """
        session_id = session_id or self.audit.new_session_id()
        start_time = time.time()
        ctx = StepContext(inputs)

        # 记录会话开始
        self.audit.log_session_start(session_id, skill.name, inputs)
        logger.info(f"[RCA] 开始执行 Skill '{skill.name}' v{skill.version}")

        final_status = "success"
        last_root_cause = None

        try:
            # 按步骤顺序执行
            for i, step in enumerate(skill.steps):
                logger.info(
                    f"[RCA] 执行步骤 {i + 1}/{len(skill.steps)}: "
                    f"'{step.id}' (type={step.type.value})"
                )

                # 检查总超时
                elapsed = time.time() - start_time
                if elapsed > self.max_total_timeout:
                    raise RCAExecutionError(
                        step.id,
                        f"整体执行超时（已用 {elapsed:.1f}s，限制 {self.max_total_timeout}s）",
                    )

                step_start = time.time()
                trace = StepTrace(
                    step_id=step.id,
                    step_type=step.type.value,
                    start_time=step_start,
                )

                try:
                    # 单步超时控制
                    output = await asyncio.wait_for(
                        self._execute_step(step, ctx),
                        timeout=self.max_step_timeout,
                    )

                    trace.end_time = time.time()
                    trace.output_data = output
                    trace.status = "success"

                    # 记录根因
                    if "root_cause" in output:
                        last_root_cause = output["root_cause"]

                    # 流式回调
                    if stream_callback:
                        stream_callback(step.id, output)

                except asyncio.TimeoutError:
                    trace.end_time = time.time()
                    trace.status = "error"
                    trace.error_message = f"步骤执行超时（限制 {self.max_step_timeout}s）"
                    ctx.add_trace(trace)

                    RCA_STEP_DURATION.labels(
                        step_type=step.type.value, status="error",
                    ).observe(trace.duration)

                    self.audit.log_step(
                        session_id, step.id, step.type.value,
                        None, {}, {}, "error", trace.duration,
                    )
                    raise RCAExecutionError(
                        step.id, trace.error_message,
                        {"elapsed": trace.duration},
                    )

                except SecurityViolationError as e:
                    trace.end_time = time.time()
                    trace.status = "error"
                    trace.error_message = str(e)
                    ctx.add_trace(trace)

                    RCA_STEP_DURATION.labels(
                        step_type=step.type.value, status="error",
                    ).observe(trace.duration)
                    RCA_SECURITY_REJECT_TOTAL.labels(
                        tool_name=e.tool_name,
                    ).inc()

                    self.audit.log_security_event(
                        session_id, "tool_rejected",
                        {"step_id": step.id, "tool": e.tool_name, "reason": e.reason},
                    )
                    self.audit.log_step(
                        session_id, step.id, step.type.value,
                        None, {}, {}, "error", trace.duration,
                    )
                    raise RCAExecutionError(step.id, str(e))

                except (InputFromResolveError, RCAExecutionError) as e:
                    trace.end_time = time.time()
                    trace.status = "error"
                    trace.error_message = str(e)
                    ctx.add_trace(trace)

                    RCA_STEP_DURATION.labels(
                        step_type=step.type.value, status="error",
                    ).observe(trace.duration)

                    self.audit.log_step(
                        session_id, step.id, step.type.value,
                        None, {}, {}, "error", trace.duration,
                    )
                    raise RCAExecutionError(step.id, str(e))

                else:
                    ctx.add_trace(trace)
                    RCA_STEP_DURATION.labels(
                        step_type=step.type.value, status="success",
                    ).observe(trace.duration)
                    self.audit.log_step(
                        session_id, step.id, step.type.value,
                        None, trace.input_data, output, "success", trace.duration,
                    )

        except RCAExecutionError:
            final_status = "error"
            raise

        except Exception as e:
            final_status = "error"
            logger.error(f"[RCA] 执行异常: {e}")
            raise RCAExecutionError("unknown", str(e))

        finally:
            duration = time.time() - start_time
            self.audit.log_session_end(
                session_id, final_status, duration, last_root_cause,
            )
            # Prometheus 指标上报
            RCA_EXECUTION_DURATION.labels(
                skill_name=skill.name, status=final_status,
            ).observe(duration)
            RCA_EXECUTION_TOTAL.labels(
                skill_name=skill.name, status=final_status,
            ).inc()
            logger.info(
                f"[RCA] Skill '{skill.name}' 执行完成, "
                f"状态: {final_status}, 耗时: {duration:.2f}s"
            )

        # 生成报告
        report = ReportGenerator.generate(
            ctx,
            skill_name=skill.name,
            skill_version=skill.version,
            start_time=start_time,
        )
        return report

    async def _execute_step(
        self,
        step: SkillStep,
        ctx: StepContext,
    ) -> dict[str, Any]:
        """根据步骤类型分发执行。"""
        if step.type == StepType.LLM:
            return await self._execute_llm_step(step, ctx)
        elif step.type == StepType.TOOL:
            return await self._execute_tool_step(step, ctx)
        elif step.type == StepType.ROOT_CAUSE_DEFINITION:
            return await self._execute_rcd_step(step, ctx)
        else:
            raise RCAExecutionError(step.id, f"未知的步骤类型: {step.type}")

    async def _execute_llm_step(
        self,
        step: SkillStep,
        ctx: StepContext,
    ) -> dict[str, Any]:
        """执行 LLM 类型步骤。

        流程:
        1. 从 input_from 解析前置步骤输出
        2. 渲染 prompt 模板
        3. 构建单轮 SLM 调用消息（最小上下文）
        4. 调用 LLMProvider.chat()
        5. 解析 SLM 返回的 JSON 结果
        6. 校验输出是否匹配 output_schema
        7. 存入 StepContext
        """
        # 1. 解析引用
        extra_vars: dict[str, Any] = {}
        if step.input_from:
            extra_vars = ctx.resolve_input_from(step.input_from)

        # 2. 渲染 prompt
        prompt = ctx.resolve_template(step.prompt or "", extra_vars)

        # 3. 构建消息（最小上下文，仅当前步骤）
        messages = [
            {
                "role": "system",
                "content": (
                    "你是一个运维诊断助手，请严格按照要求的 JSON 格式输出结果。"
                    "不要输出 JSON 以外的任何内容。"
                ),
            },
            {"role": "user", "content": prompt},
        ]

        # 4. 单轮 SLM 调用（含重试）
        output = await self._call_llm_with_retry(step, messages)

        # 7. 存入上下文
        ctx.set_output(step.id, output)
        return output

    async def _call_llm_with_retry(
        self,
        step: SkillStep,
        messages: list[dict[str, str]],
        max_retries: int = 1,
    ) -> dict[str, Any]:
        """调用 LLM 并在格式错误时重试。"""
        last_error = None

        for attempt in range(max_retries + 1):
            try:
                response = await self.provider.chat(
                    messages=messages,
                    model=self.model,
                )

                # 提取响应内容
                content = self._extract_content(response)

                # 5. 解析 JSON 输出
                output = self._parse_json_output(content)

                # 6. 校验输出
                if step.output_schema:
                    self._validate_output(output, step.output_schema)

                return output

            except (json.JSONDecodeError, ValueError) as e:
                last_error = e
                if attempt < max_retries:
                    logger.warning(
                        f"[RCA] LLM 输出格式错误 (尝试 {attempt + 1})，重试中..."
                    )
                    # 附加格式约束提示
                    messages.append({
                        "role": "user",
                        "content": (
                            "你的回复不是有效的 JSON 格式，请只输出纯 JSON，"
                            "不要包含任何其他文字或 markdown 标记。"
                        ),
                    })

        raise RCAExecutionError(
            step.id,
            f"LLM 输出格式错误（已重试 {max_retries} 次）: {last_error}",
        )

    @staticmethod
    def _extract_content(response: Any) -> str:
        """从 LLM 响应中提取文本内容。"""
        if isinstance(response, str):
            return response
        if hasattr(response, "content"):
            return str(response.content)
        if isinstance(response, dict):
            # 兼容多种响应格式
            if "content" in response:
                return str(response["content"])
            if "choices" in response:
                choices = response["choices"]
                if choices and isinstance(choices, list):
                    msg = choices[0].get("message", {})
                    return str(msg.get("content", ""))
        return str(response)

    @staticmethod
    def _parse_json_output(content: str) -> dict[str, Any]:
        """从 SLM 文本回复中提取 JSON。

        支持纯 JSON 和 markdown 代码块中的 JSON。
        """
        content = content.strip()

        # 尝试直接解析
        try:
            result = json.loads(content)
            if isinstance(result, dict):
                return result
        except json.JSONDecodeError:
            pass

        # 尝试从 markdown 代码块中提取
        json_match = re.search(r"```(?:json)?\s*\n?(.*?)\n?```", content, re.DOTALL)
        if json_match:
            try:
                result = json.loads(json_match.group(1).strip())
                if isinstance(result, dict):
                    return result
            except json.JSONDecodeError:
                pass

        # 尝试查找第一个 {...} 结构
        brace_match = re.search(r"\{[^{}]*(?:\{[^{}]*\}[^{}]*)*\}", content, re.DOTALL)
        if brace_match:
            try:
                result = json.loads(brace_match.group(0))
                if isinstance(result, dict):
                    return result
            except json.JSONDecodeError:
                pass

        raise json.JSONDecodeError(
            f"无法从 SLM 回复中提取有效 JSON", content, 0
        )

    @staticmethod
    def _validate_output(
        output: dict[str, Any],
        schema: Any,
    ) -> None:
        """校验输出是否匹配 output_schema。"""
        if schema is None:
            return

        expected_fields = schema.fields if hasattr(schema, "fields") else {}
        missing = set(expected_fields.keys()) - set(output.keys())
        if missing:
            logger.warning(
                f"[RCA] 输出缺少字段: {missing}（非致命，继续执行）"
            )

    async def _execute_tool_step(
        self,
        step: SkillStep,
        ctx: StepContext,
    ) -> dict[str, Any]:
        """执行 Tool 类型步骤。

        流程:
        1. 安全校验（白名单检查）
        2. 通过 ToolRegistry 执行工具调用
        3. 解析返回结果
        4. 按 output_schema 存入 StepContext
        """
        tool_name = step.tool or ""
        tool_input = dict(step.input or {})

        # 解析 input 中的模板变量（支持引用前置步骤输出）
        if step.input_from:
            extra_vars = ctx.resolve_input_from(step.input_from)
            for key, value in tool_input.items():
                if isinstance(value, str) and "{{" in value:
                    tool_input[key] = ctx.resolve_template(value, extra_vars)

        # 1. 安全校验
        self.security.validate_tool_call(tool_name, tool_input)

        # 2. 执行工具
        result = await self.tools.execute(tool_name, tool_input)

        # 3-4. 解析并存储
        output = self._parse_tool_output(result, step)
        ctx.set_output(step.id, output)
        return output

    @staticmethod
    def _parse_tool_output(result: Any, step: SkillStep) -> dict[str, Any]:
        """解析工具返回结果。"""
        # 尝试解析为 JSON
        if isinstance(result, str):
            try:
                parsed = json.loads(result)
                if isinstance(parsed, dict):
                    return parsed
            except (json.JSONDecodeError, ValueError):
                pass

        # 如果有 output_schema，按 schema 构造输出
        if step.output_schema and step.output_schema.fields:
            output: dict[str, Any] = {}
            for field_name, field_type in step.output_schema.fields.items():
                if field_type == "number":
                    # 尝试从结果中提取数字
                    try:
                        output[field_name] = float(str(result))
                    except (ValueError, TypeError):
                        output[field_name] = str(result)
                else:
                    output[field_name] = str(result)
            return output

        return {"result": str(result)}

    async def _execute_rcd_step(
        self,
        step: SkillStep,
        ctx: StepContext,
    ) -> dict[str, Any]:
        """执行 Root Cause Definition 类型步骤。

        流程:
        1. 遍历 logic 列表中的匹配规则
        2. 将每条规则的 when 条件与前置步骤输出进行匹配
        3. 支持比较运算符（如 ">90"）
        4. 命中规则的 root_cause 和 solution 作为输出
        """
        if not step.logic:
            output = {
                "root_cause": "未定义根因匹配规则",
                "solution": "建议人工介入排查",
            }
            ctx.set_output(step.id, output)
            return output

        # 收集所有前置步骤的输出，用于规则匹配
        all_outputs: dict[str, Any] = {}
        for step_id, step_output in ctx.get_all_outputs().items():
            all_outputs.update(step_output)

        matched_root_cause = None
        matched_solution = None

        for rule in step.logic:
            if self._match_rule(rule.when, all_outputs):
                matched_root_cause = rule.root_cause
                matched_solution = rule.solution
                logger.info(
                    f"[RCA] 根因规则命中: {rule.when} → {rule.root_cause}"
                )
                break  # 首条命中即停止

        output = {
            "root_cause": matched_root_cause or "未能匹配到已知根因",
            "solution": matched_solution or "建议人工介入排查",
        }

        ctx.set_output(step.id, output)
        return output

    @staticmethod
    def _match_rule(
        when: dict[str, str],
        context_data: dict[str, Any],
    ) -> bool:
        """匹配单条根因规则。

        支持：
        - 精确匹配: {"key": "value"}
        - 比较运算符: {"key": ">90"}, {"key": "<10"}, {"key": ">=50"}
        """
        for key, expected in when.items():
            actual = context_data.get(key)
            if actual is None:
                return False

            expected_str = str(expected)

            # 解析比较运算符
            comp_match = re.match(r"^(>=|<=|>|<|==|!=)(.+)$", expected_str)
            if comp_match:
                op, threshold_str = comp_match.groups()
                try:
                    threshold = float(threshold_str)
                    actual_num = float(str(actual))
                except (ValueError, TypeError):
                    return False

                if op == ">" and not (actual_num > threshold):
                    return False
                elif op == ">=" and not (actual_num >= threshold):
                    return False
                elif op == "<" and not (actual_num < threshold):
                    return False
                elif op == "<=" and not (actual_num <= threshold):
                    return False
                elif op == "==" and not (actual_num == threshold):
                    return False
                elif op == "!=" and not (actual_num != threshold):
                    return False
            else:
                # 精确字符串匹配
                if str(actual) != expected_str:
                    return False

        return True
