import json
from typing import Dict, Any

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
- list_namesrv_pods
- list_proxy_pods
- send_fail_check
- unknown
        
        要求：
        - 如果用户要求“全部/都需要/全量”，返回所有可执行 intents（除 unknown）。
        - 如果缺少必要参数（如 namespace/topic），也要返回 intents，但在参数里留空。
        - 只输出 JSON，不要输出解释性文字。
        - 用户提到“查询/获取/查看 broker 配置”时，必须包含 `broker_admin_config`。
        
输出 JSON 格式：
{{{{
  "intents": ["list_broker_pods"],
  "keyword": "rocketmq5",
  "instance_id": "",
  "namespace": "",
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
    try:
        chain = prompt | llm
        resp = chain.invoke({"user_msg": user_msg})
    except TypeError:
        # Fallback for dummy/test LLM without Runnable support
        resp = llm.invoke({"user_msg": user_msg})
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
    if ("broker" in lower or "broker" in user_msg) and ("pod" in lower or "pod" in user_msg or "列出" in user_msg):
        if "list_broker_pods" not in intents:
            intents.append("list_broker_pods")
    if ("namesrv" in lower or "nameserver" in lower or "namesrv" in user_msg) and ("pod" in lower or "pod" in user_msg or "列出" in user_msg):
        if "list_namesrv_pods" not in intents:
            intents.append("list_namesrv_pods")
    if ("proxy" in lower or "proxy" in user_msg) and ("pod" in lower or "pod" in user_msg or "列出" in user_msg):
        if "list_proxy_pods" not in intents:
            intents.append("list_proxy_pods")

    # 发送消息失败 -> 排查 topic 是否存在
    if "发送" in user_msg and "失败" in user_msg:
        if "send_fail_check" not in intents:
            intents.append("send_fail_check")

    # 提取 instance_id / namespace / topic
    import re
    if not data.get("instance_id"):
        m = re.search(r"(rmq-[\\w-]+|rocketmq-[\\w-]+)", user_msg, re.IGNORECASE)
        if m:
            data["instance_id"] = m.group(1)
    if not data.get("namespace"):
        m = re.search(r"(MQ_INT[\\w-]+)", user_msg, re.IGNORECASE)
        if m:
            data["namespace"] = m.group(1)
        else:
            m = re.search(r"namespace\\s*[:=]\\s*([\\w-]+)", user_msg, re.IGNORECASE)
            if m:
                data["namespace"] = m.group(1)
    if not data.get("topic"):
        m = re.search(r"topic\\s*[:=]\\s*([\\w.-]+)", user_msg, re.IGNORECASE)
        if m:
            data["topic"] = m.group(1)

    data["intents"] = intents or data.get("intents", [])
    if "rocketmq" in lower and not data.get("keyword"):
        data["keyword"] = "rocketmq5"
    return data
