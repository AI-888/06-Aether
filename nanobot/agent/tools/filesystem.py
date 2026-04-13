from __future__ import annotations
"""File system tools: read, write, edit."""

import json
import shlex
import subprocess
from pathlib import Path
from typing import Any

from nanobot.agent.tools.base import Tool


def _resolve_path(path: str, allowed_dir: Path | None = None) -> Path:
    """Resolve path and optionally enforce directory restriction."""
    resolved = Path(path).expanduser().resolve()
    if allowed_dir and not str(resolved).startswith(str(allowed_dir.resolve())):
        raise PermissionError(f"Path {path} is outside allowed directory {allowed_dir}")
    return resolved


class ReadFileTool(Tool):
    """Tool to read file contents."""

    def __init__(self, allowed_dir: Path | None = None):
        self._allowed_dir = allowed_dir

    @property
    def name(self) -> str:
        return "read_file"

    @property
    def description(self) -> str:
        return "Read the contents of a file at the given path."

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "The file path to read"
                }
            },
            "required": ["path"]
        }

    async def execute(self, path: str, **kwargs: Any) -> str:
        try:
            file_path = _resolve_path(path, self._allowed_dir)
            if not file_path.exists():
                return f"Error: File not found: {path}"
            if not file_path.is_file():
                return f"Error: Not a file: {path}"

            content = file_path.read_text(encoding="utf-8")
            return content
        except PermissionError as e:
            return f"Error: {e}"
        except Exception as e:
            return f"Error reading file: {str(e)}"


class WriteFileTool(Tool):
    """Tool to write content to a file."""

    def __init__(self, allowed_dir: Path | None = None):
        self._allowed_dir = allowed_dir

    @property
    def name(self) -> str:
        return "write_file"

    @property
    def description(self) -> str:
        return "Write content to a file at the given path. Creates parent directories if needed."

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "The file path to write to"
                },
                "content": {
                    "type": "string",
                    "description": "The content to write"
                }
            },
            "required": ["path", "content"]
        }

    async def execute(self, path: str, content: str, **kwargs: Any) -> str:
        try:
            file_path = _resolve_path(path, self._allowed_dir)
            file_path.parent.mkdir(parents=True, exist_ok=True)
            file_path.write_text(content, encoding="utf-8")
            return f"Successfully wrote {len(content)} bytes to {path}"
        except PermissionError as e:
            return f"Error: {e}"
        except Exception as e:
            return f"Error writing file: {str(e)}"


class EditFileTool(Tool):
    """Tool to edit a file by replacing text."""

    def __init__(self, allowed_dir: Path | None = None):
        self._allowed_dir = allowed_dir

    @property
    def name(self) -> str:
        return "edit_file"

    @property
    def description(self) -> str:
        return "Edit a file by replacing old_text with new_text. The old_text must exist exactly in the file."

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "The file path to edit"
                },
                "old_text": {
                    "type": "string",
                    "description": "The exact text to find and replace"
                },
                "new_text": {
                    "type": "string",
                    "description": "The text to replace with"
                }
            },
            "required": ["path", "old_text", "new_text"]
        }

    async def execute(self, path: str, old_text: str, new_text: str, **kwargs: Any) -> str:
        try:
            file_path = _resolve_path(path, self._allowed_dir)
            if not file_path.exists():
                return f"Error: File not found: {path}"

            content = file_path.read_text(encoding="utf-8")

            if old_text not in content:
                return f"Error: old_text not found in file. Make sure it matches exactly."

            # Count occurrences
            count = content.count(old_text)
            if count > 1:
                return f"Warning: old_text appears {count} times. Please provide more context to make it unique."

            new_content = content.replace(old_text, new_text, 1)
            file_path.write_text(new_content, encoding="utf-8")

            return f"Successfully edited {path}"
        except PermissionError as e:
            return f"Error: {e}"
        except Exception as e:
            return f"Error editing file: {str(e)}"


class ListDirTool(Tool):
    """Tool to list directory contents."""

    def __init__(self, allowed_dir: Path | None = None):
        self._allowed_dir = allowed_dir

    @property
    def name(self) -> str:
        return "list_dir"

    @property
    def description(self) -> str:
        return "List the contents of a directory."

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "The directory path to list"
                }
            },
            "required": ["path"]
        }

    async def execute(self, path: str, **kwargs: Any) -> str:
        try:
            dir_path = _resolve_path(path, self._allowed_dir)
            if not dir_path.exists():
                return f"Error: Directory not found: {path}"
            if not dir_path.is_dir():
                return f"Error: Not a directory: {path}"

            items = []
            for item in sorted(dir_path.iterdir()):
                prefix = "📁 " if item.is_dir() else "📄 "
                items.append(f"{prefix}{item.name}")

            if not items:
                return f"Directory {path} is empty"

            return "\n".join(items)
        except PermissionError as e:
            return f"Error: {e}"
        except Exception as e:
            return f"Error listing directory: {str(e)}"


