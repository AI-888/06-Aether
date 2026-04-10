from __future__ import annotations
"""本地文件关键字过滤工具。"""

import json
from pathlib import Path
from typing import Any

from nanobot.agent.tools.base import Tool


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

            path_obj = Path(file_path).expanduser().resolve()
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
