"""Prometheus 指标定义模块.

集中管理所有 Prometheus 监控指标，供各模块导入使用。
通过 prometheus_client 暴露指标，供 Prometheus server 抓取。
"""

from __future__ import annotations

from prometheus_client import Histogram, Counter, Gauge, REGISTRY

# ---------------------------------------------------------------------------
# 1. 大模型 (LLM) 访问耗时指标
# ---------------------------------------------------------------------------

# LLM 调用耗时直方图
# 标签:
#   - model: 模型名称 (e.g. "anthropic/claude-sonnet-4-5")
#   - purpose: 调用目的 (e.g. "intent_classification", "qa_answer", "agent_loop", "troubleshooting", "general")
#   - status: 调用状态 ("success" / "error")
#   - input_length_range: 输入文本长度区间 (0-500每10一档, 500+每100一档, 最大"32000+")
LLM_REQUEST_DURATION = Histogram(
    "aether_llm_request_duration_seconds",
    "大模型调用耗时（秒）",
    labelnames=["model", "purpose", "status", "input_length_range"],
    buckets=(0.5, 1, 2, 3, 5, 8, 10, 15, 20, 30, 45, 60, 90, 120),
)

# LLM 输出文本长度直方图
LLM_OUTPUT_TEXT_LENGTH = Histogram(
    "aether_llm_output_text_length_chars",
    "大模型输出的完整内容长度（字符数）",
    labelnames=["model", "purpose"],
    buckets=(50, 100, 500, 1000, 2000, 5000, 10000, 20000),
)

# LLM 调用次数计数器
LLM_REQUEST_TOTAL = Counter(
    "aether_llm_request_total",
    "大模型调用总次数",
    labelnames=["model", "purpose", "status"],
)

# ---------------------------------------------------------------------------
# 2. RAG 向量数据库访问耗时指标
# ---------------------------------------------------------------------------

# 向量数据库查询耗时直方图
# 标签:
#   - operation: 操作类型 (e.g. "semantic_search", "metadata_search", "intent_routing_tools", "intent_routing_skills")
#   - domain: 知识领域 (e.g. "rocketmq", "kubernetes", "general")
#   - status: 查询状态 ("success" / "error")
RAG_QUERY_DURATION = Histogram(
    "aether_rag_query_duration_seconds",
    "RAG 向量数据库查询耗时（秒）",
    labelnames=["operation", "domain", "status"],
    buckets=(0.01, 0.05, 0.1, 0.2, 0.5, 1, 2, 3, 5, 10),
)

# 向量化（embedding）耗时直方图
RAG_EMBEDDING_DURATION = Histogram(
    "aether_rag_embedding_duration_seconds",
    "文本向量化耗时（秒）",
    labelnames=["operation"],
    buckets=(0.01, 0.05, 0.1, 0.2, 0.5, 1, 2, 5),
)

# 向量数据库查询结果数量直方图
RAG_QUERY_RESULTS_COUNT = Histogram(
    "aether_rag_query_results_count",
    "向量数据库查询返回的结果数量",
    labelnames=["operation", "domain"],
    buckets=(0, 1, 2, 3, 5, 10, 20, 50),
)

# 向量数据库查询次数计数器
RAG_QUERY_TOTAL = Counter(
    "aether_rag_query_total",
    "向量数据库查询总次数",
    labelnames=["operation", "domain", "status"],
)

# ---------------------------------------------------------------------------
# 3. 重排序 (Rerank) 耗时指标
# ---------------------------------------------------------------------------

RERANK_DURATION = Histogram(
    "aether_rerank_duration_seconds",
    "CrossEncoder 重排序耗时（秒）",
    labelnames=["status"],
    buckets=(0.01, 0.05, 0.1, 0.2, 0.5, 1, 2, 5),
)

# ---------------------------------------------------------------------------
# 4. RCA（根因分析）指标
# ---------------------------------------------------------------------------

# RCA 完整执行耗时
RCA_EXECUTION_DURATION = Histogram(
    "aether_rca_execution_duration_seconds",
    "RCA 完整执行耗时（秒）",
    labelnames=["skill_name", "status"],
    buckets=(1, 2, 5, 10, 20, 30, 60, 120, 300),
)

# RCA 单步骤执行耗时
RCA_STEP_DURATION = Histogram(
    "aether_rca_step_duration_seconds",
    "RCA 单步骤执行耗时（秒）",
    labelnames=["step_type", "status"],
    buckets=(0.1, 0.5, 1, 2, 5, 10, 20, 30),
)

# RCA 执行总次数
RCA_EXECUTION_TOTAL = Counter(
    "aether_rca_execution_total",
    "RCA 执行总次数",
    labelnames=["skill_name", "status"],
)

# Skill 检索匹配次数
RCA_SKILL_MATCH_TOTAL = Counter(
    "aether_rca_skill_match_total",
    "RCA Skill 检索匹配次数",
    labelnames=["matched"],
)

# 安全拒绝次数
RCA_SECURITY_REJECT_TOTAL = Counter(
    "aether_rca_security_reject_total",
    "RCA 安全拒绝次数",
    labelnames=["tool_name"],
)

# ---------------------------------------------------------------------------
# 辅助函数
# ---------------------------------------------------------------------------


