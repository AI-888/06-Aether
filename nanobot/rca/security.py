"""RCA 安全校验层。

对所有由 SLM 生成或 Skill 定义的工具调用和命令进行安全校验。
"""

from __future__ import annotations

import re
from typing import Any

from loguru import logger


class SecurityViolationError(Exception):
    """安全策略违规异常。"""

    def __init__(self, tool_name: str, reason: str):
        self.tool_name = tool_name
        self.reason = reason
        super().__init__(f"安全校验拒绝: 工具 '{tool_name}' - {reason}")


class SecurityGuard:
    """安全校验层。

    对所有由 SLM 生成或 Skill 定义的命令进行白名单校验，
    防止危险操作的执行。
    """

    # 默认工具白名单
    DEFAULT_WHITELIST: set[str] = {
        "check_disk_usage",
        "check_memory",
        "check_cpu",
        "kubectl_get_pods",
        "kubectl_query_log",
        "knowledge_search",
        "exec",
        "mcp_tool",
    }

    # 危险命令黑名单正则模式
    BLACKLIST_PATTERNS: list[str] = [
        r"rm\s+(-rf?|--recursive)",
        r"shutdown",
        r"reboot",
        r"mkfs",
        r"dd\s+if=",
        r":\(\)\{",           # fork bomb
        r"chmod\s+777",
        r"chown\s+root",
        r">\s*/dev/sd",       # 直写磁盘
        r"curl.*\|\s*sh",     # pipe to shell
        r"wget.*\|\s*sh",
    ]

    def __init__(self, extra_whitelist: list[str] | None = None):
        """初始化安全校验层。

        Args:
            extra_whitelist: 额外的工具白名单，来自 RCAConfig.security_whitelist
        """
        self._whitelist = set(self.DEFAULT_WHITELIST)
        if extra_whitelist:
            self._whitelist.update(extra_whitelist)

        # 预编译黑名单正则
        self._blacklist_compiled = [
            re.compile(pattern, re.IGNORECASE)
            for pattern in self.BLACKLIST_PATTERNS
        ]

    @property
    def whitelist(self) -> set[str]:
        """当前白名单集合。"""
        return set(self._whitelist)

    def validate_tool_call(
        self,
        tool_name: str,
        params: dict[str, Any] | None = None,
    ) -> None:
        """校验工具调用是否安全。

        Args:
            tool_name: 工具名称
            params: 工具参数

        Raises:
            SecurityViolationError: 不安全的工具调用
        """
        if tool_name not in self._whitelist:
            logger.warning(
                f"[RCA-SECURITY] 工具 '{tool_name}' 不在白名单中，拒绝执行"
            )
            raise SecurityViolationError(
                tool_name,
                f"工具不在白名单中，允许的工具: {sorted(self._whitelist)}",
            )

        # 如果参数中包含命令字符串，进一步校验
        if params:
            for key, value in params.items():
                if isinstance(value, str):
                    self.validate_command(value, tool_name=tool_name)

    def validate_command(
        self,
        command: str,
        tool_name: str = "unknown",
    ) -> None:
        """校验 shell 命令是否安全。

        Args:
            command: 要执行的 shell 命令
            tool_name: 发起命令的工具名称（用于日志）

        Raises:
            SecurityViolationError: 包含危险命令
        """
        for pattern in self._blacklist_compiled:
            if pattern.search(command):
                logger.warning(
                    f"[RCA-SECURITY] 命令包含危险模式 '{pattern.pattern}': "
                    f"{command[:100]}..."
                )
                raise SecurityViolationError(
                    tool_name,
                    f"命令包含危险操作模式: {pattern.pattern}",
                )

    def add_to_whitelist(self, tool_name: str) -> None:
        """动态添加工具到白名单。"""
        self._whitelist.add(tool_name)
        logger.info(f"[RCA-SECURITY] 工具 '{tool_name}' 已添加到白名单")

    def remove_from_whitelist(self, tool_name: str) -> None:
        """从白名单中移除工具。"""
        self._whitelist.discard(tool_name)
        logger.info(f"[RCA-SECURITY] 工具 '{tool_name}' 已从白名单移除")
