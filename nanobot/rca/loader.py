"""RCA Skill 文件加载器。

负责从指定目录加载 YAML Skill 文件，区分 Atomic Skill 和 SOP Skill，
支持格式校验、热加载和 RAG 注册。
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
from nanobot.rca.schema import AtomicSkill, SOPSkill, SkillType


class RCASkillLoader:
    """RCA Skill 文件加载器。

    职责：
    1. 从指定目录加载 YAML Skill 文件
    2. 区分 Atomic Skill 和 SOP Skill
    3. 格式校验与解析
    4. 文件变更监听与热加载
    5. 注册到 RAG 向量库
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
        self._atomic_skills: dict[str, AtomicSkill] = {}  # name -> AtomicSkill
        self._sop_skills: dict[str, SOPSkill] = {}         # name -> SOPSkill
        self._lock = threading.RLock()
        self._watcher_thread: threading.Thread | None = None
        self._watcher_stop_event = threading.Event()

    def load_all(self) -> int:
        """加载目录中所有 YAML Skill 文件（递归搜索子目录）。

        Returns:
            成功加载的 Skill 数量
        """
        if not self.skill_dir.exists():
            logger.info(f"[RCA-LOADER] 创建 Skill 目录: {self.skill_dir}")
            self.skill_dir.mkdir(parents=True, exist_ok=True)
            return 0

        count = 0
        # 递归加载所有 yaml/yml 文件
        yaml_files = sorted(self.skill_dir.rglob("*.yaml")) + sorted(
            self.skill_dir.rglob("*.yml")
        )
        for path in yaml_files:
            skill = self.load_file(path)
            if skill:
                count += 1

        atomic_count = len(self._atomic_skills)
        sop_count = len(self._sop_skills)
        logger.info(
            f"[RCA-LOADER] 已加载 {count} 个 RCA Skill "
            f"(Atomic: {atomic_count}, SOP: {sop_count})"
        )
        return count

    def load_file(self, path: Path) -> AtomicSkill | SOPSkill | None:
        """加载并校验单个 YAML 文件，按 type 字段区分类型。

        Args:
            path: YAML 文件路径

        Returns:
            解析成功的 AtomicSkill 或 SOPSkill 对象，失败返回 None
        """
        try:
            with open(path, "r", encoding="utf-8") as f:
                raw = yaml.safe_load(f)

            if not raw:
                logger.warning(f"[RCA-LOADER] 空文件: {path}")
                return None

            # 校验并解析（parse_yaml 自动按 type 区分）
            skill = parse_yaml(raw)
            skill.file_path = str(path)
            skill.loaded_at = datetime.now().isoformat()

            # 按类型存入对应的字典
            with self._lock:
                if isinstance(skill, AtomicSkill):
                    self._atomic_skills[skill.name] = skill
                    logger.info(
                        f"[RCA-LOADER] ✅ 加载 Atomic Skill: {skill.name} "
                        f"v{skill.version} (output_schema: "
                        f"{list(skill.output_schema.keys())})"
                    )
                elif isinstance(skill, SOPSkill):
                    self._sop_skills[skill.name] = skill
                    logger.info(
                        f"[RCA-LOADER] ✅ 加载 SOP Skill: {skill.name} "
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

    # ------------------------------------------------------------------
    # 查询接口
    # ------------------------------------------------------------------

    def get_atomic_skill(self, name: str) -> AtomicSkill | None:
        """按名称获取已加载的 Atomic Skill。

        用于 SOP 步骤执行时查找 output_schema 和绑定的 Tool。

        Args:
            name: Atomic Skill 名称

        Returns:
            AtomicSkill 对象，未找到返回 None
        """
        with self._lock:
            return self._atomic_skills.get(name)

    def get_sop_skill(self, name: str) -> SOPSkill | None:
        """按名称获取已加载的 SOP Skill。

        Args:
            name: SOP Skill 名称

        Returns:
            SOPSkill 对象，未找到返回 None
        """
        with self._lock:
            return self._sop_skills.get(name)

    def get_skill(self, name: str) -> AtomicSkill | SOPSkill | None:
        """按名称获取已加载的 Skill（不区分类型）。

        优先查找 SOP Skill，再查找 Atomic Skill。

        Args:
            name: Skill 名称

        Returns:
            Skill 对象，未找到返回 None
        """
        with self._lock:
            return self._sop_skills.get(name) or self._atomic_skills.get(name)

    def list_skills(self) -> list[dict[str, Any]]:
        """列出所有已加载 Skill 的摘要信息。

        返回包含 type、output_schema 等字段的摘要列表。
        """
        with self._lock:
            result: list[dict[str, Any]] = []

            # Atomic Skills
            for skill in self._atomic_skills.values():
                result.append({
                    "name": skill.name,
                    "version": skill.version,
                    "description": skill.description,
                    "type": "atomic",
                    "output_schema": skill.output_schema,
                    "input_schema": skill.input_schema,
                    "file_path": skill.file_path or "",
                    "loaded_at": skill.loaded_at or "",
                })

            # SOP Skills
            for skill in self._sop_skills.values():
                result.append({
                    "name": skill.name,
                    "version": skill.version,
                    "description": skill.description,
                    "type": "sop",
                    "steps_count": len(skill.steps),
                    "input_schema": skill.input_schema,
                    "file_path": skill.file_path or "",
                    "loaded_at": skill.loaded_at or "",
                })

            return result

    def list_skill_names(self) -> list[str]:
        """列出所有已加载 Skill 的名称列表。

        用于 IntentClassifier 的 skill_names 参数。
        """
        with self._lock:
            names = list(self._sop_skills.keys()) + list(self._atomic_skills.keys())
            return sorted(set(names))

    def get_all_skills(self) -> dict[str, AtomicSkill | SOPSkill]:
        """获取所有已加载的 Skill。"""
        with self._lock:
            result: dict[str, AtomicSkill | SOPSkill] = {}
            result.update(self._atomic_skills)
            result.update(self._sop_skills)
            return result

    def remove_skill(self, name: str) -> None:
        """移除指定 Skill。"""
        with self._lock:
            removed = False
            if name in self._atomic_skills:
                del self._atomic_skills[name]
                removed = True
            if name in self._sop_skills:
                del self._sop_skills[name]
                removed = True
            if removed:
                logger.info(f"[RCA-LOADER] 移除 Skill: {name}")

    # ------------------------------------------------------------------
    # 热加载
    # ------------------------------------------------------------------

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

        def _is_skill_file(path: str) -> bool:
            """检查是否为 yaml/yml 文件。"""
            from pathlib import Path as _Path
            name = _Path(path).name
            return name.endswith(".yaml") or name.endswith(".yml")

        class _Handler(FileSystemEventHandler):
            """文件系统事件处理器。"""

            def on_created(self, event):
                if not event.is_directory and _is_skill_file(event.src_path):
                    logger.info(f"[RCA-LOADER] 检测到新增: {event.src_path}")
                    loader.load_file(Path(event.src_path))

            def on_modified(self, event):
                if not event.is_directory and _is_skill_file(event.src_path):
                    logger.info(f"[RCA-LOADER] 检测到修改: {event.src_path}")
                    loader.load_file(Path(event.src_path))

            def on_deleted(self, event):
                if not event.is_directory and _is_skill_file(event.src_path):
                    logger.info(f"[RCA-LOADER] 检测到删除: {event.src_path}")
                    # 根据文件路径找到对应的 Skill 并移除
                    with loader._lock:
                        to_remove: list[str] = []
                        for name, skill in loader._atomic_skills.items():
                            if skill.file_path == event.src_path:
                                to_remove.append(name)
                        for name, skill in loader._sop_skills.items():
                            if skill.file_path == event.src_path:
                                to_remove.append(name)
                    for name in to_remove:
                        loader.remove_skill(name)

        self._watcher_stop_event.clear()

        def _watch_loop():
            observer = Observer()
            observer.schedule(_Handler(), str(self.skill_dir), recursive=True)
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

    # ------------------------------------------------------------------
    # RAG 注册
    # ------------------------------------------------------------------

    def _register_to_rag(self, skill: AtomicSkill | SOPSkill) -> None:
        """将 Skill 注册到 RAG 向量库供检索。"""
        if not self.intent_store:
            return

        try:
            # 向量化名字、描述、类型和源文件地址，保持检索文本精简
            skill_type = "Atomic" if isinstance(skill, AtomicSkill) else "SOP"
            doc_text = (
                f"Skill: {skill.name}\n"
                f"类型: {skill_type}\n"
                f"描述: {skill.description}\n"
                f"源文件: {skill.file_path or ''}"
            )

            # 使用 IntentRoutingStore 的统一方法注册
            if hasattr(self.intent_store, "register_skill"):
                skill_type_val = "atomic" if isinstance(skill, AtomicSkill) else "sop"
                self.intent_store.register_skill(
                    skill_name=skill.name,
                    doc_text=doc_text,
                    skill_type=skill_type_val,
                )
                logger.debug(
                    f"[RCA-LOADER] {skill_type} Skill '{skill.name}' 已注册到 RAG 索引"
                )
        except Exception as e:
            logger.warning(
                f"[RCA-LOADER] Skill '{skill.name}' RAG 注册失败: {e}"
            )
