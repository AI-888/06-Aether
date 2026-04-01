"""基于 Tree-sitter 的代码智能分块器。

支持多种编程语言的 AST 解析，在函数、类、方法等代码单元边界处进行分块。
不支持的语言回退到按固定行数分块。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from loguru import logger


# 语言文件扩展名映射
EXTENSION_LANGUAGE_MAP: dict[str, str] = {
    ".py": "python",
    ".java": "java",
    ".go": "go",
    ".js": "javascript",
    ".jsx": "javascript",
    ".ts": "typescript",
    ".tsx": "typescript",
    ".c": "c",
    ".h": "c",
    ".cpp": "cpp",
    ".cc": "cpp",
    ".cxx": "cpp",
    ".hpp": "cpp",
    ".sql": "sql",
    ".sh": "bash",
    ".bash": "bash",
    ".rb": "ruby",
    ".rs": "rust",
    ".php": "php",
    ".cs": "c_sharp",
}

# Tree-sitter 中需要提取为独立分块的 AST 节点类型
_INTERESTING_NODE_TYPES: dict[str, set[str]] = {
    "python": {"function_definition", "class_definition", "decorated_definition"},
    "java": {"class_declaration", "method_declaration", "constructor_declaration", "interface_declaration"},
    "go": {"function_declaration", "method_declaration", "type_declaration"},
    "javascript": {"function_declaration", "class_declaration", "method_definition", "arrow_function",
                    "function_expression"},
    "typescript": {"function_declaration", "class_declaration", "method_definition", "arrow_function",
                   "function_signature", "interface_declaration"},
    "c": {"function_definition", "struct_specifier"},
    "cpp": {"function_definition", "class_specifier", "struct_specifier"},
    "sql": {"create_table_statement", "select_statement", "insert_statement"},
    "bash": {"function_definition"},
    "ruby": {"method", "class", "module"},
    "rust": {"function_item", "impl_item", "struct_item", "enum_item"},
    "php": {"function_definition", "class_declaration", "method_declaration"},
    "c_sharp": {"class_declaration", "method_declaration", "struct_declaration"},
}

# 不支持 Tree-sitter 的文件类型（使用回退分块）
_FALLBACK_EXTENSIONS = {".yaml", ".yml", ".json", ".xml", ".conf", ".properties", ".toml", ".ini", ".cfg", ".env",
                        ".md", ".txt", ".csv"}


@dataclass
class CodeChunk:
    """代码分块数据结构。"""

    content: str
    file_path: str
    filename: str
    domain: str
    language: str
    chunk_index: int
    total_chunks: int
    node_type: str = "unknown"
    start_line: int = 0
    end_line: int = 0

    def to_dict(self) -> dict[str, Any]:
        """转换为字典（用于存入向量库）。"""
        return {
            "content": self.content,
            "file_path": self.file_path,
            "filename": self.filename,
            "domain": self.domain,
            "language": self.language,
            "chunk_index": self.chunk_index,
            "total_chunks": self.total_chunks,
            "node_type": self.node_type,
            "start_line": self.start_line,
            "end_line": self.end_line,
        }


class TreeSitterChunker:
    """基于 Tree-sitter 的代码分块器。

    - 支持多种语言的 AST 解析
    - 在函数/类/方法等节点边界处分块
    - 不支持的语言自动回退到行分块
    """

    def __init__(
        self,
        fallback_lines: int = 50,
        fallback_overlap: int = 10,
        max_chunk_lines: int = 200,
    ):
        """初始化分块器。

        Args:
            fallback_lines: 回退分块时每块行数
            fallback_overlap: 回退分块时重叠行数
            max_chunk_lines: 单个分块的最大行数（超过则拆分）
        """
        self._fallback_lines = fallback_lines
        self._fallback_overlap = fallback_overlap
        self._max_chunk_lines = max_chunk_lines
        self._parsers: dict[str, Any] = {}
        self._ts_available = self._check_tree_sitter()

    def _check_tree_sitter(self) -> bool:
        """检查 tree-sitter 是否可用。"""
        try:
            import tree_sitter_languages  # noqa: F401
            logger.info("[TreeSitterChunker] tree-sitter-languages 可用")
            return True
        except ImportError:
            logger.warning("[TreeSitterChunker] tree-sitter-languages 不可用，将使用回退分块")
            return False

    def _get_parser(self, language: str):
        """获取或创建指定语言的 Tree-sitter 解析器。"""
        if language in self._parsers:
            return self._parsers[language]

        if not self._ts_available:
            return None

        try:
            import tree_sitter_languages
            parser = tree_sitter_languages.get_parser(language)
            self._parsers[language] = parser
            return parser
        except Exception as exc:
            logger.warning(f"[TreeSitterChunker] 语言 '{language}' 的解析器不可用: {exc}")
            self._parsers[language] = None
            return None

    # ------------------------------------------------------------------
    # 公共接口
    # ------------------------------------------------------------------

    def chunk_file(
        self,
        content: str,
        file_path: str,
        domain: str,
        language: Optional[str] = None,
    ) -> list[CodeChunk]:
        """对单个文件进行分块。

        Args:
            content: 文件内容
            file_path: 文件路径
            domain: 领域名
            language: 编程语言（为 None 时根据扩展名推断）

        Returns:
            CodeChunk 列表
        """
        if not content.strip():
            return []

        filename = Path(file_path).name
        ext = Path(file_path).suffix.lower()

        # 推断语言
        if language is None:
            language = EXTENSION_LANGUAGE_MAP.get(ext, "")

        # 判断使用 Tree-sitter 还是回退
        if ext in _FALLBACK_EXTENSIONS or not language:
            chunks = self._fallback_chunk(content, file_path, filename, domain, language or ext.lstrip("."))
        else:
            parser = self._get_parser(language)
            if parser is not None:
                chunks = self._tree_sitter_chunk(content, file_path, filename, domain, language, parser)
            else:
                chunks = self._fallback_chunk(content, file_path, filename, domain, language)

        # 设置 total_chunks
        total = len(chunks)
        for i, c in enumerate(chunks):
            c.chunk_index = i
            c.total_chunks = total

        return chunks

    def detect_language(self, file_path: str) -> str:
        """根据文件扩展名检测编程语言。"""
        ext = Path(file_path).suffix.lower()
        return EXTENSION_LANGUAGE_MAP.get(ext, "")

    # ------------------------------------------------------------------
    # Tree-sitter 分块
    # ------------------------------------------------------------------

    def _tree_sitter_chunk(
        self,
        content: str,
        file_path: str,
        filename: str,
        domain: str,
        language: str,
        parser: Any,
    ) -> list[CodeChunk]:
        """使用 Tree-sitter AST 进行分块。"""
        try:
            tree = parser.parse(content.encode("utf-8"))
            root_node = tree.root_node
        except Exception as exc:
            logger.warning(f"[TreeSitterChunker] AST 解析失败 ({file_path}): {exc}")
            return self._fallback_chunk(content, file_path, filename, domain, language)

        interesting_types = _INTERESTING_NODE_TYPES.get(language, set())
        chunks: list[CodeChunk] = []
        lines = content.split("\n")

        # 收集所有感兴趣的节点
        interesting_nodes = self._collect_interesting_nodes(root_node, interesting_types)

        if not interesting_nodes:
            # 没有感兴趣的节点，回退到行分块
            return self._fallback_chunk(content, file_path, filename, domain, language)

        # 处理节点之间的间隙（导入声明、全局变量等）
        covered_lines: set[int] = set()
        node_ranges: list[tuple[int, int, str]] = []

        for node in interesting_nodes:
            start_line = node.start_point[0]
            end_line = node.end_point[0]
            node_type = node.type
            node_ranges.append((start_line, end_line, node_type))
            for ln in range(start_line, end_line + 1):
                covered_lines.add(ln)

        # 先添加文件头部未覆盖的内容（如导入语句）
        header_lines = []
        for i in range(len(lines)):
            if i in covered_lines:
                break
            if lines[i].strip():
                header_lines.append(lines[i])

        if header_lines:
            header_content = "\n".join(header_lines)
            if header_content.strip():
                chunks.append(CodeChunk(
                    content=header_content,
                    file_path=file_path,
                    filename=filename,
                    domain=domain,
                    language=language,
                    chunk_index=0,
                    total_chunks=0,
                    node_type="module_header",
                    start_line=1,
                    end_line=len(header_lines),
                ))

        # 将每个感兴趣的节点作为一个分块
        for start_line, end_line, node_type in node_ranges:
            node_content = "\n".join(lines[start_line:end_line + 1])
            if not node_content.strip():
                continue

            # 如果节点太大，进一步拆分
            node_lines = end_line - start_line + 1
            if node_lines > self._max_chunk_lines:
                sub_chunks = self._split_large_node(
                    node_content, file_path, filename, domain, language, node_type,
                    start_line,
                )
                chunks.extend(sub_chunks)
            else:
                chunks.append(CodeChunk(
                    content=node_content,
                    file_path=file_path,
                    filename=filename,
                    domain=domain,
                    language=language,
                    chunk_index=0,
                    total_chunks=0,
                    node_type=node_type,
                    start_line=start_line + 1,
                    end_line=end_line + 1,
                ))

        return chunks if chunks else self._fallback_chunk(content, file_path, filename, domain, language)

    def _collect_interesting_nodes(self, node, interesting_types: set[str]) -> list:
        """递归收集所有感兴趣的 AST 节点。"""
        result = []
        if node.type in interesting_types:
            result.append(node)
        else:
            for child in node.children:
                result.extend(self._collect_interesting_nodes(child, interesting_types))
        return result

    def _split_large_node(
        self,
        content: str,
        file_path: str,
        filename: str,
        domain: str,
        language: str,
        node_type: str,
        global_start_line: int,
    ) -> list[CodeChunk]:
        """将过大的 AST 节点按行拆分。"""
        lines = content.split("\n")
        chunks: list[CodeChunk] = []
        i = 0
        while i < len(lines):
            end = min(i + self._fallback_lines, len(lines))
            chunk_lines = lines[i:end]
            chunk_content = "\n".join(chunk_lines)
            if chunk_content.strip():
                chunks.append(CodeChunk(
                    content=chunk_content,
                    file_path=file_path,
                    filename=filename,
                    domain=domain,
                    language=language,
                    chunk_index=0,
                    total_chunks=0,
                    node_type=f"{node_type}_part",
                    start_line=global_start_line + i + 1,
                    end_line=global_start_line + end,
                ))
            i = end - self._fallback_overlap if end < len(lines) else end
        return chunks

    # ------------------------------------------------------------------
    # 回退分块（按固定行数）
    # ------------------------------------------------------------------

    def _fallback_chunk(
        self,
        content: str,
        file_path: str,
        filename: str,
        domain: str,
        language: str,
    ) -> list[CodeChunk]:
        """按固定行数分块（默认 50 行，重叠 10 行）。"""
        lines = content.split("\n")
        if not lines:
            return []

        chunks: list[CodeChunk] = []
        i = 0
        while i < len(lines):
            end = min(i + self._fallback_lines, len(lines))
            chunk_lines = lines[i:end]
            chunk_content = "\n".join(chunk_lines)
            if chunk_content.strip():
                chunks.append(CodeChunk(
                    content=chunk_content,
                    file_path=file_path,
                    filename=filename,
                    domain=domain,
                    language=language,
                    chunk_index=0,
                    total_chunks=0,
                    node_type="line_block",
                    start_line=i + 1,
                    end_line=end,
                ))
            # 滑动窗口
            if end >= len(lines):
                break
            i = end - self._fallback_overlap
        return chunks
