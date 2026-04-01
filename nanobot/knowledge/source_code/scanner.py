"""源代码目录扫描器。

递归扫描 workspace/src/ 目录，识别领域子目录，提取代码文件元数据。
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from loguru import logger


# 支持的代码文件扩展名
SUPPORTED_EXTENSIONS: set[str] = {
    ".py", ".java", ".go", ".js", ".jsx", ".ts", ".tsx",
    ".c", ".h", ".cpp", ".cc", ".cxx", ".hpp",
    ".sql", ".sh", ".bash",
    ".rb", ".rs", ".php", ".cs",
    ".yaml", ".yml", ".json", ".xml",
    ".conf", ".properties", ".toml", ".ini",
}

# 大文件警告阈值（100KB）
_LARGE_FILE_THRESHOLD = 100 * 1024

# 默认忽略的目录
_IGNORE_DIRS: set[str] = {
    ".git", ".svn", ".hg",
    "node_modules", "__pycache__", ".tox", ".mypy_cache",
    "venv", ".venv", "env",
    ".idea", ".vscode",
    "build", "dist", "target", "out",
    ".gradle", ".mvn",
}


@dataclass
class SourceFile:
    """源代码文件元数据。"""

    file_path: str  # 相对于 workspace/src/ 的路径
    absolute_path: str  # 绝对路径
    filename: str
    domain: str
    language: str  # 根据扩展名推断的语言
    extension: str
    size_bytes: int
    is_large: bool  # 是否超过 100KB

    def read_content(self) -> str:
        """读取文件内容。"""
        try:
            with open(self.absolute_path, "r", encoding="utf-8", errors="replace") as f:
                return f.read()
        except Exception as exc:
            logger.error(f"[Scanner] 读取文件失败 {self.absolute_path}: {exc}")
            return ""


class SourceCodeScanner:
    """源代码目录扫描器。

    扫描 ``workspace/src/`` 下的各领域子目录，收集代码文件元数据。
    """

    def __init__(self, src_dir: Path):
        """初始化扫描器。

        Args:
            src_dir: 源代码根目录，如 ``workspace/src/``
        """
        self._src_dir = src_dir

    # ------------------------------------------------------------------
    # 公共接口
    # ------------------------------------------------------------------

    def list_domains(self) -> list[str]:
        """列出所有领域子目录名称。"""
        if not self._src_dir.exists():
            logger.warning(f"[Scanner] 源代码目录不存在: {self._src_dir}")
            return []

        domains = []
        for item in sorted(self._src_dir.iterdir()):
            if item.is_dir() and item.name not in _IGNORE_DIRS and not item.name.startswith("."):
                domains.append(item.name)
        return domains

    def scan_domain(self, domain: str) -> list[SourceFile]:
        """扫描指定领域的所有代码文件。

        Args:
            domain: 领域名称

        Returns:
            SourceFile 列表
        """
        domain_dir = self._src_dir / domain
        if not domain_dir.exists():
            logger.warning(f"[Scanner] 领域目录不存在: {domain_dir}")
            return []

        files: list[SourceFile] = []
        for root, dirs, filenames in os.walk(domain_dir):
            # 过滤忽略目录
            dirs[:] = [d for d in dirs if d not in _IGNORE_DIRS and not d.startswith(".")]

            for fname in filenames:
                abs_path = os.path.join(root, fname)
                ext = os.path.splitext(fname)[1].lower()

                if ext not in SUPPORTED_EXTENSIONS:
                    continue

                try:
                    size = os.path.getsize(abs_path)
                except OSError:
                    continue

                is_large = size > _LARGE_FILE_THRESHOLD
                if is_large:
                    logger.warning(f"[Scanner] ⚠️ 大文件 ({size / 1024:.1f}KB): {abs_path}")

                rel_path = os.path.relpath(abs_path, self._src_dir)
                language = self._detect_language(ext)

                files.append(SourceFile(
                    file_path=rel_path,
                    absolute_path=abs_path,
                    filename=fname,
                    domain=domain,
                    language=language,
                    extension=ext,
                    size_bytes=size,
                    is_large=is_large,
                ))

        logger.info(f"[Scanner] 领域 '{domain}' 扫描完成: {len(files)} 个文件")
        return files

    def scan_all(self) -> dict[str, list[SourceFile]]:
        """扫描所有领域。

        Returns:
            字典 {领域名: [SourceFile, ...]}
        """
        result: dict[str, list[SourceFile]] = {}
        domains = self.list_domains()

        if not domains:
            logger.info("[Scanner] 未发现任何领域目录")
            return result

        for domain in domains:
            files = self.scan_domain(domain)
            if files:
                result[domain] = files

        total_files = sum(len(v) for v in result.values())
        logger.info(f"[Scanner] 全部扫描完成: {len(result)} 个领域, {total_files} 个文件")
        return result

    def get_domain_file_tree(self, domain: str) -> dict:
        """获取领域的文件目录树结构（JSON 友好格式）。

        Returns:
            嵌套字典表示的目录树
        """
        domain_dir = self._src_dir / domain
        if not domain_dir.exists():
            return {"name": domain, "type": "directory", "children": []}

        return self._build_tree(domain_dir, domain)

    @property
    def src_dir(self) -> Path:
        return self._src_dir

    # ------------------------------------------------------------------
    # 内部方法
    # ------------------------------------------------------------------

    @staticmethod
    def _detect_language(ext: str) -> str:
        """根据扩展名推断编程语言。"""
        lang_map = {
            ".py": "python", ".java": "java", ".go": "go",
            ".js": "javascript", ".jsx": "javascript",
            ".ts": "typescript", ".tsx": "typescript",
            ".c": "c", ".h": "c",
            ".cpp": "cpp", ".cc": "cpp", ".cxx": "cpp", ".hpp": "cpp",
            ".sql": "sql", ".sh": "bash", ".bash": "bash",
            ".rb": "ruby", ".rs": "rust", ".php": "php", ".cs": "csharp",
            ".yaml": "yaml", ".yml": "yaml", ".json": "json", ".xml": "xml",
            ".conf": "config", ".properties": "properties",
            ".toml": "toml", ".ini": "ini",
        }
        return lang_map.get(ext, "unknown")

    def _build_tree(self, dir_path: Path, domain: str) -> dict:
        """递归构建文件目录树。"""
        node: dict = {
            "name": dir_path.name,
            "type": "directory",
            "children": [],
        }

        try:
            items = sorted(dir_path.iterdir(), key=lambda x: (not x.is_dir(), x.name.lower()))
        except PermissionError:
            return node

        for item in items:
            if item.name in _IGNORE_DIRS or item.name.startswith("."):
                continue

            if item.is_dir():
                child = self._build_tree(item, domain)
                node["children"].append(child)
            elif item.is_file():
                ext = item.suffix.lower()
                if ext in SUPPORTED_EXTENSIONS:
                    size = item.stat().st_size
                    node["children"].append({
                        "name": item.name,
                        "type": "file",
                        "size": size,
                        "language": self._detect_language(ext),
                        "extension": ext,
                    })

        return node
