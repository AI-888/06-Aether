"""源代码 RAG 初始化状态管理。

独立于现有知识库的 init_status.json，使用 source_code_init_status.json 文件
管理各领域源代码的初始化状态。
"""

from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Optional

from loguru import logger

# 默认状态文件名
_STATUS_FILENAME = "source_code_init_status.json"


@dataclass
class DomainStatus:
    """单个领域的初始化状态记录。"""

    domain: str
    initialized: bool = False
    initialized_at: Optional[str] = None
    file_count: int = 0
    chunk_count: int = 0
    source_type: str = "local"  # "local" 或 "git"
    git_commit_hash: Optional[str] = None
    git_repo_url: Optional[str] = None
    error: Optional[str] = None


@dataclass
class SourceCodeInitStatusData:
    """完整的初始化状态数据。"""

    version: str = "1.0"
    domains: dict[str, DomainStatus] = field(default_factory=dict)


class SourceCodeInitStatus:
    """源代码 RAG 初始化状态管理器。

    负责读写 ``source_code_init_status.json``，跟踪各领域的索引状态。
    - 与现有 ``init_status.json`` 完全隔离
    - 状态文件损坏时自动重置（将所有领域视为未初始化）
    """

    def __init__(self, knowledge_dir: Path):
        """初始化状态管理器。

        Args:
            knowledge_dir: 知识库根目录，如 ``workspace/knowledge/``
        """
        self._knowledge_dir = knowledge_dir
        self._status_file = knowledge_dir / _STATUS_FILENAME
        self._data: SourceCodeInitStatusData = self._load()

    # ------------------------------------------------------------------
    # 公共接口
    # ------------------------------------------------------------------

    def is_initialized(self, domain: str) -> bool:
        """检查指定领域是否已初始化。"""
        ds = self._data.domains.get(domain)
        return ds.initialized if ds else False

    def get_domain_status(self, domain: str) -> Optional[DomainStatus]:
        """获取指定领域的状态。"""
        return self._data.domains.get(domain)

    def get_all_domains(self) -> dict[str, DomainStatus]:
        """获取所有领域的状态。"""
        return dict(self._data.domains)

    def mark_initialized(
        self,
        domain: str,
        file_count: int,
        chunk_count: int,
        source_type: str = "local",
        git_commit_hash: Optional[str] = None,
        git_repo_url: Optional[str] = None,
    ) -> None:
        """将领域标记为已初始化并持久化。"""
        self._data.domains[domain] = DomainStatus(
            domain=domain,
            initialized=True,
            initialized_at=time.strftime("%Y-%m-%dT%H:%M:%S"),
            file_count=file_count,
            chunk_count=chunk_count,
            source_type=source_type,
            git_commit_hash=git_commit_hash,
            git_repo_url=git_repo_url,
        )
        self._save()
        logger.info(f"[SourceCodeRAG] ✅ 领域 '{domain}' 标记为已初始化 "
                     f"(files={file_count}, chunks={chunk_count})")

    def mark_error(self, domain: str, error_msg: str) -> None:
        """将领域标记为初始化错误。"""
        existing = self._data.domains.get(domain)
        if existing:
            existing.error = error_msg
            existing.initialized = False
        else:
            self._data.domains[domain] = DomainStatus(
                domain=domain,
                initialized=False,
                error=error_msg,
            )
        self._save()
        logger.error(f"[SourceCodeRAG] ❌ 领域 '{domain}' 初始化失败: {error_msg}")

    def clear_domain(self, domain: str) -> None:
        """清除指定领域的状态（用于重新初始化）。"""
        if domain in self._data.domains:
            del self._data.domains[domain]
            self._save()
            logger.info(f"[SourceCodeRAG] 🗑️ 领域 '{domain}' 状态已清除")

    def clear_all(self) -> None:
        """清除所有领域的状态。"""
        self._data.domains.clear()
        self._save()
        logger.info("[SourceCodeRAG] 🗑️ 所有领域状态已清除")

    def get_git_commit_hash(self, domain: str) -> Optional[str]:
        """获取指定领域上次记录的 Git commit hash。"""
        ds = self._data.domains.get(domain)
        return ds.git_commit_hash if ds else None

    def to_dict(self) -> dict:
        """序列化为字典。"""
        result = {"version": self._data.version, "domains": {}}
        for name, ds in self._data.domains.items():
            result["domains"][name] = asdict(ds)
        return result

    # ------------------------------------------------------------------
    # 内部方法
    # ------------------------------------------------------------------

    def _load(self) -> SourceCodeInitStatusData:
        """从文件加载状态；文件不存在或损坏时返回空状态。"""
        if not self._status_file.exists():
            logger.info(f"[SourceCodeRAG] 状态文件不存在，使用空状态: {self._status_file}")
            return SourceCodeInitStatusData()

        try:
            raw = json.loads(self._status_file.read_text(encoding="utf-8"))
            data = SourceCodeInitStatusData(version=raw.get("version", "1.0"))
            for name, d in raw.get("domains", {}).items():
                data.domains[name] = DomainStatus(
                    domain=d.get("domain", name),
                    initialized=d.get("initialized", False),
                    initialized_at=d.get("initialized_at"),
                    file_count=d.get("file_count", 0),
                    chunk_count=d.get("chunk_count", 0),
                    source_type=d.get("source_type", "local"),
                    git_commit_hash=d.get("git_commit_hash"),
                    git_repo_url=d.get("git_repo_url"),
                    error=d.get("error"),
                )
            logger.info(f"[SourceCodeRAG] 已加载状态文件，共 {len(data.domains)} 个领域")
            return data
        except Exception as exc:
            logger.warning(f"[SourceCodeRAG] 状态文件损坏或不可读，将重置: {exc}")
            return SourceCodeInitStatusData()

    def _save(self) -> None:
        """将状态持久化到文件。"""
        try:
            self._knowledge_dir.mkdir(parents=True, exist_ok=True)
            payload = self.to_dict()
            self._status_file.write_text(
                json.dumps(payload, indent=2, ensure_ascii=False),
                encoding="utf-8",
            )
        except Exception as exc:
            logger.error(f"[SourceCodeRAG] 状态文件写入失败: {exc}")
