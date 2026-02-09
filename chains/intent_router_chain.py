import json
from typing import Dict, Any

from langchain_core.prompts import PromptTemplate

from tools.tool_registry import (
    TOOL_LIST_BROKER_PODS,
    TOOL_LIST_NAMESRV_PODS,
    TOOL_LIST_PROXY_PODS,
    TOOL_SEND_FAIL_CHECK,
    TOOL_LIST_TOPICS,
    TOOL_GET_BROKER_CONFIG,
    build_tools_prompt,
    list_admin_tool_names,
    list_tool_names,
)

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
    tool_list_text = build_tools_prompt()
    intents_list = list_tool_names() + list_admin_tool_names() + ["unknown"]
    intents_lines = "\n".join([f"- {name}" for name in intents_list])
    template = f"""
        {{base_prompt}}

        {tool_list_text}

        你是 RocketMQ 排障意图路由器。请根据用户输入识别要执行的操作意图。
        
        可选 intents：
        {intents_lines}
        
        要求：
        - 如果用户要求“全部/都需要/全量”，返回所有可执行 intents（除 unknown）。
        - 如果缺少必要参数（如 namespace/topic），也要返回 intents，但在参数里留空。
        - 只输出 JSON，不要输出解释性文字。
                
        输出 JSON 格式：
        {{{{
          "intents": ["{TOOL_LIST_BROKER_PODS}"],
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
        """.replace("{TOOL_LIST_BROKER_PODS}", TOOL_LIST_BROKER_PODS)

    prompt = PromptTemplate.from_template(template)
    try:
        chain = prompt | llm
        resp = chain.invoke({"user_msg": user_msg, "base_prompt": base_prompt})
    except TypeError:
        # Fallback for dummy/test LLM without Runnable support
        resp = llm.invoke({"user_msg": user_msg, "base_prompt": base_prompt})
    data = _extract_json(resp.content)

    # rule-based fallback
    intents = data.get("intents", []) or []
    lower = user_msg.lower()
    if not intents:
        intents = []
    if ("broker" in lower or "broker" in user_msg) and ("pod" in lower or "pod" in user_msg or "列出" in user_msg):
        if TOOL_LIST_BROKER_PODS not in intents:
            intents.append(TOOL_LIST_BROKER_PODS)
    if ("namesrv" in lower or "nameserver" in lower or "namesrv" in user_msg) and (
            "pod" in lower or "pod" in user_msg or "列出" in user_msg):
        if TOOL_LIST_NAMESRV_PODS not in intents:
            intents.append(TOOL_LIST_NAMESRV_PODS)
    if ("proxy" in lower or "proxy" in user_msg) and ("pod" in lower or "pod" in user_msg or "列出" in user_msg):
        if TOOL_LIST_PROXY_PODS not in intents:
            intents.append(TOOL_LIST_PROXY_PODS)

    if "topic" in lower and ("列出" in user_msg or "list" in lower or "all" in lower):
        if TOOL_LIST_TOPICS not in intents:
            intents.append(TOOL_LIST_TOPICS)

    if "broker" in lower and ("配置" in user_msg or "config" in lower):
        if TOOL_GET_BROKER_CONFIG not in intents:
            intents.append(TOOL_GET_BROKER_CONFIG)

    if "消费进度" in user_msg or ("进度" in user_msg and "消费" in user_msg):
        if "consumerProgress" not in intents:
            intents.append("consumerProgress")

    # Admin tool keyword routing (minimal but direct)
    admin_keyword_map = {
        "topicroute": "topicRoute",
        "topic 路由": "topicRoute",
        "路由信息": "topicRoute",
        "topic status": "topicStatus",
        "topic 状态": "topicStatus",
        "topicclusterlist": "topicClusterList",
        "topic 集群": "topicClusterList",
        "主题集群": "topicClusterList",
        "brokerstatus": "brokerStatus",
        "broker 状态": "brokerStatus",
        "broker 运行状态": "brokerStatus",
        "querymsgbyid": "queryMsgById",
        "msg id": "queryMsgById",
        "消息id": "queryMsgById",
        "querymsgbykey": "queryMsgByKey",
        "msg key": "queryMsgByKey",
        "消息key": "queryMsgByKey",
        "uniquekey": "queryMsgByUniqueKey",
        "唯一key": "queryMsgByUniqueKey",
        "offset": "queryMsgByOffset",
        "偏移量": "queryMsgByOffset",
        "trace": "queryMsgTraceById",
        "消息轨迹": "queryMsgTraceById",
        "printmsg": "printMsg",
        "打印消息": "printMsg",
        "brokerconsumestats": "brokerConsumeStats",
        "broker 消费统计": "brokerConsumeStats",
        "browsemessage": "browseMessage",
        "浏览消息": "browseMessage",
        "producerconnection": "producerConnection",
        "生产者连接": "producerConnection",
        "consumerconnection": "consumerConnection",
        "消费者连接": "consumerConnection",
        "consumerprogress": "consumerProgress",
        "消费进度": "consumerProgress",
        "consumerstatus": "consumerStatus",
        "消费者状态": "consumerStatus",
        "clusterlist": "clusterList",
        "集群列表": "clusterList",
        "statsall": "statsAll",
        "tps 统计": "statsAll",
        "allocatemq": "allocateMQ",
        "分配mq": "allocateMQ",
        "checkmsgsendrt": "checkMsgSendRT",
        "发送rt": "checkMsgSendRT",
        "clusterrt": "clusterRT",
        "集群rt": "clusterRT",
        "getnamesrvconfig": "getNamesrvConfig",
        "namesrv 配置": "getNamesrvConfig",
        "getbrokerconfig": "getBrokerConfig",
        "broker 配置": "getBrokerConfig",
        "getconsumerconfig": "getConsumerConfig",
        "消费组配置": "getConsumerConfig",
        "clusteraclconfigversion": "clusterAclConfigVersion",
        "acl 配置版本": "clusterAclConfigVersion",
        "hastatus": "haStatus",
        "ha 状态": "haStatus",
        "getsyncstateset": "getSyncStateSet",
        "同步状态": "getSyncStateSet",
        "getbrokerepoch": "getBrokerEpoch",
        "broker epoch": "getBrokerEpoch",
        "getcontrollermetadata": "getControllerMetaData",
        "controller 元数据": "getControllerMetaData",
        "getcontrollerconfig": "getControllerConfig",
        "controller 配置": "getControllerConfig",
        "getacl": "getAcl",
        "获取acl": "getAcl",
        "listacl": "listAcl",
        "列出acl": "listAcl",
        "consumer": "consumer",
        "querytopicrouterules": "topicRoute",
        "路由规则": "topicRoute",
        "getconsumeroffset": "getConsumerOffset",
        "消费offset": "getConsumerOffset",
        "消费进度": "getConsumerOffset",
    }
    for key, cmd in admin_keyword_map.items():
        if key in lower:
            if cmd not in intents:
                intents.append(cmd)

    # Knowledge-only intent (no tool execution needed)
    info_keywords = [
        "是什么", "概念", "原理", "如何", "怎么", "教程", "指南", "介绍", "文档", "说明", "示例",
        "best practice", "最佳实践", "配置说明", "参数说明", "优点", "缺点",
    ]
    if any(k in user_msg for k in info_keywords):
        data["info_only"] = True
        # If user is only asking for knowledge, keep intents empty
        if not intents:
            data["intents"] = []
            return data
    # 发送消息失败 -> 排查 topic 是否存在
    if "发送" in user_msg and "失败" in user_msg:
        if TOOL_SEND_FAIL_CHECK not in intents:
            intents.append(TOOL_SEND_FAIL_CHECK)

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
    if not data["intents"]:
        data["info_only"] = True
    if "rocketmq" in lower and not data.get("keyword"):
        data["keyword"] = "rocketmq5"
    return data