class FileKeywordFilterTool(Tool):
    """基于 grep 的多文件统一关键字过滤工具。"""

    def __init__(self, allowed_dir: Path | None = None):
        self._allowed_dir = allowed_dir

    @property
    def name(self) -> str:
        return "file_keyword_filter"

    @property
    def description(self) -> str:
        return (
            "使用 grep 对多个文件执行关键字过滤。"
            "所有文件共享同一组 keywords，且采用 AND 规则：同一文件需同时命中全部关键字才视为匹配成功。"
            "输出参数说明：commands 为本次执行的 grep 命令列表；data 为结构化匹配结果。"
        )

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "file_paths": {
                    "type": "array",
                    "description": "待过滤的本地文件路径。支持 string（单文件）或 array[string]（多文件）。",
                    "items": {"type": "string"},
                    "minItems": 1,
                },
                "keywords": {
                    "type": "array",
                    "description": "关键字列表（必填，至少 1 个）。所有文件都使用该列表进行过滤。",
                    "items": {"type": "string"},
                    "minItems": 1,
                },
                "case_sensitive": {
                    "type": "boolean",
                    "description": "是否区分大小写（可选，默认 false）。",
                    "default": False,
                },
                "max_latest_lines": {
                    "type": "integer",
                    "description": "全部关键字返回最多的匹配行数（可选，默认 50，范围 1~1000）。",
                    "default": 50,
                    "minimum": 1,
                    "maximum": 1000,
                },
                "after_context_lines": {
                    "type": "integer",
                    "description": "grep -A 后面的输出行数（可选，默认 0，范围 0~200）。",
                    "default": 6,
                    "minimum": 0,
                    "maximum": 200,
                },
            },
            "required": ["keywords"],
        }

    async def execute(
        self,
        keywords: list[str],
        file_paths: list[str] | None = None,
        case_sensitive: bool = False,
        max_latest_lines: int = 50,
        after_context_lines: int = 6,
        **kwargs: Any,
    ) -> dict[str, Any]:
        result_map: dict[str, Any] = {}
        commands: list[str] = []

        normalized_keywords = [str(k).strip() for k in (keywords or []) if str(k).strip()]
        if not normalized_keywords:
            return {
                "result": "keywords 不能为空",
                "commands": [],
                "data": {"error": "keywords 不能为空"},
            }

        all_file_paths: list[str] = []
        if isinstance(file_paths, list):
            all_file_paths.extend(str(p).strip() for p in file_paths if str(p).strip())

        all_file_paths = list(dict.fromkeys(all_file_paths))

        if not all_file_paths:
            return {
                "result": "至少需要提供一个文件路径",
                "commands": [],
                "data": {"error": "至少需要提供一个文件路径"},
            }

        after_context_lines = max(0, int(after_context_lines))

        grep_base_cmd = ["grep", "-n", "-F", "-m", str(max_latest_lines), "-A", str(after_context_lines)]
        if not case_sensitive:
            grep_base_cmd.append("-i")

        keyword_cmd_prefixes = [
            (keyword, [*grep_base_cmd, keyword])
            for keyword in normalized_keywords
        ]

        for raw_path in all_file_paths:

            try:
                path_obj = _resolve_path(raw_path, self._allowed_dir)
            except Exception as e:
                result_map[raw_path] = {"error": f"路径解析失败: {str(e)}"}
                continue

            key = str(path_obj)
            if not path_obj.exists():
                result_map[key] = {"error": f"文件不存在: {raw_path}"}
                continue
            if not path_obj.is_file():
                result_map[key] = {"error": f"不是文件: {raw_path}"}
                continue

            file_result: dict[str, Any] = {}
            missing_keywords: list[str] = []
            has_runtime_error = False

            for keyword, cmd_prefix in keyword_cmd_prefixes:
                cmd = [*cmd_prefix, key]
                commands.append(shlex.join(cmd))

                try:
                    proc = subprocess.run(
                        cmd,
                        capture_output=True,
                        text=True,
                        check=False,
                    )
                except Exception as e:
                    file_result[keyword] = [{"error": f"grep 执行失败: {str(e)}"}]
                    has_runtime_error = True
                    continue

                if proc.returncode not in (0, 1):
                    err_msg = (proc.stderr or "").strip() or f"grep 异常退出码: {proc.returncode}"
                    file_result[keyword] = [{"error": err_msg}]
                    has_runtime_error = True
                    continue

                matched: list[dict[str, Any]] = []
                for line in (proc.stdout or "").splitlines():
                    if not line or line == "--":
                        continue

                    # grep -n 输出匹配行为: 行号:内容；grep -A 上下文行为: 行号-内容
                    sep = ":" if ":" in line else "-"
                    parts = line.split(sep, 1)
                    if len(parts) != 2:
                        continue

                    line_no_raw, line_text = parts
                    try:
                        line_no = int(line_no_raw)
                    except ValueError:
                        continue

                    matched.append({
                        "line_number": line_no,
                        "line": line_text,
                        "is_match": sep == ":",
                    })

                if not matched:
                    missing_keywords.append(keyword)
                file_result[keyword] = matched

            if has_runtime_error:
                result_map[key] = {
                    "matched_all_keywords": False,
                    "error": "部分关键字执行失败，请查看各关键字结果",
                    "matches_by_keyword": file_result,
                }
            elif missing_keywords:
                result_map[key] = {
                    "matched_all_keywords": False,
                    "missing_keywords": missing_keywords,
                    "matches_by_keyword": file_result,
                }
            else:
                result_map[key] = {
                    "matched_all_keywords": True,
                    "matches_by_keyword": file_result,
                }

        return {
            "raw_data_str": json.dumps(result_map),
            "commands": commands,
            "data": result_map,
        }
