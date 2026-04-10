from __future__ import annotations
"""File system tools: read, write, edit."""

import json
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
    """支持多组 [文件路径, 关键字列表] 的文件关键字过滤工具。"""

    @property
    def name(self) -> str:
        return "file_keyword_filter"

    @property
    def description(self) -> str:
        return (
            "按输入的多组[文件路径,关键字列表]过滤文件内容，返回 key=value 结构："
            "key 为文件路径，value 为该文件下各关键字的匹配结果（含行号与内容）。"
        )

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "groups": {
                    "type": "array",
                    "description": "多组过滤条件，每组包含 file_path 与 keywords",
                    "items": {
                        "type": "object",
                        "properties": {
                            "file_path": {
                                "type": "string",
                                "description": "待过滤的本地文件路径",
                            },
                            "keywords": {
                                "type": "array",
                                "description": "关键字列表，至少 1 个",
                                "items": {"type": "string"},
                                "minItems": 1,
                            },
                        },
                        "required": ["file_path", "keywords"],
                    },
                    "minItems": 1,
                },
                "case_sensitive": {
                    "type": "boolean",
                    "description": "是否区分大小写，默认 false",
                    "default": False,
                },
                "max_matches_per_keyword": {
                    "type": "integer",
                    "description": "每个关键字最多返回匹配条数，默认 50",
                    "default": 50,
                    "minimum": 1,
                    "maximum": 1000,
                },
            },
            "required": ["groups"],
        }

    async def execute(
        self,
        groups: list[dict[str, Any]],
        case_sensitive: bool = False,
        max_matches_per_keyword: int = 50,
        **kwargs: Any,
    ) -> dict[str, Any]:
        result_map: dict[str, Any] = {}

        for group in groups:
            file_path = str(group.get("file_path", "")).strip()
            keywords_raw = group.get("keywords", [])
            keywords = [str(k).strip() for k in keywords_raw if str(k).strip()]

            if not file_path:
                continue

            path_obj = _resolve_path(file_path)
            key = str(path_obj)

            if not path_obj.exists():
                result_map[key] = {"error": f"文件不存在: {file_path}"}
                continue

            if not path_obj.is_file():
                result_map[key] = {"error": f"不是文件: {file_path}"}
                continue

            try:
                content = path_obj.read_text(encoding="utf-8", errors="replace")
            except Exception as e:
                result_map[key] = {"error": f"读取文件失败: {str(e)}"}
                continue

            lines = content.splitlines()
            file_result: dict[str, Any] = {}

            for keyword in keywords:
                matched: list[dict[str, Any]] = []
                target = keyword if case_sensitive else keyword.lower()

                for idx, line in enumerate(lines, start=1):
                    source = line if case_sensitive else line.lower()
                    if target in source:
                        matched.append({
                            "line_number": idx,
                            "line": line,
                        })
                        if len(matched) >= max_matches_per_keyword:
                            break

                file_result[keyword] = matched

            result_map[key] = file_result

        return {
            "result": json.dumps(result_map, ensure_ascii=False),
            "commands": [],
            "data": result_map,
        }