"""Context builder for assembling agent prompts."""

import base64
import mimetypes
import platform
from pathlib import Path
from typing import Any

from nanobot.agent.memory import MemoryStore
from nanobot.agent.skills import SkillsLoader


class ContextBuilder:
    """
    Builds the context (system prompt + messages) for the agent.
    
    Assembles bootstrap files, memory, skills, and conversation history
    into a coherent prompt for the LLM.
    """

    BOOTSTRAP_FILES = ["AGENTS.md", "SOUL.md", "USER.md", "TOOLS.md"]
    PROMPT_DIR = "prompt"  # 自定义提示词目录

    def __init__(self, workspace: Path):
        self.workspace = workspace
        self.memory = MemoryStore(workspace)
        self.skills = SkillsLoader(workspace)

    def build_system_prompt(self, skill_names: list[str] | None = None) -> str:
        """
        Build the system prompt from bootstrap files, memory, and skills.
        
        Args:
            skill_names: Optional list of skills to include.
        
        Returns:
            Complete system prompt.
        """
        parts = []

        # Core identity
        parts.append(self._get_identity())

        # Bootstrap files
        bootstrap = self._load_bootstrap_files()
        if bootstrap:
            parts.append(bootstrap)

        # Custom prompts from workspace/prompt directory
        custom_prompts = self._load_custom_prompts()
        if custom_prompts:
            parts.append(custom_prompts)

        # Memory context
        memory = self.memory.get_memory_context()
        if memory:
            parts.append(f"# Memory\n\n{memory}")

        # Skills - progressive loading
        # 1. Always-loaded skills: include full content
        always_skills = self.skills.get_always_skills()
        if always_skills:
            always_content = self.skills.load_skills_for_context(always_skills)
            if always_content:
                parts.append(f"# Active Skills\n\n{always_content}")

        # 2. Available skills: only show summary (agent uses read_file to load)
        #         skills_summary = self.skills.build_skills_summary()
        #         if skills_summary:
        #             parts.append(f"""# Skills
        #
        # The following skills extend your capabilities. To use a skill, read its SKILL.md file using the read_file tool.
        # Skills with available="false" need dependencies installed first - you can try installing them with apt/brew.
        #
        # {skills_summary}""")

        return "\n\n---\n\n".join(parts)

    def _get_identity(self) -> str:
        """Get the core identity section."""
        from datetime import datetime
        now = datetime.now().strftime("%Y-%m-%d %H:%M (%A)")
        workspace_path = str(self.workspace.expanduser().resolve())
        system = platform.system()
        runtime = f"{'macOS' if system == 'Darwin' else system} {platform.machine()}, Python {platform.python_version()}"

        return f"""# Identity
                ## Current Time
                {now}

                ## Runtime
                {runtime}
                """

    def _load_bootstrap_files(self) -> str:
        """Load all bootstrap files from workspace."""
        parts = []

        for filename in self.BOOTSTRAP_FILES:
            file_path = self.workspace / filename
            if file_path.exists():
                content = file_path.read_text(encoding="utf-8")
                parts.append(f"## {filename}\n\n{content}")

        return "\n\n".join(parts) if parts else ""

    def _load_custom_prompts(self) -> str:
        """
        Load custom prompt files from workspace/prompt directory.
        
        Supports .md and .txt files. Files are loaded in alphabetical order.
        This allows users to add domain-specific prompts, role definitions,
        or additional context without modifying core files.
        
        Returns:
            Formatted custom prompts content.
        """
        from loguru import logger

        prompt_dir = self.workspace / self.PROMPT_DIR
        if not prompt_dir.exists() or not prompt_dir.is_dir():
            return ""

        parts = []
        prompt_files = sorted(prompt_dir.glob("*.md")) + sorted(prompt_dir.glob("*.txt"))

        for prompt_file in prompt_files:
            try:
                content = prompt_file.read_text(encoding="utf-8")
                # 使用文件名（不含扩展名）作为标题
                title = prompt_file.stem
                parts.append(f"## Custom Prompt: {title}\n\n{content}")
                logger.info(f"[CONTEXT] 📄 Loaded custom prompt: {prompt_file.name}")
            except Exception as e:
                logger.warning(f"[CONTEXT] ⚠️  Failed to load prompt file {prompt_file.name}: {e}")

        if parts:
            return f"# Custom Prompts\n\n" + "\n\n".join(parts)
        return ""

    def build_messages(
            self,
            history: list[dict[str, Any]],
            current_message: str,
            skill_names: list[str] | None = None,
            media: list[str] | None = None,
            channel: str | None = None,
            chat_id: str | None = None,
            additional_context: str | None = None,
    ) -> list[dict[str, Any]]:
        """
        Build the complete message list for an LLM call.

        Args:
            history: Previous conversation messages.
            current_message: The new user message.
            skill_names: Optional skills to include.
            media: Optional list of local file paths for images/media.
            channel: Current channel (telegram, feishu, etc.).
            chat_id: Current chat/user ID.
            additional_context: Optional additional context (e.g., knowledge base results).

        Returns:
            List of messages including system prompt.
        """
        messages = []

        # System prompt
        system_prompt = self.build_system_prompt(skill_names)
        if channel and chat_id:
            system_prompt += f"\n\n## Current Session\nChannel: {channel}\nChat ID: {chat_id}"

        # 如果存在额外上下文（如知识库查询结果），添加到系统提示中
        if additional_context:
            system_prompt += f"\n\n## Additional Context\n{additional_context}"

        messages.append({"role": "system", "content": system_prompt})

        # History
        # messages.extend(history)

        # Current message (with optional image attachments)
        user_content = self._build_user_content(current_message, media)
        messages.append({"role": "user", "content": user_content})

        return messages

    def _build_user_content(self, text: str, media: list[str] | None) -> str | list[dict[str, Any]]:
        """Build user message content with optional base64-encoded images."""
        if not media:
            return text

        images = []
        for path in media:
            p = Path(path)
            mime, _ = mimetypes.guess_type(path)
            if not p.is_file() or not mime or not mime.startswith("image/"):
                continue
            b64 = base64.b64encode(p.read_bytes()).decode()
            images.append({"type": "image_url", "image_url": {"url": f"data:{mime};base64,{b64}"}})

        if not images:
            return text
        return images + [{"type": "text", "text": text}]

    def add_tool_result(
            self,
            messages: list[dict[str, Any]],
            tool_call_id: str,
            tool_name: str,
            result: str
    ) -> list[dict[str, Any]]:
        """
        Add a tool result to the message list.
        
        Args:
            messages: Current message list.
            tool_call_id: ID of the tool call.
            tool_name: Name of the tool.
            result: Tool execution result.
        
        Returns:
            Updated message list.
        """
        messages.append({
            "role": "tool",
            "tool_call_id": tool_call_id,
            "name": tool_name,
            "content": result
        })
        return messages

    def add_assistant_message(
            self,
            messages: list[dict[str, Any]],
            content: str | None,
            tool_calls: list[dict[str, Any]] | None = None,
            reasoning_content: str | None = None,
    ) -> list[dict[str, Any]]:
        """
        Add an assistant message to the message list.
        
        Args:
            messages: Current message list.
            content: Message content.
            tool_calls: Optional tool calls.
            reasoning_content: Thinking output (Kimi, DeepSeek-R1, etc.).
        
        Returns:
            Updated message list.
        """
        msg: dict[str, Any] = {"role": "assistant", "content": content or ""}

        if tool_calls:
            msg["tool_calls"] = tool_calls

        # Thinking models reject history without this
        if reasoning_content:
            msg["reasoning_content"] = reasoning_content

        messages.append(msg)
        return messages
