from typing import Dict, Any
import json

from langchain_core.prompts import PromptTemplate


def _extract_json(text: str) -> Dict[str, Any]:
    try:
        return json.loads(text)
    except Exception:
        start = text.find("{")
        end = text.rfind("}")
        if start != -1 and end != -1 and end > start:
            return json.loads(text[start:end + 1])
        return {}


def run_intent_router_chain(llm, user_msg: str, base_prompt: str = "") -> Dict[str, Any]:
    """
    使用 LLM 识别用户意图，输出结构化 intents。
    """
    template = f"""
{base_prompt}

你是 RocketMQ 排障意图路由器。请根据用户输入识别要执行的操作意图。

可选 intents：
- list_broker_pods
- list_rocketmq_services
- broker_logs
- namesrv_logs
- broker_admin_status
- broker_admin_config
- namesrv_admin_cluster
- namesrv_admin_topic_route
- broker_jvm
- namesrv_jvm
- unknown

要求：
- 如果用户要求“全部/都需要/全量”，返回所有可执行 intents（除 unknown）。
- 如果缺少必要参数（如 namespace/topic），也要返回 intents，但在参数里留空。
- 只输出 JSON，不要输出解释性文字。
- 用户提到“查询/获取/查看 broker 配置”时，必须包含 `broker_admin_config`。

输出 JSON 格式：
{{{{
  "intents": ["list_broker_pods"],
  "namespace": "rocketmq5",
  "topic": "",
  "group": "",
  "broker": "",
  "namesrv": "",
  "pod": "",
  "container": ""
}}}}

用户输入：
{{user_msg}}
"""

    prompt = PromptTemplate.from_template(template)
    chain = prompt | llm
    resp = chain.invoke({"user_msg": user_msg})
    data = _extract_json(resp.content)

    # rule-based fallback
    intents = data.get("intents", []) or []
    lower = user_msg.lower()
    if not intents:
        intents = []
    if "broker" in lower and ("配置" in user_msg or "config" in lower):
        if "broker_admin_config" not in intents:
            intents.append("broker_admin_config")
    if "broker" in lower and ("状态" in user_msg or "status" in lower):
        if "broker_admin_status" not in intents:
            intents.append("broker_admin_status")
    if "topic" in lower and ("路由" in user_msg or "route" in lower):
        if "namesrv_admin_topic_route" not in intents:
            intents.append("namesrv_admin_topic_route")
    if "namesrv" in lower or "nameserver" in lower:
        if "namesrv_admin_cluster" not in intents:
            intents.append("namesrv_admin_cluster")

    data["intents"] = intents or data.get("intents", [])
    return data
