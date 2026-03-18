"""RCA 审计日志记录器。

记录 RCA 执行过程中的每个步骤，支持事后审计与回放。
日志以 JSON Lines 格式持久化到磁盘。
"""

from __future__ import annotations

import json
import time
import uuid
from pathlib import Path
from typing import Any

from loguru import logger

from nanobot.utils.helpers import ensure_dir


class AuditLogger:
    """审计日志记录器。

    记录 RCA 执行过程中的每个步骤和安全事件，
    日志以 JSON Lines (.jsonl) 格式持久化到指定目录。
    """

    def __init__(self, log_dir: str | Path = "~/.nanobot/workspace/rca_audit"):
        """初始化审计日志记录器。

        Args:
            log_dir: 审计日志存储目录
        """
        self.log_dir = Path(log_dir).expanduser()
        ensure_dir(self.log_dir)

    def new_session_id(self) -> str:
        """生成新的会话 ID。"""
        return str(uuid.uuid4())[:8]

    def _get_log_path(self, session_id: str) -> Path:
        """获取会话日志文件路径。"""
        return self.log_dir / f"rca_session_{session_id}.jsonl"

    def _write_entry(self, session_id: str, entry: dict[str, Any]) -> None:
        """写入一条日志记录。"""
        log_path = self._get_log_path(session_id)
        try:
            with open(log_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(entry, ensure_ascii=False, default=str) + "\n")
        except Exception as e:
            logger.error(f"[RCA-AUDIT] 写入审计日志失败: {e}")

    def log_step(
        self,
        session_id: str,
        step_id: str,
        step_type: str,
        command: str | None,
        input_data: dict[str, Any],
        output_data: dict[str, Any],
        status: str,
        duration: float,
    ) -> None:
        """记录步骤执行日志。

        Args:
            session_id: 会话 ID
            step_id: 步骤标识
            step_type: 步骤类型
            command: 执行的命令（如有）
            input_data: SLM 推理输入 / 工具参数
            output_data: SLM 推理输出 / 工具返回值
            status: 执行状态 ("success" / "error" / "skipped")
            duration: 执行耗时（秒）
        """
        entry = {
            "timestamp": time.time(),
            "event_type": "step_execution",
            "session_id": session_id,
            "step_id": step_id,
            "step_type": step_type,
            "command": command,
            "input_data": input_data,
            "output_data": output_data,
            "status": status,
            "duration_seconds": round(duration, 3),
        }
        self._write_entry(session_id, entry)
        logger.debug(
            f"[RCA-AUDIT] 步骤 '{step_id}' ({step_type}) "
            f"状态: {status}, 耗时: {duration:.3f}s"
        )

    def log_security_event(
        self,
        session_id: str,
        event_type: str,
        details: dict[str, Any],
    ) -> None:
        """记录安全事件（拒绝、告警等）。

        Args:
            session_id: 会话 ID
            event_type: 事件类型（如 "tool_rejected", "command_blocked"）
            details: 事件详细信息
        """
        entry = {
            "timestamp": time.time(),
            "event_type": f"security_{event_type}",
            "session_id": session_id,
            "details": details,
        }
        self._write_entry(session_id, entry)
        logger.warning(f"[RCA-AUDIT] 安全事件: {event_type} - {details}")

    def log_session_start(
        self,
        session_id: str,
        skill_name: str,
        inputs: dict[str, Any],
    ) -> None:
        """记录会话开始。"""
        entry = {
            "timestamp": time.time(),
            "event_type": "session_start",
            "session_id": session_id,
            "skill_name": skill_name,
            "inputs": inputs,
        }
        self._write_entry(session_id, entry)

    def log_session_end(
        self,
        session_id: str,
        status: str,
        duration: float,
        root_cause: str | None = None,
    ) -> None:
        """记录会话结束。"""
        entry = {
            "timestamp": time.time(),
            "event_type": "session_end",
            "session_id": session_id,
            "status": status,
            "duration_seconds": round(duration, 3),
            "root_cause": root_cause,
        }
        self._write_entry(session_id, entry)

    def get_session_log(self, session_id: str) -> list[dict[str, Any]]:
        """获取完整会话日志，用于审计回放。

        Args:
            session_id: 会话 ID

        Returns:
            日志记录列表
        """
        log_path = self._get_log_path(session_id)
        if not log_path.exists():
            return []

        entries: list[dict[str, Any]] = []
        try:
            with open(log_path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line:
                        entries.append(json.loads(line))
        except Exception as e:
            logger.error(f"[RCA-AUDIT] 读取审计日志失败: {e}")

        return entries
