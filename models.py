from pydantic import BaseModel, Field


class BrokerLogAnalysis(BaseModel):
    is_isr_related: bool
    reason: str
    suspected_root: str = Field(
        pattern="^(network|disk_io|jvm|controller|slave_lag|unknown)$"
    )
    next_state: str = Field(
        pattern="^(CHECK_CONTROLLER|CHECK_NETWORK|CHECK_DISK_IO|CHECK_JVM|CHECK_SLAVE_PROGRESS|NO_ISR_ISSUE)$"
    )
    confidence: float
