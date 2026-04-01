"""源代码 RAG 管理后端 API。

提供领域概览、管理、文件浏览、操作日志、WebSocket 实时进度等接口。
独立于 web.py 主文件，通过 APIRouter 集成。
"""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from fastapi import APIRouter, WebSocket, WebSocketDisconnect, Query
from fastapi.responses import HTMLResponse
from loguru import logger
from pydantic import BaseModel

# ---------------------------------------------------------------------------
# Pydantic 请求/响应模型
# ---------------------------------------------------------------------------


class DomainCreateRequest(BaseModel):
    """创建领域请求。"""
    domain_name: str
    source_type: str = "local"  # "local" 或 "git"
    repo_url: Optional[str] = None
    branch: Optional[str] = "main"
    sub_directory: Optional[str] = None


class FileDeleteRequest(BaseModel):
    """删除文件请求。"""
    file_pattern: Optional[str] = None  # glob 模式


# ---------------------------------------------------------------------------
# 操作日志管理
# ---------------------------------------------------------------------------


class OperationLogger:
    """操作审计日志记录器。"""

    def __init__(self, knowledge_dir: Path):
        self._log_file = knowledge_dir / "source_code_operation_logs.json"
        self._logs: list[dict] = self._load()

    def _load(self) -> list[dict]:
        if self._log_file.exists():
            try:
                return json.loads(self._log_file.read_text(encoding="utf-8"))
            except Exception:
                return []
        return []

    def _save(self) -> None:
        try:
            self._log_file.parent.mkdir(parents=True, exist_ok=True)
            self._log_file.write_text(
                json.dumps(self._logs, indent=2, ensure_ascii=False),
                encoding="utf-8",
            )
        except Exception as exc:
            logger.error(f"[OpLog] 日志写入失败: {exc}")

    def log(
        self,
        domain: str,
        action: str,
        params: dict = None,
        result: str = "success",
        detail: str = "",
    ) -> None:
        entry = {
            "id": len(self._logs) + 1,
            "domain": domain,
            "action": action,
            "params": params or {},
            "result": result,
            "detail": detail,
            "timestamp": datetime.now().isoformat(),
        }
        self._logs.append(entry)
        self._save()

    def get_logs(
        self,
        domain: Optional[str] = None,
        action_type: Optional[str] = None,
        page: int = 1,
        page_size: int = 20,
    ) -> dict:
        filtered = self._logs
        if domain:
            filtered = [l for l in filtered if l.get("domain") == domain]
        if action_type:
            filtered = [l for l in filtered if l.get("action") == action_type]

        # 按时间倒序
        filtered = list(reversed(filtered))
        total = len(filtered)
        start = (page - 1) * page_size
        end = start + page_size
        return {
            "logs": filtered[start:end],
            "total": total,
            "page": page,
            "page_size": page_size,
        }


# ---------------------------------------------------------------------------
# WebSocket 进度管理
# ---------------------------------------------------------------------------


class SourceCodeWSManager:
    """源代码管理 WebSocket 连接管理器。"""

    def __init__(self):
        self._connections: list[WebSocket] = []

    async def connect(self, ws: WebSocket):
        await ws.accept()
        self._connections.append(ws)

    def disconnect(self, ws: WebSocket):
        if ws in self._connections:
            self._connections.remove(ws)

    async def broadcast(self, message: dict):
        dead = []
        for ws in self._connections:
            try:
                await ws.send_json(message)
            except Exception:
                dead.append(ws)
        for ws in dead:
            self._connections.remove(ws)


# ---------------------------------------------------------------------------
# 领域级锁
# ---------------------------------------------------------------------------


class DomainLockManager:
    """领域级锁管理器，确保同一领域同时只有一个写操作。"""

    def __init__(self):
        self._locks: dict[str, asyncio.Lock] = {}
        self._busy: dict[str, str] = {}  # domain -> 当前操作描述

    def get_lock(self, domain: str) -> asyncio.Lock:
        if domain not in self._locks:
            self._locks[domain] = asyncio.Lock()
        return self._locks[domain]

    def is_busy(self, domain: str) -> bool:
        return domain in self._busy

    def set_busy(self, domain: str, operation: str):
        self._busy[domain] = operation

    def clear_busy(self, domain: str):
        self._busy.pop(domain, None)

    def get_busy_operation(self, domain: str) -> Optional[str]:
        return self._busy.get(domain)


# ---------------------------------------------------------------------------
# 全局实例（延迟初始化）
# ---------------------------------------------------------------------------

_ws_manager = SourceCodeWSManager()
_lock_manager = DomainLockManager()
_op_logger: Optional[OperationLogger] = None
_indexer_cache = None