def get_input_length_range(length: int) -> str:
    """将输入文本长度映射为区间标签。

    区间规则:
    - 0 ~ 500: 每 10 一档, e.g. "0-10", "10-20", ..., "490-500"
    - 500 ~ 32000: 每 100 一档, e.g. "500-600", "600-700", ..., "31900-32000"
    - 32000 以上: "32000+"
    """
    if length >= 32000:
        return "32000+"
    if length < 500:
        lower = (length // 10) * 10
        upper = lower + 10
        return f"{lower}-{upper}"
    # 500 ~ 32000, 间隔 100
    lower = (length // 100) * 100
    upper = lower + 100
    return f"{lower}-{upper}"


def calc_messages_text_length(messages: list[dict]) -> int:
    """计算 messages 列表中所有文本内容的总长度（字符数）。"""
    total = 0
    for msg in messages:
        content = msg.get("content", "")
        if isinstance(content, str):
            total += len(content)
        elif isinstance(content, list):
            # 多模态消息格式: [{"type": "text", "text": "..."}, ...]
            for part in content:
                if isinstance(part, dict) and part.get("type") == "text":
                    total += len(part.get("text", ""))
    return total


# ---------------------------------------------------------------------------
# 定时指标日志打印器
# ---------------------------------------------------------------------------

import logging
import os
import threading
from datetime import datetime
from logging.handlers import TimedRotatingFileHandler

from prometheus_client.parser import text_string_to_metric_families
from prometheus_client import generate_latest


class MetricsLogger:
    """定时将全部 Prometheus 指标打印到按日期分割的日志文件。

    日志文件存储在 ``<log_dir>/metrics.log``，并通过
    ``TimedRotatingFileHandler`` 在每日零点自动分割，历史文件后缀为
    ``metrics.log.yyyy-MM-dd``。
    """

    _instance: "MetricsLogger | None" = None

    def __init__(
        self,
        log_dir: str = "logs",
        interval_seconds: int = 5,
        backup_count: int = 30,
    ):
        self._log_dir = log_dir
        self._interval = interval_seconds
        self._backup_count = backup_count
        self._timer: threading.Timer | None = None
        self._running = False
        self._logger = self._setup_logger()

    # ---- 日志配置 ----

    def _setup_logger(self) -> logging.Logger:
        """创建专用 logger，按日期自动分割日志文件。"""
        os.makedirs(self._log_dir, exist_ok=True)

        logger = logging.getLogger("aether.metrics.periodic")
        logger.setLevel(logging.INFO)
        # 避免重复添加 handler（防止多次调用 _setup_logger）
        if logger.handlers:
            return logger

        log_file = os.path.join(self._log_dir, "metrics.log")

        handler = TimedRotatingFileHandler(
            filename=log_file,
            when="midnight",           # 每日零点切割
            interval=1,
            backupCount=self._backup_count,
            encoding="utf-8",
            utc=False,
        )
        # 切割后的文件命名为 metrics.log.yyyy-MM-dd
        handler.suffix = "%Y-%m-%d"
        handler.namer = lambda name: name  # 保持默认命名

        formatter = logging.Formatter(
            fmt="%(asctime)s %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )
        handler.setFormatter(formatter)
        logger.addHandler(handler)

        # 不向上层 logger 传播，避免在控制台重复输出
        logger.propagate = False

        return logger

    # ---- 指标收集与格式化 ----

    @staticmethod
    def _collect_metrics_text() -> str:
        """收集全部已注册的 Prometheus 指标，格式化为可读文本。"""
        raw = generate_latest().decode("utf-8")
        lines: list[str] = []
        for family in text_string_to_metric_families(raw):
            # 跳过 prometheus_client 内置的 python_*/process_* 指标
            if family.name.startswith(("python_", "process_")):
                continue
            for sample in family.samples:
                label_str = ",".join(
                    f'{k}="{v}"' for k, v in sorted(sample.labels.items())
                ) if sample.labels else ""
                metric_name = sample.name
                if label_str:
                    metric_name = f"{sample.name}{{{label_str}}}"
                lines.append(f"  {metric_name} = {sample.value}")
        if not lines:
            return "  (暂无指标数据)"
        return "\n".join(lines)

    # ---- 定时任务控制 ----

    def _tick(self):
        """单次定时回调：打印指标并调度下次执行。"""
        if not self._running:
            return
        try:
            ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            metrics_text = self._collect_metrics_text()
            self._logger.info(
                "[METRICS SNAPSHOT @ %s]\n%s", ts, metrics_text,
            )
        except Exception as exc:  # pragma: no cover
            self._logger.error("[METRICS] 指标采集异常: %s", exc)
        finally:
            # 调度下次执行
            if self._running:
                self._timer = threading.Timer(self._interval, self._tick)
                self._timer.daemon = True
                self._timer.start()

    def start(self):
        """启动定时指标打印。多次调用仅生效一次。"""
        if self._running:
            return
        self._running = True
        self._logger.info(
            "[METRICS] 定时指标打印已启动，间隔 %d 秒，日志目录: %s",
            self._interval,
            os.path.abspath(self._log_dir),
        )
        self._timer = threading.Timer(self._interval, self._tick)
        self._timer.daemon = True
        self._timer.start()

    def stop(self):
        """停止定时指标打印。"""
        self._running = False
        if self._timer:
            self._timer.cancel()
            self._timer = None
        self._logger.info("[METRICS] 定时指标打印已停止")

    # ---- 单例便捷接口 ----

    @classmethod
    def get_instance(
        cls,
        log_dir: str = "logs",
        interval_seconds: int = 5,
        backup_count: int = 30,
    ) -> "MetricsLogger":
        """获取全局单例（首次调用时创建）。"""
        if cls._instance is None:
            cls._instance = cls(
                log_dir=log_dir,
                interval_seconds=interval_seconds,
                backup_count=backup_count,
            )
        return cls._instance


def start_metrics_logging(
    log_dir: str = "logs",
    interval_seconds: int = 5,
    backup_count: int = 30,
):
    """便捷函数：启动全局定时指标打印。"""
    ml = MetricsLogger.get_instance(
        log_dir=log_dir,
        interval_seconds=interval_seconds,
        backup_count=backup_count,
    )
    ml.start()
    return ml