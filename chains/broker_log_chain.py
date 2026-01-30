from langchain.chains import LLMChain
from langchain.prompts import PromptTemplate

BROKER_LOG_PROMPT = PromptTemplate(
    input_variables=["broker_log"],
    template="""
    你是 Apache RocketMQ 专家，下面是 broker.log：
    {broker_log}
    请判断 ISR 异常并输出 JSON：
    {
    "is_isr_related": true | false,
    "reason": "...",
    "suspected_root": "network|disk_io|jvm|controller|slave_lag|unknown",
    "next_state": "CHECK_CONTROLLER|CHECK_NETWORK|CHECK_DISK_IO|CHECK_JVM|CHECK_SLAVE_PROGRESS|NO_ISR_ISSUE",
    "confidence": 0.0
    }
    """,
)


def build_chain(llm):
    return LLMChain(llm=llm, prompt=BROKER_LOG_PROMPT)
