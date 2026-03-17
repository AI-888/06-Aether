"""Agent loop: the core processing engine."""

import asyncio
import json
import re
import time
from pathlib import Path
from typing import Any

from loguru import logger

from nanobot.agent.context import ContextBuilder
from nanobot.agent.subagent import SubagentManager
from nanobot.agent.tools.cron import CronTool
from nanobot.agent.tools.knowledge import KnowledgeSearchTool
from nanobot.agent.tools.mcp import MCPTool
from nanobot.agent.tools.message import MessageTool
from nanobot.agent.tools.registry import ToolRegistry

from nanobot.agent.tools.kubectl import KubectlGetPodsTool, KubectlExecLogTool
from nanobot.agent.tools.shell import ExecTool
from nanobot.agent.tools.spawn import SpawnTool
from nanobot.bus.events import InboundMessage, OutboundMessage
from nanobot.bus.queue import MessageBus
from nanobot.providers.base import LLMProvider
from nanobot.session.manager import SessionManager


class AgentLoop:
    """
    The agent loop is the core processing engine.
    
    It:
    1. Receives messages from the bus
    2. Builds context with history, memory, skills
    3. Calls the LLM
    4. Executes tool calls
    5. Sends responses back
    """

    def __init__(
            self,
            bus: MessageBus,
            provider: LLMProvider,
            workspace: Path,
            model: str | None = None,
            max_iterations: int = 3,
            brave_api_key: str | None = None,
            exec_config: "ExecToolConfig | None" = None,
            cron_service: "CronService | None" = None,
            restrict_to_workspace: bool = False,
            session_manager: SessionManager | None = None,
            custom_prompt: str | None = None,
    ):
        from nanobot.config.schema import ExecToolConfig
        self.bus = bus
        self.provider = provider
        self.workspace = workspace
        self.model = model or provider.get_default_model()
        self.max_iterations = max_iterations
        self.brave_api_key = brave_api_key
        self.exec_config = exec_config or ExecToolConfig()
        self.cron_service = cron_service
        self.restrict_to_workspace = restrict_to_workspace

        self.context = ContextBuilder(workspace)
        self.sessions = session_manager or SessionManager(workspace)
        self.tools = ToolRegistry()
        self.subagents = SubagentManager(
            provider=provider,
            workspace=workspace,
            bus=bus,
            model=self.model,
            brave_api_key=brave_api_key,
            exec_config=self.exec_config,
            restrict_to_workspace=restrict_to_workspace,
        )

        self._running = False
        self._register_default_tools()

        self.custom_prompt = custom_prompt
        if self.custom_prompt:
            logger.info("Custom prompt provided, using it instead of default context builder")

    def _register_default_tools(self) -> None:
        """Register the default set of tools."""
        # Shell tool (register first to improve compatibility with models that
        # emit placeholder tool names like function/function_1).
        self.tools.register(ExecTool(
            working_dir=str(self.workspace),
            timeout=self.exec_config.timeout,
            restrict_to_workspace=self.restrict_to_workspace,
        ))

        # File tools (restrict to workspace if configured)
        # allowed_dir = self.workspace if self.restrict_to_workspace else None
        # self.tools.register(ReadFileTool(allowed_dir=allowed_dir))
        # self.tools.register(WriteFileTool(allowed_dir=allowed_dir))
        # self.tools.register(EditFileTool(allowed_dir=allowed_dir))
        # self.tools.register(ListDirTool(allowed_dir=allowed_dir))

        # Web tools
        # self.tools.register(WebSearchTool(api_key=self.brave_api_key))
        # self.tools.register(WebFetchTool())

        # Message tool
        # message_tool = MessageTool(send_callback=self.bus.publish_outbound)
        # self.tools.register(message_tool)

        # Spawn tool (for subagents)
        # spawn_tool = SpawnTool(manager=self.subagents)
        # self.tools.register(spawn_tool)

        # Cron tool (for scheduling)
        # if self.cron_service:
        #     self.tools.register(CronTool(self.cron_service))

        # MCP tools (for Model Context Protocol integration)
        self.tools.register(MCPTool())
        # self.tools.register(MCPKnowledgeSearchTool())

        # Knowledge base tools (for local knowledge storage and retrieval)
        self.tools.register(KnowledgeSearchTool())
        # self.tools.register(KnowledgeAddTool())
        # self.tools.register(DomainKnowledgeTool())
        # self.tools.register(KnowledgeExportTool())

        # kubectl 通用工具
        self.tools.register(KubectlGetPodsTool())
        self.tools.register(KubectlExecLogTool())

    async def run(self) -> None:
        """Run the agent loop, processing messages from the bus."""
        self._running = True
        logger.info("Agent loop started")

        while self._running:
            try:
                # Wait for next message
                msg = await asyncio.wait_for(
                    self.bus.consume_inbound(),
                    timeout=1.0
                )

                # Process it
                try:
                    response = await self._process_message(msg)
                    if response:
                        await self.bus.publish_outbound(response)
                except Exception as e:
                    logger.error(f"Error processing message: {e}")
                    # Send error response
                    await self.bus.publish_outbound(OutboundMessage(
                        channel=msg.channel,
                        chat_id=msg.chat_id,
                        content=f"Sorry, I encountered an error: {str(e)}"
                    ))
            except asyncio.TimeoutError:
                continue

    def stop(self) -> None:
        """Stop the agent loop."""
        self._running = False
        logger.info("Agent loop stopping")

    async def _process_message(self, msg: InboundMessage) -> OutboundMessage | None:
        """
        Process a single inbound message.
        
        Args:
            msg: The inbound message to process.
        
        Returns:
            The response message, or None if no response needed.
        """
        # Handle system messages (subagent announces)
        # The chat_id contains the original "channel:chat_id" to route back to
        if msg.channel == "system":
            return await self._process_system_message(msg)

        preview = msg.content[:80] + "..." if len(msg.content) > 80 else msg.content
        logger.info(f"Processing message from {msg.channel}:{msg.sender_id}: {preview}")
        logger.info(f"[LOOP] 📥 Received user message: {msg.content}")

        # Get or create session
        session = self.sessions.get_or_create(msg.session_key)

        # Update tool contexts
        message_tool = self.tools.get("message")
        if isinstance(message_tool, MessageTool):
            message_tool.set_context(msg.channel, msg.chat_id)

        spawn_tool = self.tools.get("spawn")
        if isinstance(spawn_tool, SpawnTool):
            spawn_tool.set_context(msg.channel, msg.chat_id)

        cron_tool = self.tools.get("cron")
        if isinstance(cron_tool, CronTool):
            cron_tool.set_context(msg.channel, msg.chat_id)

        # 可从上层传入额外系统上下文；需要时可关闭自动知识库查询
        metadata = msg.metadata or {}
        additional_context = metadata.get("additional_context")
        disable_auto_kb = bool(metadata.get("disable_auto_kb", False))

        knowledge_context = additional_context
        if knowledge_context is None and not disable_auto_kb:
            # 在构建消息前优先查询知识库
            knowledge_context = await self._query_knowledge_base(msg.content)

        # 构建初始消息（如果存在知识库查询结果，将其作为额外上下文）
        messages = self.context.build_messages(
            history=session.get_history(),
            current_message=msg.content,
            media=msg.media if msg.media else None,
            channel=msg.channel,
            chat_id=msg.chat_id,
            additional_context=knowledge_context  # 添加知识库查询结果作为额外上下文
        )

        # Agent loop
        iteration = 0
        final_content = None

        # 记录整个消息处理的开始时间
        process_start_time = time.time()

        while iteration < self.max_iterations:
            iteration += 1

            logger.info(f"[LOOP] 🔄 Agent iteration {iteration}/{self.max_iterations}")
            logger.info(f"[LOOP] 📝 Context messages count: {len(messages)}")

            # Log the last user message for context
            for msg_item in reversed(messages):
                if msg_item.get("role") == "user":
                    content_preview = str(msg_item.get("content", ""))[:200]
                    logger.info(f"[LOOP] 💬 Last user message: {content_preview}...")
                    break

            # Call LLM
            logger.info(f"[LOOP] 🤖 Calling LLM with model: {self.model}")

            # 记录LLM调用开始时间
            llm_start_time = time.time()

            # 检查是否有流式回调函数
            stream_callback = getattr(self, 'stream_callback', None)

            # 如果存在流式回调，传递迭代计数信息
            if stream_callback:
                # 发送迭代开始信息
                iteration_info = {
                    "content": f"🔄 第{iteration}次迭代开始处理...\\n",
                    "is_iteration_start": True,
                    "iteration_count": iteration,
                    "timestamp": llm_start_time,
                    "duration_from_start": round(llm_start_time - process_start_time, 3)
                }
                if asyncio.iscoroutinefunction(stream_callback):
                    await stream_callback(iteration_info)
                else:
                    stream_callback(iteration_info)

            response = await self.provider.chat(
                messages=messages,
                tools=self.tools.get_definitions(),
                model=self.model,
                stream=bool(stream_callback),
                stream_callback=stream_callback,
                purpose="agent_loop",
            )

            # 记录LLM调用结束时间并计算耗时
            llm_end_time = time.time()
            llm_duration = llm_end_time - llm_start_time
            logger.info(f"[LOOP] ⏱️  LLM调用耗时: {llm_duration:.3f}秒")

            # Log LLM response
            response_preview = response.content if response.content else "(no content)"
            logger.info(f"[LOOP] 🤖 LLM response content: {response_preview}")
            logger.info(f"[LOOP] 🤖 LLM response has_tool_calls: {response.has_tool_calls}")

            if response.has_tool_calls:
                logger.info(f"[LOOP] 🔧 LLM requested {len(response.tool_calls)} tool call(s)")
                for tc in response.tool_calls:
                    logger.info(f"[LOOP] 🔧   - Tool: {tc.name}")
            else:
                logger.info(f"[LOOP] ✅ LLM provided final response (no tool calls)")

            # Handle tool calls
            if response.has_tool_calls:
                # Add assistant message with tool calls
                tool_call_dicts = [
                    {
                        "id": tc.id,
                        "type": "function",
                        "function": {
                            "name": tc.name,
                            "arguments": json.dumps(tc.arguments)  # Must be JSON string
                        }
                    }
                    for tc in response.tool_calls
                ]
                messages = self.context.add_assistant_message(
                    messages, response.content, tool_call_dicts,
                    reasoning_content=response.reasoning_content,
                )

                # Execute tools
                for tool_call in response.tool_calls:
                    tool_name, tool_args = self._repair_tool_call(
                        tool_call.name,
                        tool_call.arguments,
                        msg.content,
                    )
                    args_str = json.dumps(tool_args, ensure_ascii=False)
                    logger.info(f"[LOOP] 🔧 执行工具: {tool_name}")
                    logger.info(f"[LOOP] 🔧 工具输入: {args_str[:500]}...")

                    # 记录开始时间
                    start_time = time.time()

                    # 发送工具开始执行的信息到前端
                    if stream_callback:
                        # 如果是exec工具，显示具体的命令而不是"exec"
                        display_tool_name = tool_name
                        if tool_name == "exec" and isinstance(tool_args, dict):
                            command = tool_args.get("command", "")
                            if command:
                                # 提取命令的第一个单词作为显示名称
                                command_parts = command.strip().split()
                                if command_parts:
                                    display_tool_name = f"exec: {command_parts[0]}"

                        tool_start_info = {
                            "content": f"🔧 开始执行工具: {display_tool_name}\\n工具参数: {args_str[:1000]}...\\n",
                            "is_tool_call": True,
                            "tool_args": tool_args,
                            "tool_name": display_tool_name,
                            "tool_status": "start"
                        }
                        if asyncio.iscoroutinefunction(stream_callback):
                            await stream_callback(tool_start_info)
                        else:
                            stream_callback(tool_start_info)

                    try:
                        result = await self.tools.execute(tool_name, tool_args)

                        # 计算执行耗时
                        end_time = time.time()
                        duration = end_time - start_time

                        result_preview = str(result)[:300] if result else "(empty result)"
                        logger.info(f"[LOOP] 🔧 工具输出: {result_preview}...")
                        logger.info(f"[LOOP] ⏱️  工具执行耗时: {duration:.3f}秒")

                        # 发送工具执行结果到前端
                        if stream_callback:
                            # 如果是exec工具，显示具体的命令而不是"exec"
                            display_tool_name = tool_name
                            if tool_name == "exec" and isinstance(tool_args, dict):
                                command = tool_args.get("command", "")
                                if command:
                                    # 提取命令的第一个单词作为显示名称
                                    command_parts = command.strip().split()
                                    if command_parts:
                                        display_tool_name = f"exec: {command_parts[0]}"

                            tool_result_info = {
                                "content": f"✅ 工具执行完成: {display_tool_name}\\n执行耗时: {duration:.3f}秒\\n执行结果: {result_preview}\\n",
                                "is_tool_call": True,
                                "tool_name": display_tool_name,
                                "tool_args": tool_args,
                                "tool_status": "completed",
                                "tool_duration": duration,
                                "tool_result": result_preview
                            }
                            if asyncio.iscoroutinefunction(stream_callback):
                                await stream_callback(tool_result_info)
                            else:
                                stream_callback(tool_result_info)

                        messages = self.context.add_tool_result(
                            messages, tool_call.id, tool_name, result
                        )
                    except Exception as e:
                        # 计算执行耗时
                        end_time = time.time()
                        duration = end_time - start_time

                        error_msg = f"工具执行失败: {str(e)}"
                        logger.error(f"[LOOP] ❌ {error_msg}")
                        logger.error(f"[LOOP] ⏱️  工具执行耗时: {duration:.3f}秒")

                        # 发送工具执行错误到前端
                        if stream_callback:
                            # 如果是exec工具，显示具体的命令而不是"exec"
                            display_tool_name = tool_name
                            if tool_name == "exec" and isinstance(tool_args, dict):
                                command = tool_args.get("command", "")
                                if command:
                                    # 提取命令的第一个单词作为显示名称
                                    command_parts = command.strip().split()
                                    if command_parts:
                                        display_tool_name = f"exec: {command_parts[0]}"

                            tool_error_info = {
                                "content": f"❌ 工具执行失败: {display_tool_name}\\n错误信息: {error_msg}\\n执行耗时: {duration:.3f}秒\\n",
                                "is_tool_call": True,
                                "tool_name": display_tool_name,
                                "tool_args": tool_args,
                                "tool_status": "error",
                                "tool_duration": duration,
                                "tool_error": error_msg
                            }
                            if asyncio.iscoroutinefunction(stream_callback):
                                await stream_callback(tool_error_info)
                            else:
                                stream_callback(tool_error_info)

                        # 添加错误结果到消息中
                        messages = self.context.add_tool_result(
                            messages, tool_call.id, tool_name, error_msg
                        )
            else:
                # No tool calls, we're done
                fallback_content = await self._fallback_exec_on_empty_response(
                    msg.content,
                    response.content,
                )
                final_content = fallback_content if fallback_content is not None else response.content
                break

        if final_content is None:
            final_content = "I've completed processing but have no response to give."

        # Log response preview
        logger.info(f"[LOOP] 📤 Final response generated (length: {len(final_content)} chars)")
        preview = final_content[:120] + "..." if len(final_content) > 120 else final_content
        logger.info(f"[LOOP] 📤 Response preview: {preview}")

        # 计算整个消息处理的总耗时
        process_end_time = time.time()
        process_duration = process_end_time - process_start_time
        logger.info(f"[LOOP] ⏱️  整个消息处理总耗时: {process_duration:.3f}秒")

        # 记录详细的耗时统计
        logger.info(f"[LOOP] 📊 耗时统计详情:")
        logger.info(f"[LOOP] 📊 - LLM调用总耗时: {llm_duration:.3f}秒")
        logger.info(f"[LOOP] 📊 - 工具执行总耗时: {process_duration - llm_duration:.3f}秒")
        logger.info(f"[LOOP] 📊 - 总迭代次数: {iteration}次")

        # Save to session
        session.add_message("user", msg.content)
        session.add_message("assistant", final_content)
        self.sessions.save(session)

        return OutboundMessage(
            channel=msg.channel,
            chat_id=msg.chat_id,
            content=final_content,
            metadata=msg.metadata or {},  # Pass through for channel-specific needs (e.g. Slack thread_ts)
        )

    async def _process_system_message(self, msg: InboundMessage) -> OutboundMessage | None:
        """
        Process a system message (e.g., subagent announce).
        
        The chat_id field contains "original_channel:original_chat_id" to route
        the response back to the correct destination.
        """
        logger.info(f"Processing system message from {msg.sender_id}")

        # Parse origin from chat_id (format: "channel:chat_id")
        if ":" in msg.chat_id:
            parts = msg.chat_id.split(":", 1)
            origin_channel = parts[0]
            origin_chat_id = parts[1]
        else:
            # Fallback
            origin_channel = "cli"
            origin_chat_id = msg.chat_id

        # Use the origin session for context
        session_key = f"{origin_channel}:{origin_chat_id}"
        session = self.sessions.get_or_create(session_key)

        # Update tool contexts
        message_tool = self.tools.get("message")
        if isinstance(message_tool, MessageTool):
            message_tool.set_context(origin_channel, origin_chat_id)

        spawn_tool = self.tools.get("spawn")
        if isinstance(spawn_tool, SpawnTool):
            spawn_tool.set_context(origin_channel, origin_chat_id)

        cron_tool = self.tools.get("cron")
        if isinstance(cron_tool, CronTool):
            cron_tool.set_context(origin_channel, origin_chat_id)

        # Build messages with the announce content
        messages = self.context.build_messages(
            history=session.get_history(),
            current_message=msg.content,
            channel=origin_channel,
            chat_id=origin_chat_id,
        )

        # Agent loop (limited for announce handling)
        iteration = 0
        final_content = None

        # 记录整个系统消息处理的开始时间
        process_start_time = time.time()
        llm_duration = 0.0

        while iteration < self.max_iterations:
            iteration += 1

            # 记录LLM调用开始时间
            llm_start_time = time.time()

            response = await self.provider.chat(
                messages=messages,
                tools=self.tools.get_definitions(),
                model=self.model,
                purpose="agent_loop_system",
            )

            # 记录LLM调用结束时间并计算耗时
            llm_end_time = time.time()
            llm_duration += llm_end_time - llm_start_time

            if response.has_tool_calls:
                tool_call_dicts = [
                    {
                        "id": tc.id,
                        "type": "function",
                        "function": {
                            "name": tc.name,
                            "arguments": json.dumps(tc.arguments)
                        }
                    }
                    for tc in response.tool_calls
                ]
                messages = self.context.add_assistant_message(
                    messages, response.content, tool_call_dicts,
                    reasoning_content=response.reasoning_content,
                )

                for tool_call in response.tool_calls:
                    tool_name, tool_args = self._repair_tool_call(
                        tool_call.name,
                        tool_call.arguments,
                        msg.content,
                    )
                    args_str = json.dumps(tool_args, ensure_ascii=False)
                    logger.info(f"[SYSTEM] 🔧 执行工具: {tool_name}")
                    logger.info(f"[SYSTEM] 🔧 工具输入: {args_str[:200]}...")

                    # 记录开始时间
                    start_time = time.time()

                    try:
                        result = await self.tools.execute(tool_name, tool_args)

                        # 计算执行耗时
                        end_time = time.time()
                        duration = end_time - start_time

                        result_preview = str(result)[:300] if result else "(empty result)"
                        logger.info(f"[SYSTEM] 🔧 工具输出: {result_preview}...")
                        logger.info(f"[SYSTEM] ⏱️  工具执行耗时: {duration:.3f}秒")

                        messages = self.context.add_tool_result(
                            messages, tool_call.id, tool_name, result
                        )
                    except Exception as e:
                        # 计算执行耗时
                        end_time = time.time()
                        duration = end_time - start_time

                        error_msg = f"工具执行失败: {str(e)}"
                        logger.error(f"[SYSTEM] ❌ {error_msg}")
                        logger.error(f"[SYSTEM] ⏱️  工具执行耗时: {duration:.3f}秒")

                        # 添加错误结果到消息中
                        messages = self.context.add_tool_result(
                            messages, tool_call.id, tool_name, error_msg
                        )
            else:
                final_content = response.content
                break

        if final_content is None:
            final_content = "Background task completed."

        # 计算整个系统消息处理的总耗时
        process_end_time = time.time()
        process_duration = process_end_time - process_start_time
        logger.info(f"[SYSTEM] ⏱️  系统消息处理总耗时: {process_duration:.3f}秒")

        # 记录详细的耗时统计
        logger.info(f"[SYSTEM] 📊 耗时统计详情:")
        logger.info(f"[SYSTEM] 📊 - LLM调用总耗时: {llm_duration:.3f}秒")
        logger.info(f"[SYSTEM] 📊 - 工具执行总耗时: {process_duration - llm_duration:.3f}秒")
        logger.info(f"[SYSTEM] 📊 - 总迭代次数: {iteration}次")

        # Save to session (mark as system message in history)
        session.add_message("user", f"[System: {msg.sender_id}] {msg.content}")
        session.add_message("assistant", final_content)
        self.sessions.save(session)

        return OutboundMessage(
            channel=origin_channel,
            chat_id=origin_chat_id,
            content=final_content
        )

    def _repair_tool_call(
            self,
            name: str,
            args: dict[str, Any] | Any,
            user_text: str,
    ) -> tuple[str, dict[str, Any] | Any]:
        """Best-effort repair for malformed tool calls from small models."""
        repaired_name = name
        repaired_args = args

        if not self.tools.has(repaired_name):
            lowered = (repaired_name or "").lower()
            if lowered == "function":
                repaired_name = self.tools.tool_names[0] if self.tools.tool_names else repaired_name
            else:
                m = re.fullmatch(r"function_(\d+)", lowered)
                if m:
                    idx = int(m.group(1)) - 1
                    names = self.tools.tool_names
                    if 0 <= idx < len(names):
                        repaired_name = names[idx]

        if repaired_name == "exec" and isinstance(repaired_args, dict):
            repaired_args = self._repair_exec_args(repaired_args, user_text)

        return repaired_name, repaired_args

    def _repair_exec_args(self, args: dict[str, Any], user_text: str) -> dict[str, Any]:
        """Repair placeholder exec args like argument_1/value wrappers."""
        command = args.get("command")
        if isinstance(command, str) and command.strip() and not self._looks_like_placeholder_command(command):
            return args

        extracted: str | None = None
        for key in ("argument_1", "arg_1", "input", "value"):
            val = args.get(key)
            if isinstance(val, str) and val.strip():
                extracted = val.strip()
                break
            if isinstance(val, dict):
                inner = val.get("value")
                if isinstance(inner, str) and inner.strip():
                    extracted = inner.strip()
                    break

        if extracted and not self._looks_like_placeholder_command(extracted):
            repaired = {"command": extracted}
            if "working_dir" in args:
                repaired["working_dir"] = args["working_dir"]
            return repaired

        inferred = self._infer_exec_command_from_text(user_text, extracted or "")
        if inferred:
            repaired = {"command": inferred}
            if "working_dir" in args:
                repaired["working_dir"] = args["working_dir"]
            return repaired

        if extracted:
            repaired = {"command": extracted}
            if "working_dir" in args:
                repaired["working_dir"] = args["working_dir"]
            return repaired
        return args

    @staticmethod
    def _looks_like_placeholder_command(command: str) -> bool:
        token = command.strip().lower()
        return bool(re.fullmatch(r"[a-z][a-z0-9_:-]{2,}", token)) and " " not in token

    @staticmethod
    def _infer_exec_command_from_text(user_text: str, hint: str = "") -> str | None:
        text = f"{user_text} {hint}".lower()
        if "broker" in text and "pod" in text:
            return "kubectl get pods -Ao wide | grep ocloud-tdmq-rocketmq5-broker"
        if ("namesrv" in text or "nameserver" in text) and "pod" in text:
            return "kubectl get pods -Ao wide | grep ocloud-tdmq-rocketmq5-namesrv"
        if "proxy" in text and "pod" in text:
            return "kubectl get pods -Ao wide | grep ocloud-tdmq-rocketmq5-proxy"
        if "rocketmq" in text and "pod" in text:
            return "kubectl get pods -Ao wide | grep rocketmq | grep -v cmq"
        return None

    async def _fallback_exec_on_empty_response(
            self,
            user_text: str,
            llm_content: str | None,
    ) -> str | None:
        """
        Fallback for tiny models that return '{}' without emitting tool calls.
        """
        content = (llm_content or "").strip()
        if content not in {"", "{}"}:
            return None

        command = self._infer_exec_command_from_text(user_text)
        if not command:
            return None

        result = await self.tools.execute("exec", {"command": command})
        return f"已执行命令:\n{command}\n\n结果:\n{result}"

    async def process_direct(
            self,
            content: str,
            session_key: str = "cli:direct",
            channel: str = "cli",
            chat_id: str = "direct",
            additional_context: str | None = None,
            disable_auto_kb: bool = False,
    ) -> str:
        """
        Process a message directly (for CLI or cron usage).
        
        Args:
            content: The message content.
            session_key: Session identifier.
            channel: Source channel (for context).
            chat_id: Source chat ID (for context).
        
        Returns:
            The agent's response.
        """
        msg = InboundMessage(
            channel=channel,
            sender_id="user",
            chat_id=chat_id,
            content=content,
            metadata={
                "additional_context": additional_context,
                "disable_auto_kb": disable_auto_kb,
            },
        )

        # 记录开始时间
        start_time = time.time()

        # 设置流式回调函数，传递迭代计数和耗时信息
        original_stream_callback = getattr(self, 'stream_callback', None)

        if original_stream_callback:
            async def enhanced_stream_callback(context_info: dict):
                """增强的流式回调，添加迭代计数和耗时信息"""
                # 添加迭代计数和耗时信息
                context_info['iteration_count'] = context_info.get('iteration_count', 0)
                context_info['timestamp'] = time.time()
                context_info['duration_from_start'] = round(time.time() - start_time, 3)

                # 调用原始回调函数
                if asyncio.iscoroutinefunction(original_stream_callback):
                    await original_stream_callback(context_info)
                else:
                    original_stream_callback(context_info)

            self.stream_callback = enhanced_stream_callback

        response = await self._process_message(msg)

        # 恢复原始回调函数
        if original_stream_callback:
            self.stream_callback = original_stream_callback

        return response.content if response else ""

    async def stream_callback(self, context_info: dict) -> None:
        """
        流式回调函数，能够区分不同类型的LLM响应内容。
        
        Args:
            context_info: 包含流式响应上下文信息的字典，包含：
                - content: 内容块
                - model: 模型名称
                - is_tool_call: 是否是工具调用
                - is_reasoning: 是否是推理内容
                - is_final_answer: 是否是最终答案
                - total_length: 总内容长度
                - chunk_index: 当前块索引
        """
        # 安全获取content参数，处理可能的字典类型
        content = context_info.get("content", "")

        # 如果content是字典，提取content字段
        if isinstance(content, dict):
            content = content.get("content", "")

        # 确保content是字符串类型
        if not isinstance(content, str):
            logger.warning(f"[STREAM] ⚠️ Invalid content type: {type(content)}")
            logger.warning(f"[STREAM] ⚠️ content: {content}")
            content = str(content)

        if not content.strip():
            return

        # 根据上下文信息确定响应类型
        response_type = self._determine_response_type(context_info)

        # 根据类型进行不同的处理
        if response_type == "reasoning":
            # 意图识别或推理过程
            logger.info(f"[STREAM] 🤔 意图识别 (模型: {context_info.get('model', 'unknown')}): {content}")
            # 这里可以调用UI更新方法，显示意图识别内容

        elif response_type == "tool_call":
            # 工具调用
            logger.info(f"[STREAM] 🔧 工具执行 (模型: {context_info.get('model', 'unknown')}): {content}")
            # 这里可以调用UI更新方法，显示工具执行内容

        elif response_type == "final_answer":
            # 最终答案
            logger.info(f"[STREAM] 💬 最终回答 (模型: {context_info.get('model', 'unknown')}): {content}")
            # 这里可以调用UI更新方法，显示最终回答内容

        else:
            # 普通文本内容
            logger.info(f"[STREAM] 📝 普通内容 (模型: {context_info.get('model', 'unknown')}): {content}")
            # 这里可以调用UI更新方法，显示普通内容

    def _determine_response_type(self, context_info: dict) -> str:
        """
        基于上下文信息确定响应内容的类型。
        
        Args:
            context_info: 包含流式响应上下文信息的字典
            
        Returns:
            响应类型："reasoning"、"tool_call"、"final_answer"、"normal"
        """
        # 安全获取content参数，处理可能的字典类型
        content = context_info.get("content", "")

        # 如果content是字典，提取content字段
        if isinstance(content, dict):
            content = content.get("content", "")

        # 确保content是字符串类型
        if not isinstance(content, str):
            logger.warning(f"Unexpected content type: {type(content)}")
            logger.warning(f"Unexpected content: {content}")
            content = str(content)

        content_lower = content.lower().strip()

        # 优先使用上下文信息中的标志
        if context_info.get("is_tool_call", False):
            return "tool_call"

        if context_info.get("is_reasoning", False):
            return "reasoning"

        if context_info.get("is_final_answer", False):
            return "final_answer"

        # 如果没有上下文标志，则基于内容分析

        # 检查是否是推理/意图识别内容
        reasoning_keywords = ["think", "reason", "analyze", "consider", "plan", "strategy", "步骤", "思考", "分析"]
        if any(keyword in content_lower for keyword in reasoning_keywords):
            return "reasoning"

        # 检查是否是工具调用
        tool_keywords = ["tool", "function", "call", "execute", "run", "工具", "函数", "调用", "执行"]
        if any(keyword in content_lower for keyword in tool_keywords):
            return "tool_call"

        # 检查是否是最终答案的开始
        answer_keywords = ["answer", "result", "conclusion", "summary", "回答", "结果", "结论", "总结"]
        if any(keyword in content_lower for keyword in answer_keywords):
            return "final_answer"

        # 检查JSON格式的工具调用
        if content.strip().startswith('{') and 'name' in content_lower and 'arguments' in content_lower:
            return "tool_call"

        return "normal"

    async def _query_knowledge_base(self, user_input: str) -> str | None:
        """
        根据用户输入自动查询知识库，返回相关的知识内容作为上下文。
        
        Args:
            user_input: 用户输入的内容
            
        Returns:
            知识库查询结果，如果没有相关结果则返回None
        """
        from loguru import logger

        # 如果用户输入太短，不进行知识库查询
        if len(user_input.strip()) < 5:
            logger.info("[KNOWLEDGE] 📝 用户输入太短，跳过知识库查询")
            return None

        # 发送知识库查询开始的流式回调
        if hasattr(self, 'stream_callback') and self.stream_callback:
            await self._send_stream_callback({
                "content": "🔍 正在查询知识库...",
                "is_knowledge_query": True,
                "knowledge_status": "start",
                "knowledge_query": user_input[:100]
            })

        # 自动推断知识库查询的domain和query
        domain, query = self._infer_knowledge_query(user_input)

        if not domain or not query:
            logger.info("[KNOWLEDGE] 📝 无法推断知识库查询参数，跳过查询")
            if hasattr(self, 'stream_callback') and self.stream_callback:
                await self._send_stream_callback({
                    "content": "⚠️ 无法确定查询领域，跳过知识库查询",
                    "is_knowledge_query": True,
                    "knowledge_status": "skipped"
                })
            return None

        logger.info(f"[KNOWLEDGE] 🔍 开始知识库查询:")
        logger.info(f"[KNOWLEDGE]   - Domain: {domain}")
        logger.info(f"[KNOWLEDGE]   - Query: {query}")

        # 发送查询参数的流式回调
        if hasattr(self, 'stream_callback') and self.stream_callback:
            await self._send_stream_callback({
                "content": f"📚 查询领域: {domain}\n🔎 查询关键词: {query}",
                "is_knowledge_query": True,
                "knowledge_status": "searching",
                "knowledge_domain": domain,
                "knowledge_query": query
            })

        try:
            # 使用KnowledgeSearchTool执行查询
            knowledge_tool = self.tools.get("knowledge_search")
            if not knowledge_tool:
                logger.warning("[KNOWLEDGE] ⚠️ KnowledgeSearchTool未注册，跳过查询")
                if hasattr(self, 'stream_callback') and self.stream_callback:
                    await self._send_stream_callback({
                        "content": "⚠️ 知识库工具未注册",
                        "is_knowledge_query": True,
                        "knowledge_status": "error"
                    })
                return None

            # 执行知识库查询
            result = await knowledge_tool.execute(
                domain=domain,
                query=query,
                limit=5  # 限制返回结果数量
            )

            if "No knowledge found" in result or "Error" in result:
                logger.info(f"[KNOWLEDGE] ⚠️ 知识库查询无结果: {result[:100]}...")
                if hasattr(self, 'stream_callback') and self.stream_callback:
                    await self._send_stream_callback({
                        "content": "📭 未找到相关知识",
                        "is_knowledge_query": True,
                        "knowledge_status": "no_results"
                    })
                return None

            logger.info(f"[KNOWLEDGE] ✅ 知识库查询成功，返回{len(result)}字符的结果")

            # 解析结果数量
            import re
            result_count_match = re.search(r'Found (\d+) knowledge items', result)
            result_count = int(result_count_match.group(1)) if result_count_match else 0

            # 发送查询成功的流式回调
            if hasattr(self, 'stream_callback') and self.stream_callback:
                await self._send_stream_callback({
                    "content": f"✅ 找到 {result_count} 条相关知识",
                    "is_knowledge_query": True,
                    "knowledge_status": "success",
                    "knowledge_count": result_count,
                    "knowledge_result": result[:500] + "..." if len(result) > 500 else result
                })

            # 格式化查询结果作为上下文
            knowledge_context = f"""
📚 **相关知识库信息**

以下是与您的问题相关的知识库内容，供参考：

{result}

---
请基于以上知识库信息，结合您的具体问题提供更准确的回答。
"""

            return knowledge_context

        except Exception as e:
            logger.error(f"[KNOWLEDGE] ❌ 知识库查询失败: {str(e)}")
            if hasattr(self, 'stream_callback') and self.stream_callback:
                await self._send_stream_callback({
                    "content": f"❌ 知识库查询失败: {str(e)}",
                    "is_knowledge_query": True,
                    "knowledge_status": "error"
                })
            return None

    async def _send_stream_callback(self, context_info: dict):
        """发送流式回调信息的辅助方法."""
        if hasattr(self, 'stream_callback') and self.stream_callback:
            import asyncio
            if asyncio.iscoroutinefunction(self.stream_callback):
                await self.stream_callback(context_info)
            else:
                self.stream_callback(context_info)

    def _infer_knowledge_query(self, user_input: str) -> tuple[str | None, str | None]:
        """
        根据用户输入自动推断知识库查询的domain和query。
        
        Args:
            user_input: 用户输入的内容
            
        Returns:
            (domain, query) 元组，如果无法推断则返回(None, None)
        """
        input_lower = user_input.lower()

        # 定义领域关键词映射
        domain_keywords = {
            "rocketmq": ["rocketmq", "tdmq", "消息队列", "mq", "broker", "namesrv", "nameserver", "cluster", "topic",
                         "consumer", "producer", "group"],
            "kubernetes": ["k8s", "kubernetes", "pod", "deployment", "service", "kubectl", "namespace"],
            "general": []  # 通用领域，用于没有匹配到特定领域的情况
        }

        # 根据关键词匹配领域
        matched_domain = None
        for domain, keywords in domain_keywords.items():
            if any(keyword in input_lower for keyword in keywords):
                matched_domain = domain
                break

        # 如果没有匹配到特定领域，使用通用领域
        if not matched_domain:
            matched_domain = "general"

        # 清理查询关键词：移除标点符号和多余空格
        import re
        query_keywords = re.sub(r'[^\w\s]', ' ', input_lower)
        query_keywords = ' '.join(query_keywords.split())

        # 如果查询关键词为空，使用原始输入的前20个字符
        if not query_keywords.strip():
            query_keywords = user_input[:20].strip()

        logger.info(f"[KNOWLEDGE] 🔍 推断查询参数: domain={matched_domain}, query={query_keywords}")

        return matched_domain, query_keywords
