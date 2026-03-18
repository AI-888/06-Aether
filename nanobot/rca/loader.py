"""RCA Skill 文件加载器。

负责从指定目录加载 YAML Skill 文件，支持格式校验、热加载和 RAG 注册。
"""

from __future__ import annotations

import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Any

import yaml
from loguru import logger

from nanobot.rca.parser import SkillValidationError, parse_yaml, validate
from nanobot.rca.schema import RCASkill


class RCASkillLoader:
    """RCA Skill 文件加载器。

    职责：
    1. 从指定目录加载 YAML Skill 文件
    2. 格式校验与解析
    3. 文件变更监听与热加载
    4. 注册到 RAG 向量库
    """

    def __init__(
        self,
        skill_dir: str | Path,
        intent_routing_store: Any | None = None,
    ):
        """初始化加载器。

        Args:
            skill_dir: RCA Skill YAML 文件所在目录
            intent_routing_store: IntentRoutingStore 实例（用于 RAG 注册）
        """
        self.skill_dir = Path(skill_dir).expanduser()
        self.intent_store = intent_routing_store
        self._skills: dict[str, RCASkill] = {}  # name -> RCASkill
        self._lock = threading.RLock()
        self._watcher_thread: threading.Thread | None = None
        self._watcher_stop_event = threading.Event()

    def load_all(self) -> int:
        """加载目录中所有 YAML Skill 文件。

        Returns:
            成功加载的 Skill 数量
        """
        if not self.skill_dir.exists():
            logger.info(f"[RCA-LOADER] 创建 Skill 目录: {self.skill_dir}")
            self.skill_dir.mkdir(parents=True, exist_ok=True)
            return 0

        count = 0
        for path in sorted(self.skill_dir.glob("*.yaml")):
            skill = self.load_file(path)
            if skill:
                count += 1

        logger.info(f"[RCA-LOADER] 已加载 {count} 个 RCA Skill")
        return count

    def load_file(self, path: Path) -> RCASkill | None:
        """加载并校验单个 YAML 文件。

        Args:
            path: YAML 文件路径

        Returns:
            解析成功的 RCASkill 对象，失败返回 None
        """
        try:
            with open(path, "r", encoding="utf-8") as f:
                raw = yaml.safe_load(f)

            if not raw:
                logger.warning(f"[RCA-LOADER] 空文件: {path}")
                return None

            # 校验并解析
            skill = parse_yaml(raw)
            skill.file_path = str(path)
            skill.loaded_at = datetime.now().isoformat()

            with self._lock:
                self._skills[skill.name] = skill

            logger.info(
                f"[RCA-LOADER] ✅ 加载 Skill: {skill.name} "
                f"v{skill.version} ({len(skill.steps)} 步骤)"
            )

            # 注册到 RAG
            self._register_to_rag(skill)

            return skill

        except SkillValidationError as e:
            logger.error(
                f"[RCA-LOADER] ❌ Skill 校验失败 ({path.name}): "
                f"{'; '.join(e.errors)}"
            )
            return None

        except yaml.YAMLError as e:
            logger.error(f"[RCA-LOADER] ❌ YAML 解析失败 ({path.name}): {e}")
            return None

        except Exception as e:
            logger.error(f"[RCA-LOADER] ❌ 加载失败 ({path.name}): {e}")
            return None

    def get_skill(self, name: str) -> RCASkill | None:
        """按名称获取已加载的 Skill。"""
        with self._lock:
            return self._skills.get(name)

    def list_skills(self) -> list[dict[str, str]]:
        """列出所有已加载 Skill 的摘要信息。"""
        with self._lock:
            return [
                {
                    "name": skill.name,
                    "version": skill.version,
                    "description": skill.description,
                    "type": skill.type,
                    "steps_count": str(len(skill.steps)),
                    "file_path": skill.file_path or "",
                    "loaded_at": skill.loaded_at or "",
                }
                for skill in self._skills.values()
            ]

    def get_all_skills(self) -> dict[str, RCASkill]:
        """获取所有已加载的 Skill。"""
        with self._lock:
            return dict(self._skills)

    def remove_skill(self, name: str) -> None:
        """移除指定 Skill。"""
        with self._lock:
            if name in self._skills:
                del self._skills[name]
                logger.info(f"[RCA-LOADER] 移除 Skill: {name}")

    def start_watcher(self) -> None:
        """启动文件监听，实现热加载。

        使用 watchdog 库在后台线程中监听文件系统变更事件。
        """
        if self._watcher_thread and self._watcher_thread.is_alive():
            logger.warning("[RCA-LOADER] 文件监听已在运行")
            return

        try:
            from watchdog.events import FileSystemEventHandler
            from watchdog.observers import Observer
        except ImportError:
            logger.warning(
                "[RCA-LOADER] watchdog 未安装，热加载不可用。"
                "请运行: pip install watchdog"
            )
            return

        loader = self

        class _Handler(FileSystemEventHandler):
            """文件系统事件处理器。"""

            def on_created(self, event):
                if not event.is_directory and event.src_path.endswith(".yaml"):
                    logger.info(f"[RCA-LOADER] 检测到新增: {event.src_path}")
                    loader.load_file(Path(event.src_path))

            def on_modified(self, event):
                if not event.is_directory and event.src_path.endswith(".yaml"):
                    logger.info(f"[RCA-LOADER] 检测到修改: {event.src_path}")
                    loader.load_file(Path(event.src_path))

            def on_deleted(self, event):
                if not event.is_directory and event.src_path.endswith(".yaml"):
                    logger.info(f"[RCA-LOADER] 检测到删除: {event.src_path}")
                    # 根据文件路径找到对应的 Skill 并移除
                    with loader._lock:
                        to_remove = [
                            name
                            for name, skill in loader._skills.items()
                            if skill.file_path == event.src_path
                        ]
                    for name in to_remove:
                        loader.remove_skill(name)

        self._watcher_stop_event.clear()

        def _watch_loop():
            observer = Observer()
            observer.schedule(_Handler(), str(self.skill_dir), recursive=False)
            observer.start()
            logger.info(
                f"[RCA-LOADER] 🔄 文件监听已启动: {self.skill_dir}"
            )
            try:
                while not self._watcher_stop_event.is_set():
                    time.sleep(1)
            finally:
                observer.stop()
                observer.join()
                logger.info("[RCA-LOADER] 文件监听已停止")

        self._watcher_thread = threading.Thread(
            target=_watch_loop, daemon=True, name="rca-skill-watcher"
        )
        self._watcher_thread.start()

    def stop_watcher(self) -> None:
        """停止文件监听。"""
        self._watcher_stop_event.set()
        if self._watcher_thread:
            self._watcher_thread.join(timeout=5)
            self._watcher_thread = None
        logger.info("[RCA-LOADER] 文件监听已请求停止")

    def _register_to_rag(self, skill: RCASkill) -> None:
        """将 Skill 注册到 RAG 向量库供检索。"""
        if not self.intent_store:
            return

        try:
            # 拼接可检索文本
            steps_desc = " → ".join(
                f"{s.id}({s.type.value})" for s in skill.steps
            )
            doc_text = (
                f"RCA Skill: {skill.name}\n"
                f"描述: {skill.description}\n"
                f"类型: {skill.type}\n"
                f"步骤: {steps_desc}"
            )

            # 使用 IntentRoutingStore 的方法注册
            if hasattr(self.intent_store, "register_rca_skill"):
                self.intent_store.register_rca_skill(
                    skill_name=skill.name,
                    doc_text=doc_text,
                )
                logger.debug(
                    f"[RCA-LOADER] Skill '{skill.name}' 已注册到 RAG 索引"
                )
        except Exception as e:
            logger.warning(
                f"[RCA-LOADER] Skill '{skill.name}' RAG 注册失败: {e}"
            )
