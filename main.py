import json
import os

from langchain.chat_models import ChatOpenAI

from chains.broker_log_chain import build_chain
from models import BrokerLogAnalysis
from router import route

# 从环境变量获取OpenAI API密钥
openai_api_key = os.getenv("OPENAI_API_KEY")
if not openai_api_key:
    raise ValueError("请设置环境变量 OPENAI_API_KEY，或者直接在代码中传递 openai_api_key 参数")

llm = ChatOpenAI(model="gpt-4o-mini", temperature=0, openai_api_key=openai_api_key)

broker_log = """
WARN remove broker from ISR, brokerId=2, reason=heartbeat timeout
INFO broker 2 not in sync state
"""

chain = build_chain(llm)
raw = chain.invoke({"broker_log": broker_log})

analysis = BrokerLogAnalysis.parse_raw(raw["text"])

next_step = route(analysis)
result = next_step({"analysis": analysis.dict()})

print(json.dumps({"analysis": analysis.dict(), "result": result}, indent=2))