def _get_workspace() -> Path:
    """获取 workspace 路径。"""
    try:
        from nanobot.config.loader import load_config
        config = load_config()
        return Path(config.agents.defaults.workspace)
    except Exception:
        return Path.home() / ".nanobot" / "workspace"


def _get_indexer():
    """获取或创建 SourceCodeIndexer 实例。"""
    global _indexer_cache
    if _indexer_cache is None:
        from nanobot.knowledge.source_code.indexer import SourceCodeIndexer
        workspace = _get_workspace()
        _indexer_cache = SourceCodeIndexer(workspace=workspace)
    return _indexer_cache


def _get_op_logger() -> OperationLogger:
    """获取操作日志记录器。"""
    global _op_logger
    if _op_logger is None:
        workspace = _get_workspace()
        _op_logger = OperationLogger(workspace / "knowledge")
    return _op_logger


def load_html_template(template_name: str) -> str:
    """加载 HTML 模板。"""
    template_path = Path(__file__).parent / "templates" / template_name
    try:
        with open(template_path, "r", encoding="utf-8") as f:
            return f.read()
    except FileNotFoundError:
        return f"<html><body><h1>Template not found: {template_name}</h1></body></html>"


# ---------------------------------------------------------------------------
# API 路由
# ---------------------------------------------------------------------------

router = APIRouter()


# ==================== 页面路由 ====================


@router.get("/source-code")
async def get_source_code_page():
    """加载源代码 RAG 管理页面。"""
    html_content = load_html_template("source_code.html")
    return HTMLResponse(content=html_content)


# ==================== 领域概览 API ====================


@router.get("/api/source-code/domains")
async def list_domains():
    """返回所有领域列表及状态概要。"""
    try:
        indexer = _get_indexer()
        domains_info = indexer.get_all_domains_info()

        # 补充「初始化中」状态
        for info in domains_info:
            domain = info["domain"]
            if _lock_manager.is_busy(domain):
                info["status"] = "initializing"
                info["busy_operation"] = _lock_manager.get_busy_operation(domain)
            elif info.get("error"):
                info["status"] = "error"
            elif info.get("initialized"):
                info["status"] = "initialized"
            else:
                info["status"] = "not_initialized"

        # 全局统计
        total_domains = len(domains_info)
        initialized_count = sum(1 for d in domains_info if d.get("status") == "initialized")
        total_files = sum(d.get("file_count", 0) for d in domains_info)
        total_chunks = sum(d.get("chunk_count", 0) for d in domains_info)

        return {
            "domains": domains_info,
            "stats": {
                "total_domains": total_domains,
                "initialized_count": initialized_count,
                "total_files": total_files,
                "total_chunks": total_chunks,
            },
        }
    except Exception as exc:
        logger.error(f"[API] 获取领域列表失败: {exc}")
        return {"domains": [], "stats": {}, "error": str(exc)}


@router.get("/api/source-code/domains/{domain}")
async def get_domain_detail(domain: str):
    """返回指定领域的详细信息。"""
    try:
        indexer = _get_indexer()
        info = indexer.get_domain_info(domain)
        if _lock_manager.is_busy(domain):
            info["status"] = "initializing"
            info["busy_operation"] = _lock_manager.get_busy_operation(domain)
        elif info.get("error"):
            info["status"] = "error"
        elif info.get("initialized"):
            info["status"] = "initialized"
        else:
            info["status"] = "not_initialized"
        return info
    except Exception as exc:
        return {"error": str(exc)}


# ==================== 领域管理 API ====================


@router.post("/api/source-code/domains")
async def create_domain(request: DomainCreateRequest):
    """创建新领域（本地目录或 Git clone）。"""
    try:
        workspace = _get_workspace()
        src_dir = workspace / "src"
        domain_dir = src_dir / request.domain_name
        op_logger = _get_op_logger()

        # 检查领域是否已存在
        if domain_dir.exists():
            return {"error": f"领域 '{request.domain_name}' 已存在", "status": 409}

        if request.source_type == "git" and request.repo_url:
            # Git clone
            from nanobot.knowledge.source_code.git_manager import GitManager, RepoConfig
            git_mgr = GitManager(src_dir)
            config = RepoConfig(
                domain_name=request.domain_name,
                repo_url=request.repo_url,
                branch=request.branch or "main",
                sub_directory=request.sub_directory,
            )
            result = git_mgr.sync_repo(config)
            if not result.success:
                op_logger.log(request.domain_name, "create", {"source_type": "git"},
                              "failed", result.error or "")
                return {"error": result.error, "status": 500}

            op_logger.log(request.domain_name, "create", {"source_type": "git",
                          "repo_url": request.repo_url}, "success")
        else:
            # 本地目录
            domain_dir.mkdir(parents=True, exist_ok=True)
            op_logger.log(request.domain_name, "create", {"source_type": "local"}, "success")

        # 自动触发初始化（异步）
        asyncio.create_task(_async_init_domain(request.domain_name))

        return {"success": True, "domain": request.domain_name, "message": "领域创建成功，初始化已启动"}

    except Exception as exc:
        return {"error": str(exc)}


