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
from nanobot.rca.context import (
    InputFromResolveError,
    StepContext,
    StepTrace,
    TemplateResolveError,
)
from nanobot.rca.report import RCAReport, ReportGenerator
from nanobot.rca.schema import AtomicSkill, RCASkill, SOPSkill, SkillStep, StepType
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


class SkillNotFoundError(RCAExecutionError):
    """Atomic Skill 未找到异常。"""

    def __init__(self, step_id: str, skill_name: str):
        self.skill_name = skill_name
        super().__init__(step_id, f"Atomic Skill '{skill_name}' 未找到")


class ToolNotFoundError(RCAExecutionError):
    """Atomic Skill 绑定的 Tool 未找到异常。"""

    def __init__(self, step_id: str, tool_name: str):
        self.tool_name_missing = tool_name
        super().__init__(step_id, f"Atomic Skill 绑定的 Tool '{tool_name}' 在 ToolRegistry 中未找到")


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
        skill_loader: Any = None,
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
            skill_loader: RCASkillLoader 实例（用于查找 Atomic Skill）
            model: 专用 SLM 模型名称
            max_step_timeout: 单步骤超时时间（秒）
            max_total_timeout: 整体超时时间（秒）
        """
        self.provider = provider
        self.tools = tool_registry
        self.security = security_guard
        self.audit = audit_logger
        self.skill_loader = skill_loader
        self.model = model
        self.max_step_timeout = max_step_timeout
        self.max_total_timeout = max_total_timeout

    async def execute(
        self,
        skill: RCASkill,
        inputs: dict[str, Any],
        stream_callback: Callable | None = None,
        session_id: str | None = None,
        context: dict[str, Any] | None = None,
    ) -> RCAReport:
        """执行完整的 RCA Skill 工作流。

        Args:
            skill: 已解析的 RCA Skill 对象
            inputs: 外部输入参数（对应 input_schema）
            stream_callback: 流式回调（可选）
            session_id: 会话 ID（可选，不提供时自动生成）
            context: 全局上下文（可选），承载 user_input 等贯穿整个流程的信息

        Returns:
            完整的 RCA 报告
        """
        session_id = session_id or self.audit.new_session_id()
        start_time = time.time()
        ctx = StepContext(inputs, context=context)

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

                except (InputFromResolveError, TemplateResolveError, RCAExecutionError) as e:
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
        if step.type == StepType.SKILL:
            return await self._execute_skill_step(step, ctx)
        elif step.type == StepType.LLM:
            return await self._execute_llm_step(step, ctx)
        elif step.type == StepType.TOOL:
            return await self._execute_tool_step(step, ctx)
        elif step.type == StepType.ROOT_CAUSE_DEFINITION:
            return await self._execute_rcd_step(step, ctx)
        else:
            raise RCAExecutionError(step.id, f"未知的步骤类型: {step.type}")

    async def _execute_skill_step(
        self,
        step: SkillStep,
        ctx: StepContext,
    ) -> dict[str, Any]:
        """执行 skill 类型步骤 —— Atomic Skill 的完整执行路径。

        执行流程：
        1. 查找 Atomic Skill 定义（从 skill_loader 获取）
        2. 解析输入参数（模板替换 {{stepId.field}}）
        3. 查找绑定的 Tool（Atomic Skill name = Tool name）
        4. 安全校验（白名单检查）
        5. 通过 ToolRegistry 执行工具调用
        6. 按 output_schema 提取并校验输出
        7. 存入 StepContext

        Raises:
            SkillNotFoundError: Atomic Skill 未找到
            ToolNotFoundError: 绑定的 Tool 在 ToolRegistry 中未找到
        """
        skill_name = step.skill or ""

        # Step 1: 查找 Atomic Skill 定义
        if not self.skill_loader:
            raise RCAExecutionError(
                step.id, "skill_loader 未配置，无法执行 skill 类型步骤"
            )

        atomic = self.skill_loader.get_atomic_skill(skill_name)
        if atomic is None:
            raise SkillNotFoundError(step.id, skill_name)

        # Step 2: 解析输入参数（模板替换）
        resolved_input: dict[str, Any] = {}
        if step.input:
            resolved_input = ctx.resolve_input_template(step.input)
        elif step.input_from:
            resolved_input = ctx.resolve_input_from(step.input_from)

        logger.debug(
            f"[RCA] Skill 步骤 '{step.id}' → Atomic Skill '{skill_name}', "
            f"输入: {list(resolved_input.keys())}"
        )

        # Step 3-5: 执行 Atomic Skill（底层调用 Tool）
        raw_result = await self._call_atomic_skill(step.id, atomic, resolved_input)

        # Step 6: 按 output_schema 校验输出
        validated = self._validate_skill_output(raw_result, atomic.output_schema)

        # Step 7: 存入上下文
        ctx.set_output(step.id, validated)
        return validated

    async def _call_atomic_skill(
        self,
        step_id: str,
        atomic: AtomicSkill,
        params: dict[str, Any],
    ) -> dict[str, Any]:
        """Atomic Skill 底层执行 —— 通过 ToolRegistry 调用绑定的工具。

        绑定规则：从 Atomic Skill YAML 的 execution.steps[0].tool 字段
        获取底层 ToolRegistry 工具名称。若未声明则回退到 Atomic Skill 的 name。

        Args:
            step_id: 当前步骤 ID（用于错误报告）
            atomic: Atomic Skill 定义
            params: 已解析的输入参数

        Returns:
            工具调用的原始返回数据

        Raises:
            ToolNotFoundError: 绑定的 Tool 未找到
            SecurityViolationError: 安全校验拒绝
        """
        # 优先使用 execution.steps 中声明的 tool，回退到 name
        tool_name = atomic.tool or atomic.name

        # 检查 Tool 是否存在于 ToolRegistry
        if hasattr(self.tools, "has"):
            if not self.tools.has(tool_name):
                raise ToolNotFoundError(step_id, tool_name)
        elif hasattr(self.tools, "get"):
            if self.tools.get(tool_name) is None:
                raise ToolNotFoundError(step_id, tool_name)

        # 安全校验
        self.security.validate_tool_call(tool_name, params)

        # 通过 ToolRegistry 执行工具调用
        raw_result = await self.tools.execute(tool_name, params)

        # 新版返回格式为 dict: {"result": str, "commands": list, ...}
        # 提取 result 字段和 commands 用于日志
        if isinstance(raw_result, dict):
            tool_commands = raw_result.get("commands", [])
            if tool_commands:
                logger.info(f"[RCA] 🔧 工具执行命令: {tool_commands}")
            result_str = raw_result.get("result", "")
            # 检查工具返回是否为错误信息
            if isinstance(result_str, str) and result_str.startswith("Error:"):
                logger.error(f"[RCA] 工具 '{tool_name}' 执行失败: {result_str}")
                if "missing required" in result_str or "Invalid parameters" in result_str:
                    raise RCAExecutionError(
                        step_id,
                        f"工具 '{tool_name}' 参数校验失败: {result_str}",
                    )
                raise RCAExecutionError(
                    step_id,
                    f"工具 '{tool_name}' 执行返回错误: {result_str}",
                )
            return raw_result

        # 兼容旧版返回 str 的情况
        if isinstance(raw_result, str):
            if raw_result.startswith("Error:"):
                logger.error(f"[RCA] 工具 '{tool_name}' 执行失败: {raw_result}")
                if "missing required" in raw_result or "Invalid parameters" in raw_result:
                    raise RCAExecutionError(
                        step_id,
                        f"工具 '{tool_name}' 参数校验失败: {raw_result}",
                    )
                raise RCAExecutionError(
                    step_id,
                    f"工具 '{tool_name}' 执行返回错误: {raw_result}",
                )
            try:
                parsed = json.loads(raw_result)
                if isinstance(parsed, dict):
                    return parsed
            except (json.JSONDecodeError, ValueError):
                pass
            return {"result": raw_result}

        return {"result": str(raw_result)}

    @staticmethod
    def _validate_skill_output(
        raw: dict[str, Any],
        output_schema: dict[str, str],
    ) -> dict[str, Any]:
        """按 output_schema 提取并校验输出字段。

        - 仅保留 output_schema 中声明的字段（丢弃多余字段）
        - 缺失字段填充 None 并记录 WARNING
        - 确保输出结构与 output_schema 一致

        Args:
            raw: 工具调用的原始返回数据
            output_schema: Atomic Skill 的 output_schema {字段名: 类型}

        Returns:
            按 output_schema 过滤后的输出字典
        """
        if not output_schema:
            return raw

        validated: dict[str, Any] = {}
        for field_name, field_type in output_schema.items():
            if field_name in raw:
                validated[field_name] = raw[field_name]
            else:
                validated[field_name] = None
                logger.warning(
                    f"[RCA] output_schema 字段 '{field_name}' 在工具返回中缺失，填充 None"
                )
        return validated

    async def _execute_llm_step(
        self,
        step: SkillStep,
        ctx: StepContext,
    ) -> dict[str, Any]:
        """执行 LLM 类型步骤。

        流程:
        1. 从 input / input_from 解析前置步骤输出
        2. 渲染 prompt 模板
        3. 构建单轮 SLM 调用消息（最小上下文）
        4. 调用 LLMProvider.chat()
        5. 解析 SLM 返回的 JSON 结果
        6. 校验输出是否匹配 output_schema
        7. 存入 StepContext
        """
        # 1. 解析引用：优先使用 input 模板映射，回退到 input_from
        extra_vars: dict[str, Any] = {}
        if step.input:
            extra_vars = ctx.resolve_input_template(step.input)
        elif step.input_from:
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
        1. 解析 input 模板变量（支持 {{stepId.field}} 和 input_from）
        2. 安全校验（白名单检查）
        3. 通过 ToolRegistry 执行工具调用
        4. 解析返回结果
        5. 按 output_schema 存入 StepContext
        """
        tool_name = step.tool or ""

        # 1. 解析输入参数：优先使用 input 模板映射，回退到 input_from
        tool_input: dict[str, Any] = {}
        if step.input:
            tool_input = ctx.resolve_input_template(step.input)
        elif step.input_from:
            tool_input = ctx.resolve_input_from(step.input_from)

        # 2. 安全校验
        self.security.validate_tool_call(tool_name, tool_input)

        # 3. 执行工具
        exec_ret = await self.tools.execute(tool_name, tool_input)

        # 提取 commands 日志
        if isinstance(exec_ret, dict):
            tool_commands = exec_ret.get("commands", [])
            if tool_commands:
                logger.info(f"[RCA] 🔧 工具执行命令: {tool_commands}")

        # 4-5. 解析并存储
        output = self._parse_tool_output(exec_ret, step)
        ctx.set_output(step.id, output)
        return output

    @staticmethod
    def _parse_tool_output(result: Any, step: SkillStep) -> dict[str, Any]:
        """解析工具返回结果。"""
        # 新版返回格式为 dict: {"result": str, "commands": list, ...}
        if isinstance(result, dict):
            return result

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
