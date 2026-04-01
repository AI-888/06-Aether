"""源代码索引初始化流程编排。

编排完整初始化流程：Git 拉取 → 目录扫描 → Tree-sitter 分块 → CodeBERT 向量化 → ChromaDB 存储。
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Optional

from loguru import logger

from nanobot.knowledge.source_code.chunker import TreeSitterChunker
from nanobot.knowledge.source_code.git_manager import GitManager
from nanobot.knowledge.source_code.init_status import SourceCodeInitStatus
from nanobot.knowledge.source_code.scanner import SourceCodeScanner
from nanobot.knowledge.source_code.store import SourceCodeRAGStore


@dataclass
class IndexResult:
    """单个领域的索引结果。"""

    domain: str
    success: bool
    file_count: int = 0
    chunk_count: int = 0
    failed_files: int = 0
    duration_seconds: float = 0.0
    error: Optional[str] = None
    skipped: bool = False
    message: str = ""


# 进度回调类型：(domain, stage, current, total, current_file, message)
ProgressCallback = Callable[[str, str, int, int, str, str], None]


class SourceCodeIndexer:
    """源代码索引器。

    编排完整的初始化流程：
    1. Git 拉取（如有配置）
    2. 目录扫描
    3. Tree-sitter 分块
    4. CodeBERT 向量化
    5. ChromaDB 存储
    6. 状态更新
    """

    def __init__(
        self,
        workspace: Path,
        model_name: str = "microsoft/codebert-base",
    ):
        """初始化索引器。

        Args:
            workspace: 工作空间根目录
            model_name: CodeBERT 模型名称
        """
        self._workspace = workspace
        self._src_dir = workspace / "src"
        self._knowledge_dir = workspace / "knowledge"

        # 初始化各组件
        self._scanner = SourceCodeScanner(self._src_dir)
        self._chunker = TreeSitterChunker()
        self._git_manager = GitManager(self._src_dir)
        self._init_status = SourceCodeInitStatus(self._knowledge_dir)
        self._store = None
        self._model_name = model_name

        self._progress_callback: Optional[ProgressCallback] = None

    def set_progress_callback(self, callback: ProgressCallback) -> None:
        """设置进度回调函数。"""
        self._progress_callback = callback

    def _notify_progress(
        self,
        domain: str,
        stage: str,
        current: int,
        total: int,
        current_file: str = "",
        message: str = "",
    ) -> None:
        """发送进度通知。"""
        if self._progress_callback:
            try:
                self._progress_callback(domain, stage, current, total, current_file, message)
            except Exception as exc:
                logger.debug(f"[Indexer] 进度回调异常: {exc}")

    def _ensure_store(self) -> SourceCodeRAGStore:
        """确保 Store 已初始化（延迟加载）。"""
        if self._store is None:
            self._store = SourceCodeRAGStore(
                knowledge_dir=self._knowledge_dir,
                model_name=self._model_name,
            )
        return self._store

    # ------------------------------------------------------------------
    # 公共接口
    # ------------------------------------------------------------------

    def initialize_all(self, force: bool = False) -> list[IndexResult]:
        """初始化所有领域。

        Args:
            force: 是否强制重新初始化（忽略已初始化状态）

        Returns:
            IndexResult 列表
        """
        results: list[IndexResult] = []

        # 1. Git 拉取
        logger.info("[Indexer] === 阶段 1: Git 拉取 ===")
        git_results = self._git_manager.sync_all()
        git_commit_map: dict[str, Optional[str]] = {}
        git_url_map: dict[str, Optional[str]] = {}
        for gr in git_results:
            git_commit_map[gr.domain] = gr.commit_hash
            if gr.action in ("clone", "pull"):
                git_url_map[gr.domain] = self._git_manager.get_remote_url(gr.domain)

        # 2. 扫描领域
        logger.info("[Indexer] === 阶段 2: 目录扫描 ===")
        if not self._src_dir.exists():
            logger.warning(f"[Indexer] 源代码目录不存在: {self._src_dir}")
            return results

        domains = self._scanner.list_domains()
        if not domains:
            logger.info("[Indexer] 未发现任何领域目录")
            return results

        # 3. 逐领域索引
        for domain in domains:
            result = self.initialize_domain(
                domain,
                force=force,
                git_commit_hash=git_commit_map.get(domain),
                git_repo_url=git_url_map.get(domain),
            )
            results.append(result)

        # 汇总
        success_count = sum(1 for r in results if r.success)
        skip_count = sum(1 for r in results if r.skipped)
        fail_count = sum(1 for r in results if not r.success and not r.skipped)
        logger.info(f"[Indexer] 全部完成: 成功={success_count}, 跳过={skip_count}, 失败={fail_count}")

        return results

    def initialize_domain(
        self,
        domain: str,
        force: bool = False,
        git_commit_hash: Optional[str] = None,
        git_repo_url: Optional[str] = None,
    ) -> IndexResult:
        """初始化单个领域。

        Args:
            domain: 领域名称
            force: 是否强制重新初始化
            git_commit_hash: 当前 Git commit hash
            git_repo_url: Git 仓库 URL

        Returns:
            IndexResult
        """
        start_time = time.time()

        # 检查是否需要重新索引
        if not force and self._init_status.is_initialized(domain):
            # 检查 Git 领域是否有新提交
            old_hash = self._init_status.get_git_commit_hash(domain)
            if git_commit_hash and old_hash and git_commit_hash == old_hash:
                logger.info(f"[Indexer] 领域 '{domain}' 已初始化且无新提交，跳过")
                return IndexResult(
                    domain=domain,
                    success=True,
                    skipped=True,
                    message="已初始化，无需更新",
                )
            elif not git_commit_hash and old_hash is None:
                # 本地领域，已初始化
                logger.info(f"[Indexer] 领域 '{domain}' 已初始化（本地），跳过")
                return IndexResult(
                    domain=domain,
                    success=True,
                    skipped=True,
                    message="已初始化，无需更新",
                )
            elif git_commit_hash and old_hash and git_commit_hash != old_hash:
                logger.info(f"[Indexer] 领域 '{domain}' 有新提交 ({old_hash[:8]} → {git_commit_hash[:8]})，重新索引")
                force = True

        # 强制重新初始化时先清理
        if force:
            logger.info(f"[Indexer] 清理领域 '{domain}' 的现有数据")
            self._init_status.clear_domain(domain)
            store = self._ensure_store()
            store.delete_collection(domain)

        self._notify_progress(domain, "scanning", 0, 0, "", "正在扫描文件...")

        try:
            # 扫描文件
            files = self._scanner.scan_domain(domain)
            if not files:
                logger.warning(f"[Indexer] 领域 '{domain}' 无代码文件")
                self._init_status.mark_initialized(
                    domain=domain,
                    file_count=0,
                    chunk_count=0,
                    source_type="git" if self._git_manager.is_git_repo(domain) else "local",
                    git_commit_hash=git_commit_hash,
                    git_repo_url=git_repo_url,
                )
                return IndexResult(
                    domain=domain,
                    success=True,
                    file_count=0,
                    chunk_count=0,
                    duration_seconds=time.time() - start_time,
                    message="无代码文件",
                )

            total_files = len(files)
            total_chunks = 0
            failed_files = 0
            all_chunks = []

            # 分块
            self._notify_progress(domain, "chunking", 0, total_files, "", "正在分块...")
            for idx, source_file in enumerate(files):
                try:
                    content = source_file.read_content()
                    if not content.strip():
                        continue

                    chunks = self._chunker.chunk_file(
                        content=content,
                        file_path=source_file.file_path,
                        domain=domain,
                        language=source_file.language,
                    )

                    for chunk in chunks:
                        all_chunks.append(chunk.to_dict())

                    self._notify_progress(
                        domain, "chunking", idx + 1, total_files,
                        source_file.filename,
                        f"正在分块: {source_file.filename}",
                    )
                except Exception as exc:
                    failed_files += 1
                    logger.error(f"[Indexer] 分块失败 ({source_file.file_path}): {exc}")

            # 向量化 & 存储
            if all_chunks:
                self._notify_progress(domain, "indexing", 0, len(all_chunks), "", "正在向量化与索引...")
                store = self._ensure_store()
                if not store.is_ready:
                    raise RuntimeError("SourceCodeRAGStore 未就绪（模型或数据库不可用）")

                # 分批添加
                batch_size = 64
                stored = 0
                for i in range(0, len(all_chunks), batch_size):
                    batch = all_chunks[i:i + batch_size]
                    count = store.add_chunks(domain, batch)
                    stored += count
                    self._notify_progress(
                        domain, "indexing", min(i + batch_size, len(all_chunks)),
                        len(all_chunks), "",
                        f"正在索引: {stored}/{len(all_chunks)} 分块",
                    )

                total_chunks = stored
                store.persist()

            # 更新状态
            source_type = "git" if self._git_manager.is_git_repo(domain) else "local"
            self._init_status.mark_initialized(
                domain=domain,
                file_count=total_files,
                chunk_count=total_chunks,
                source_type=source_type,
                git_commit_hash=git_commit_hash or self._git_manager.get_commit_hash(domain),
                git_repo_url=git_repo_url or self._git_manager.get_remote_url(domain),
            )

            duration = time.time() - start_time
            self._notify_progress(domain, "complete", total_files, total_files, "", "索引完成")

            logger.info(f"[Indexer] ✅ 领域 '{domain}' 索引完成: "
                        f"files={total_files}, chunks={total_chunks}, "
                        f"failed={failed_files}, duration={duration:.1f}s")

            return IndexResult(
                domain=domain,
                success=True,
                file_count=total_files,
                chunk_count=total_chunks,
                failed_files=failed_files,
                duration_seconds=duration,
                message="索引完成",
            )

        except Exception as exc:
            duration = time.time() - start_time
            error_msg = str(exc)
            self._init_status.mark_error(domain, error_msg)
            self._notify_progress(domain, "error", 0, 0, "", f"索引失败: {error_msg}")
            logger.error(f"[Indexer] ❌ 领域 '{domain}' 索引失败: {error_msg}")

            return IndexResult(
                domain=domain,
                success=False,
                duration_seconds=duration,
                error=error_msg,
            )

    def reinitialize_domain(self, domain: str) -> IndexResult:
        """强制重新初始化单个领域。"""
        return self.initialize_domain(domain, force=True)

    def reinitialize_all(self) -> list[IndexResult]:
        """强制重新初始化所有领域。"""
        return self.initialize_all(force=True)

    def delete_domain_index(self, domain: str) -> bool:
        """删除指定领域的向量索引（保留源码文件）。"""
        try:
            store = self._ensure_store()
            store.delete_collection(domain)
            self._init_status.clear_domain(domain)
            logger.info(f"[Indexer] 已删除领域 '{domain}' 的索引")
            return True
        except Exception as exc:
            logger.error(f"[Indexer] 删除领域索引失败: {exc}")
            return False

    # ------------------------------------------------------------------
    # 状态查询
    # ------------------------------------------------------------------

    @property
    def init_status(self) -> SourceCodeInitStatus:
        return self._init_status

    @property
    def scanner(self) -> SourceCodeScanner:
        return self._scanner

    @property
    def git_manager(self) -> GitManager:
        return self._git_manager

    @property
    def store(self) -> SourceCodeRAGStore:
        return self._ensure_store()

    def get_domain_info(self, domain: str) -> dict[str, Any]:
        """获取领域的完整信息。"""
        status = self._init_status.get_domain_status(domain)
        store = self._ensure_store()
        stats = store.get_domain_stats(domain)
        is_git = self._git_manager.is_git_repo(domain)

        return {
            "domain": domain,
            "initialized": status.initialized if status else False,
            "initialized_at": status.initialized_at if status else None,
            "file_count": status.file_count if status else 0,
            "chunk_count": stats.get("chunk_count", 0),
            "source_type": "git" if is_git else "local",
            "git_commit_hash": status.git_commit_hash if status else None,
            "git_repo_url": status.git_repo_url if status else (
                self._git_manager.get_remote_url(domain) if is_git else None
            ),
            "error": status.error if status else None,
            "exists_in_db": stats.get("exists", False),
        }

    def get_all_domains_info(self) -> list[dict[str, Any]]:
        """获取所有领域的信息。"""
        domains = self._scanner.list_domains()
        return [self.get_domain_info(d) for d in domains]
