"""LiteLLM provider implementation for multi-provider support."""

import asyncio
import json
import os
import re
import time
from typing import Any, Callable

import litellm
from litellm import acompletion
from loguru import logger

from nanobot.providers.base import LLMProvider, LLMResponse, ToolCallRequest
from nanobot.providers.registry import find_by_model, find_gateway


class LiteLLMProvider(LLMProvider):
    """
    LLM provider using LiteLLM for multi-provider support.
    
    Supports OpenRouter, Anthropic, OpenAI, Gemini, MiniMax, and many other providers through
    a unified interface.  Provider-specific logic is driven by the registry
    (see providers/registry.py) — no if-elif chains needed here.
    """

    def __init__(
            self,
            api_key: str | None = None,
            api_base: str | None = None,
            default_model: str = "anthropic/claude-opus-4-5",
            extra_headers: dict[str, str] | None = None,
            provider_name: str | None = None,
    ):
        super().__init__(api_key, api_base)
        self.default_model = default_model
        self.extra_headers = extra_headers or {}

        # Detect gateway / local deployment.
        # provider_name (from config key) is the primary signal;
        # api_key / api_base are fallback for auto-detection.
        self._gateway = find_gateway(provider_name, api_key, api_base)

        # Configure environment variables
        if api_key:
            self._setup_env(api_key, api_base, default_model)

        if api_base:
            litellm.api_base = api_base

        # Disable LiteLLM logging noise
        litellm.suppress_debug_info = True
        # Drop unsupported parameters for providers (e.g., gpt-5 rejects some params)
        litellm.drop_params = True

    def _setup_env(self, api_key: str, api_base: str | None, model: str) -> None:
        """Set environment variables based on detected provider."""
        spec = self._gateway or find_by_model(model)
        if not spec:
            return

        # Gateway/local overrides existing env; standard provider doesn't
        if self._gateway:
            os.environ[spec.env_key] = api_key
        else:
            os.environ.setdefault(spec.env_key, api_key)

        # Resolve env_extras placeholders:
        #   {api_key}  → user's API key
        #   {api_base} → user's api_base, falling back to spec.default_api_base
        effective_base = api_base or spec.default_api_base
        for env_name, env_val in spec.env_extras:
            resolved = env_val.replace("{api_key}", api_key)
            resolved = resolved.replace("{api_base}", effective_base)
            os.environ.setdefault(env_name, resolved)

    def _resolve_model(self, model: str) -> str:
        """Resolve model name by applying provider/gateway prefixes."""
        if self._gateway:
            # Gateway mode: apply gateway prefix, skip provider-specific prefixes
            prefix = self._gateway.litellm_prefix
            if self._gateway.strip_model_prefix:
                model = model.split("/")[-1]
            if prefix and not model.startswith(f"{prefix}/"):
                model = f"{prefix}/{model}"
            return model

        # Standard mode: auto-prefix for known providers
        spec = find_by_model(model)
        if spec and spec.litellm_prefix:
            if not any(model.startswith(s) for s in spec.skip_prefixes):
                model = f"{spec.litellm_prefix}/{model}"

        return model

    def _apply_model_overrides(self, model: str, kwargs: dict[str, Any]) -> None:
        """Apply model-specific parameter overrides from the registry."""
        model_lower = model.lower()
        spec = find_by_model(model)
        if spec:
            for pattern, overrides in spec.model_overrides:
                if pattern in model_lower:
                    kwargs.update(overrides)
                    return

    async def chat(
            self,
            messages: list[dict[str, Any]],
            tools: list[dict[str, Any]] | None = None,
            model: str | None = None,
            max_tokens: int = 4096,
            temperature: float = 0.7,
            stream: bool = False,
            stream_callback: Callable | None = None,
    ) -> LLMResponse:
        """
        Send a chat completion request via LiteLLM.
        
        Args:
            messages: List of message dicts with 'role' and 'content'.
            tools: Optional list of tool definitions in OpenAI format.
            model: Model identifier (e.g., 'anthropic/claude-sonnet-4-5').
            max_tokens: Maximum tokens in response.
            temperature: Sampling temperature.
            stream: Whether to stream the response.
            stream_callback: Callback function for streaming chunks.
        
        Returns:
            LLMResponse with content and/or tool calls.
        """
        model = self._resolve_model(model or self.default_model)

        kwargs: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "max_tokens": max_tokens,
            "temperature": temperature,
        }

        # Apply model-specific overrides (e.g. kimi-k2.5 temperature)
        self._apply_model_overrides(model, kwargs)

        # Pass api_key directly — more reliable than env vars alone
        if self.api_key:
            kwargs["api_key"] = self.api_key

        # Pass api_base for custom endpoints
        if self.api_base:
            kwargs["api_base"] = self.api_base

        # Pass extra headers (e.g. APP-Code for AiHubMix)
        if self.extra_headers:
            kwargs["extra_headers"] = self.extra_headers

        if tools:
            kwargs["tools"] = tools
            kwargs["tool_choice"] = "auto"

        # 记录LLM入参
        logger.info(f"[LLM] 调用模型: {model}")
        logger.info(f"[LLM] 入参: {json.dumps(kwargs, ensure_ascii=False)}")

        # 记录开始时间
        start_time = time.time()

        try:
            if stream:
                # 流式输出模式
                kwargs["stream"] = True
                full_content = ""

                response = await acompletion(**kwargs)

                min_chunk_length = 10  # 超过10个字符才触发回调

                async for chunk in response:
                    if hasattr(chunk, 'choices') and chunk.choices:
                        delta = chunk.choices[0].delta
                        if hasattr(delta, 'content') and delta.content:
                            content_chunk = delta.content
                            full_content += content_chunk

                            # 调用流式回调函数，传递更多上下文信息
                            if stream_callback:
                                # 创建上下文信息字典
                                context_info = {
                                    "content": content_chunk,
                                    "model": model,
                                    "is_tool_call": hasattr(delta, 'tool_calls') and delta.tool_calls,
                                    "is_reasoning": self._is_reasoning_content(content_chunk),
                                    "is_final_answer": self._is_final_answer_content(content_chunk),
                                    "total_length": len(full_content),
                                    "chunk_index": len(full_content) - len(content_chunk)
                                }

                                if len(content_chunk) < min_chunk_length:
                                    logger.debug(f"[LLM] 流式调用回调: {json.dumps(context_info, ensure_ascii=False)}")
                                    continue
                                if asyncio.iscoroutinefunction(stream_callback):
                                    await stream_callback(context_info)
                                else:
                                    stream_callback(context_info)

                # 计算耗时
                end_time = time.time()
                duration = end_time - start_time

                # 记录LLM出参和耗时
                logger.info(f"[LLM] 流式调用耗时: {duration:.3f}秒")
                logger.info(f"[LLM] 流式输出内容长度: {len(full_content)}字符")

                # 创建一个模拟的response对象用于解析
                class MockResponse:
                    def __init__(self, content):
                        self.choices = [MockChoice(content)]

                class MockChoice:
                    def __init__(self, content):
                        self.message = MockMessage(content)
                        self.finish_reason = "stop"

                class MockMessage:
                    def __init__(self, content):
                        self.content = content
                        # 解析content中的工具调用
                        self.tool_calls = self._parse_tool_calls_from_content(content)

                    def _parse_tool_calls_from_content(self, content: str):
                        """从流式输出的完整内容中解析工具调用。"""
                        import re
                        import json

                        # 查找JSON格式的工具调用
                        tool_call_pattern = r'\{\s*"name"\s*:\s*"([^"]+)"\s*,\s*"arguments"\s*:\s*(\{[^}]+\})\s*\}'
                        matches = re.findall(tool_call_pattern, content)

                        if not matches:
                            return None

                        tool_calls = []
                        for name, args_str in matches:
                            try:
                                # 解析参数
                                args = json.loads(args_str)

                                # 创建模拟的ToolCall对象
                                class ToolCall:
                                    def __init__(self, name, args):
                                        self.function = Function(name, args)
                                        self.id = f"call_{len(tool_calls)}"

                                class Function:
                                    def __init__(self, name, args):
                                        self.name = name
                                        self.arguments = args

                                tool_calls.append(ToolCall(name, args))
                            except json.JSONDecodeError:
                                continue

                        return tool_calls if tool_calls else None

                mock_response = MockResponse(full_content)
                return self._parse_response(mock_response, tools)
            else:
                # 非流式模式（原有逻辑）
                response = await acompletion(**kwargs)

                # 计算耗时
                end_time = time.time()
                duration = end_time - start_time

                # 记录LLM出参和耗时
                logger.info(f"[LLM] 调用耗时: {duration:.3f}秒")
                logger.info(
                    f"[LLM] 出参: {json.dumps(response.model_dump() if hasattr(response, 'model_dump') else str(response), ensure_ascii=False)}")

                return self._parse_response(response, tools)
        except Exception as e:
            # 计算失败时的耗时
            end_time = time.time()
            duration = end_time - start_time

            logger.error(f"[LLM] 调用失败，耗时: {duration:.3f}秒，错误: {str(e)}")
            # Return error as content for graceful handling
            return LLMResponse(
                content=f"Error calling LLM: {str(e)}",
                finish_reason="error",
            )

    def _is_reasoning_content(self, content: str | dict) -> bool:
        """判断内容是否是推理/意图识别内容。"""
        # 处理字典参数（流式回调传递的上下文信息）

        # 安全处理各种类型的content参数
        if isinstance(content, dict):
            content = content.get("content", "")

        # 确保content是字符串类型
        if not isinstance(content, str):
            content = str(content)

        content_lower = content.lower().strip()
        reasoning_keywords = ["think", "reason", "analyze", "consider", "plan", "strategy", "步骤", "思考", "分析"]
        return any(keyword in content_lower for keyword in reasoning_keywords)

    def _is_final_answer_content(self, content: str | dict) -> bool:
        """判断内容是否是最终答案内容。"""
        # 处理字典参数（流式回调传递的上下文信息）

        # 安全处理各种类型的content参数
        if isinstance(content, dict):
            content = content.get("content", "")

        # 确保content是字符串类型
        if not isinstance(content, str):
            content = str(content)

        content_lower = content.lower().strip()
        answer_keywords = ["answer", "result", "conclusion", "summary", "回答", "结果", "结论", "总结"]
        return any(keyword in content_lower for keyword in answer_keywords)

    def _parse_response(
            self,
            response: Any,
            tools: list[dict[str, Any]] | None = None,
    ) -> LLMResponse:
        """Parse LiteLLM response into our standard format."""
        choice = response.choices[0]
        message = choice.message

        tool_calls = []
        if hasattr(message, "tool_calls") and message.tool_calls:
            for tc in message.tool_calls:
                # Parse arguments from JSON string if needed
                args = tc.function.arguments
                if isinstance(args, str):
                    try:
                        args = json.loads(args)
                    except json.JSONDecodeError:
                        args = {"raw": args}

                tool_name = tc.function.name
                tool_name = self._normalize_tool_name(tool_name, tools)
                args = self._normalize_tool_args(tool_name, args, tools)

                tool_calls.append(ToolCallRequest(
                    id=tc.id,
                    name=tool_name,
                    arguments=args,
                ))

        usage = {}
        if hasattr(response, "usage") and response.usage:
            usage = {
                "prompt_tokens": response.usage.prompt_tokens,
                "completion_tokens": response.usage.completion_tokens,
                "total_tokens": response.usage.total_tokens,
            }

        reasoning_content = getattr(message, "reasoning_content", None)

        return LLMResponse(
            content=message.content,
            tool_calls=tool_calls,
            finish_reason=choice.finish_reason or "stop",
            usage=usage,
            reasoning_content=reasoning_content,
        )

    @staticmethod
    def _valid_tool_names(tools: list[dict[str, Any]] | None) -> list[str]:
        if not tools:
            return []
        names: list[str] = []
        for item in tools:
            fn = item.get("function", {})
            name = fn.get("name")
            if isinstance(name, str) and name:
                names.append(name)
        return names

    def _normalize_tool_name(
            self,
            name: str,
            tools: list[dict[str, Any]] | None,
    ) -> str:
        valid_names = self._valid_tool_names(tools)
        if name in valid_names:
            return name

        lowered = (name or "").strip().lower()
        if not valid_names:
            return name

        # Some local models emit placeholders like "function" / "function_1".
        if lowered == "function":
            return valid_names[0]

        m = re.fullmatch(r"function_(\d+)", lowered)
        if m:
            idx = int(m.group(1)) - 1
            if 0 <= idx < len(valid_names):
                return valid_names[idx]

        if lowered in {"shell", "bash", "command"} and "exec" in valid_names:
            return "exec"

        if len(valid_names) == 1:
            return valid_names[0]

        return name

    def _normalize_tool_args(
            self,
            tool_name: str,
            args: Any,
            tools: list[dict[str, Any]] | None,
    ) -> Any:
        if not isinstance(args, dict):
            return args

        tool_schema: dict[str, Any] | None = None
        if tools:
            for item in tools:
                fn = item.get("function", {})
                if fn.get("name") == tool_name:
                    tool_schema = fn.get("parameters", {})
                    break

        if not tool_schema:
            return args

        properties: dict[str, Any] = tool_schema.get("properties", {}) or {}
        prop_keys = list(properties.keys())
        required = tool_schema.get("required", []) or []

        # If already has any declared key, keep it.
        if any(k in properties for k in args.keys()):
            return args

        def _unwrap(v: Any) -> Any:
            if isinstance(v, dict) and "value" in v and len(v) == 1:
                return v["value"]
            return v

        repaired: dict[str, Any] = {}
        for k, v in args.items():
            m = re.fullmatch(r"(?:arg(?:ument)?_?)(\d+)", str(k).lower())
            if not m:
                continue
            idx = int(m.group(1)) - 1
            if 0 <= idx < len(prop_keys):
                repaired[prop_keys[idx]] = _unwrap(v)

        if repaired:
            return repaired

        # If the tool expects exactly one required field, map the first value.
        if len(required) == 1 and args:
            only_key = required[0]
            first_val = _unwrap(next(iter(args.values())))
            return {only_key: first_val}

        return args

    def get_default_model(self) -> str:
        """Get the default model."""
        return self.default_model
