from enum import Enum
from typing import List, Optional

from pydantic import BaseModel, Field


class ProblemScope(str, Enum):
    OBJECT = "object"
    COMPONENT = "component"


class SuspectedObject(str, Enum):
    PRODUCER = "producer"
    CONSUMER = "consumer"
    GROUP = "group"
    TOPIC = "topic"
    NULL = "null"


class SuspectedComponent(str, Enum):
    NAMESRV = "namesrv"
    BROKER = "broker"
    PROXY = "proxy"
    NULL = "null"


class RootCause(str, Enum):
    NETWORK = "network"
    DISK_IO = "disk_io"
    JVM = "jvm"
    REPLICATION = "replication"
    METADATA = "metadata"
    CONFIG = "config"
    UNKNOWN = "unknown"


class RocketMQAnalysis(BaseModel):
    """RocketMQ问题分析结果"""
    problem_scope: ProblemScope = Field(description="问题范围：对象级或组件级")
    suspected_object: Optional[SuspectedObject] = Field(
        default=None, description="疑似对象：producer/consumer/group/topic/null"
    )
    suspected_component: Optional[SuspectedComponent] = Field(
        default=None, description="疑似组件：namesrv/broker/proxy/null"
    )
    key_evidence: List[str] = Field(default_factory=list, description="关键证据列表")
    suspected_root: RootCause = Field(description="怀疑的根因")
    recommended_next_actions: List[str] = Field(
        default_factory=list, description="建议的下一步操作"
    )
    confidence: float = Field(ge=0.0, le=1.0, description="分析置信度")


# 保持向后兼容的BrokerLogAnalysis模型
class BrokerLogAnalysis(BaseModel):
    is_isr_related: bool
    reason: str
    suspected_root: str = Field(
        pattern="^(network|disk_io|jvm|controller|slave_lag|unknown)$"
    )
    next_state: str = Field(
        pattern="^(CHECK_CONTROLLER|CHECK_NETWORK|CHECK_DISK_IO|CHECK_JVM|CHECK_SLAVE_PROGRESS|NO_ISR_ISSUE|NEED_COMMAND_LINE)$"
    )
    confidence: float
    needs_command_line: bool = Field(
        description="是否需要命令行交互来进一步诊断问题"
    )
    command_line_suggestions: str = Field(
        default="",
        description="建议执行的命令行命令或诊断步骤"
    )