@router.delete("/api/source-code/domains/{domain}")
async def delete_domain(domain: str):
    """删除领域（源码 + RAG 索引）。"""
    if _lock_manager.is_busy(domain):
        return {"error": f"领域 '{domain}' 正在执行操作: {_lock_manager.get_busy_operation(domain)}", "status": 409}

    try:
        workspace = _get_workspace()
        indexer = _get_indexer()
        op_logger = _get_op_logger()

        # 删除索引
        indexer.delete_domain_index(domain)

        # 删除源码目录
        domain_dir = workspace / "src" / domain
        deleted_files = 0
        if domain_dir.exists():
            for root, dirs, files in os.walk(domain_dir):
                deleted_files += len(files)
            shutil.rmtree(domain_dir)

        op_logger.log(domain, "delete_domain", {}, "success",
                       f"已删除 {deleted_files} 个文件和 RAG 索引")

        return {"success": True, "deleted_files": deleted_files, "message": "领域已删除"}
    except Exception as exc:
        return {"error": str(exc)}


@router.delete("/api/source-code/domains/{domain}/files")
async def delete_domain_files(domain: str, file_pattern: Optional[str] = Query(None)):
    """按 glob 模式删除文件及索引。"""
    if _lock_manager.is_busy(domain):
        return {"error": f"领域 '{domain}' 正在执行操作", "status": 409}

    try:
        import fnmatch
        workspace = _get_workspace()
        domain_dir = workspace / "src" / domain
        op_logger = _get_op_logger()

        if not domain_dir.exists():
            return {"error": f"领域 '{domain}' 不存在"}

        deleted = 0
        if file_pattern:
            for root, dirs, files in os.walk(domain_dir):
                for f in files:
                    if fnmatch.fnmatch(f, file_pattern):
                        os.remove(os.path.join(root, f))
                        deleted += 1
        else:
            # 删除全部文件
            for root, dirs, files in os.walk(domain_dir):
                for f in files:
                    os.remove(os.path.join(root, f))
                    deleted += 1

        op_logger.log(domain, "delete_files", {"pattern": file_pattern}, "success",
                       f"已删除 {deleted} 个文件")

        return {"success": True, "deleted_files": deleted}
    except Exception as exc:
        return {"error": str(exc)}


@router.delete("/api/source-code/domains/{domain}/rag")
async def delete_domain_rag(domain: str):
    """仅删除 RAG 向量库（保留源码文件）。"""
    if _lock_manager.is_busy(domain):
        return {"error": f"领域 '{domain}' 正在执行操作", "status": 409}

    try:
        indexer = _get_indexer()
        op_logger = _get_op_logger()

        success = indexer.delete_domain_index(domain)
        op_logger.log(domain, "delete_rag", {}, "success" if success else "failed")

        return {"success": success, "message": "RAG 向量库已删除" if success else "删除失败"}
    except Exception as exc:
        return {"error": str(exc)}


@router.post("/api/source-code/domains/{domain}/reinit")
async def reinit_domain(domain: str):
    """触发单领域重新初始化。"""
    if _lock_manager.is_busy(domain):
        return {"error": f"领域 '{domain}' 正在执行操作: {_lock_manager.get_busy_operation(domain)}", "status": 409}

    asyncio.create_task(_async_init_domain(domain, force=True))
    return {"success": True, "message": f"领域 '{domain}' 重新初始化已启动"}


@router.post("/api/source-code/reinit-all")
async def reinit_all_domains():
    """触发全部领域重新初始化。"""
    try:
        indexer = _get_indexer()
        domains = indexer.scanner.list_domains()
        started = []
        skipped = []

        for domain in domains:
            if _lock_manager.is_busy(domain):
                skipped.append(domain)
            else:
                asyncio.create_task(_async_init_domain(domain, force=True))
                started.append(domain)

        return {
            "success": True,
            "started": started,
            "skipped": skipped,
            "message": f"已启动 {len(started)} 个领域重新初始化",
        }
    except Exception as exc:
        return {"error": str(exc)}


# ==================== 文件浏览 API ====================


@router.get("/api/source-code/domains/{domain}/files")
async def get_domain_files(domain: str):
    """返回领域的文件目录树。"""
    try:
        indexer = _get_indexer()
        tree = indexer.scanner.get_domain_file_tree(domain)

        # 补充索引状态
        status = indexer.init_status.get_domain_status(domain)
        is_indexed = status.initialized if status else False

        return {"tree": tree, "indexed": is_indexed}
    except Exception as exc:
        return {"error": str(exc)}


@router.get("/api/source-code/domains/{domain}/files/{file_path:path}")
async def get_file_content(domain: str, file_path: str):
    """返回文件内容（限 200 行）。"""
    try:
        workspace = _get_workspace()
        abs_path = workspace / "src" / domain / file_path

        # 安全检查
        try:
            abs_path.resolve().relative_to((workspace / "src" / domain).resolve())
        except ValueError:
            return {"error": "访问被拒绝：路径在领域目录之外"}

        if not abs_path.exists():
            return {"error": "文件不存在"}

        try:
            with open(abs_path, "r", encoding="utf-8", errors="replace") as f:
                lines = []
                for i, line in enumerate(f):
                    if i >= 200:
                        break
                    lines.append(line.rstrip("\n"))
                content = "\n".join(lines)
        except Exception as exc:
            return {"error": f"读取文件失败: {exc}"}

        ext = abs_path.suffix.lower()
        lang_map = {
            ".py": "python", ".java": "java", ".go": "go",
            ".js": "javascript", ".ts": "typescript",
            ".c": "c", ".cpp": "cpp", ".h": "c",
            ".sql": "sql", ".sh": "bash",
            ".yaml": "yaml", ".yml": "yaml", ".json": "json",
            ".xml": "xml", ".conf": "ini",
        }

        return {
            "content": content,
            "language": lang_map.get(ext, "text"),
            "filename": abs_path.name,
            "size": abs_path.stat().st_size,
            "total_lines": sum(1 for _ in open(abs_path, "r", encoding="utf-8", errors="replace")),
            "truncated": sum(1 for _ in open(abs_path, "r", encoding="utf-8", errors="replace")) > 200,
        }
    except Exception as exc:
        return {"error": str(exc)}


# ==================== 操作日志 API ====================


@router.get("/api/source-code/domains/{domain}/logs")
async def get_domain_logs(
    domain: str,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    action_type: Optional[str] = Query(None),
):
    """返回领域的操作历史记录。"""
    try:
        op_logger = _get_op_logger()
        return op_logger.get_logs(
            domain=domain,
            action_type=action_type,
            page=page,
            page_size=page_size,
        )
    except Exception as exc:
        return {"error": str(exc)}


# ==================== WebSocket 实时进度 ====================


@router.websocket("/ws/source-code")
async def source_code_ws(websocket: WebSocket):
    """源代码管理 WebSocket 端点。"""
    await _ws_manager.connect(websocket)
    try:
        while True:
            # 保持连接活跃，接收客户端心跳
            data = await websocket.receive_text()
            if data == "ping":
                await websocket.send_text("pong")
    except WebSocketDisconnect:
        _ws_manager.disconnect(websocket)


# ---------------------------------------------------------------------------
# 异步初始化辅助函数
# ---------------------------------------------------------------------------


async def _async_init_domain(domain: str, force: bool = False):
    """异步执行领域初始化。"""
    lock = _lock_manager.get_lock(domain)
    if lock.locked():
        return

    async with lock:
        _lock_manager.set_busy(domain, "reinitializing" if force else "initializing")
        op_logger = _get_op_logger()

        try:
            indexer = _get_indexer()

            # 设置进度回调
            def progress_callback(d, stage, current, total, current_file, message):
                asyncio.ensure_future(_ws_manager.broadcast({
                    "type": "progress",
                    "domain": d,
                    "stage": stage,
                    "current": current,
                    "total": total,
                    "current_file": current_file,
                    "message": message,
                }))

            indexer.set_progress_callback(progress_callback)

            # 在线程池中执行（因为 indexer 内部是同步代码）
            loop = asyncio.get_event_loop()
            result = await loop.run_in_executor(
                None,
                lambda: indexer.initialize_domain(domain, force=force),
            )

            # 广播完成/失败消息
            if result.success:
                await _ws_manager.broadcast({
                    "type": "complete",
                    "domain": domain,
                    "result": {
                        "file_count": result.file_count,
                        "chunk_count": result.chunk_count,
                        "duration_seconds": result.duration_seconds,
                    },
                })
                op_logger.log(domain, "reinit" if force else "init", {},
                              "success",
                              f"files={result.file_count}, chunks={result.chunk_count}")
            else:
                await _ws_manager.broadcast({
                    "type": "error",
                    "domain": domain,
                    "error": result.error or "未知错误",
                })
                op_logger.log(domain, "reinit" if force else "init", {},
                              "failed", result.error or "")

        except Exception as exc:
            await _ws_manager.broadcast({
                "type": "error",
                "domain": domain,
                "error": str(exc),
            })
            op_logger.log(domain, "reinit" if force else "init", {},
                          "failed", str(exc))
        finally:
            _lock_manager.clear_busy(domain)
