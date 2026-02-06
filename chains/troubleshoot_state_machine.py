from typing import Dict, Any, List, TypedDict, Optional

from langgraph.graph import END
from langgraph.graph.state import StateGraph

from chains.intent_router_chain import run_intent_router_chain
from tools.tool_registry import (
    TOOL_LIST_BROKER_PODS,
    TOOL_LIST_NAMESRV_PODS,
    TOOL_LIST_PROXY_PODS,
    TOOL_SEND_FAIL_CHECK,
    TOOL_LIST_TOPICS,
    TOOL_GET_BROKER_CONFIG,
    list_admin_tool_names,
    get_admin_required,
    run_tool,
)


class TSState(TypedDict, total=False):
    """状态机上下文：在各个节点之间传递的共享数据结构。"""
    # 用户原始输入，供意图识别与兜底解析使用
    user_msg: str
    # 合并后的提示词内容（例如 base.prompt + 业务提示）
    prompt_content: str
    # LLM 实例（由 main.py 注入）
    llm: Any
    # 意图识别的原始输出，包含 intents 与解析到的参数
    intent_data: Dict[str, Any]
    # 解析后的意图列表（便于路由与执行）
    intents: List[str]
    # 工具执行结果列表（每项通常包含 action/result）
    results: List[Dict[str, Any]]
    # 待执行的工具队列（节点名称）
    tool_queue: List[str]
    # 当前待执行工具
    next_tool: str
    # 错误信息（如输入信息不足、执行失败等）
    error: str
    # 需要补充的参数提示
    missing_params: List[str]
    # 缺参对应的命令
    missing_for_tool: str
    # 用户选择跳过的参数
    skipped_params: List[str]
    # 真实拼接后的 topic/group
    resolved_real_topic: str
    resolved_real_group: str
    # 关联实例与命名空间
    resolved_instance_id: str
    resolved_namespace: str


def _intent_node(state: TSState) -> TSState:
    """意图识别节点：调用 LLM 解析用户输入，输出 intents 与解析字段。"""
    if state.get("intent_data"):
        intent_data = state.get("intent_data", {})
        intents = intent_data.get("intents", []) or []
    else:
        llm = state["llm"]
        user_msg = state["user_msg"]
        prompt_content = state.get("prompt_content", "")
        intent_data = run_intent_router_chain(llm, user_msg, base_prompt=prompt_content)
        intents = intent_data.get("intents", []) or []
    tool_queue = _build_tool_queue(intents)
    return {**state, "intent_data": intent_data, "intents": intents, "tool_queue": tool_queue}


def _dispatch_node(state: TSState) -> TSState:
    """调度节点：从 tool_queue 里取出一个待执行工具。"""
    from datetime import datetime
    queue = list(state.get("tool_queue", []))
    if not queue:
        ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        print(f"[{ts}] [Step 2] dispatch: empty tool_queue")
        return {**state, "next_tool": ""}
    next_tool = queue.pop(0)
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{ts}] [Step 2] dispatch: next_tool={next_tool}, remain={queue}")
    return {**state, "tool_queue": queue, "next_tool": next_tool}


def _list_broker_pods_node(state: TSState) -> TSState:
    """工具节点：调用 kubectl 列出所有 broker pods。"""
    results = list(state.get("results", []))
    res = run_tool(TOOL_LIST_BROKER_PODS, execute=True)
    results.append({"action": res.get("command"), "result": res})
    return {**state, "results": results}


def _list_namesrv_pods_node(state: TSState) -> TSState:
    """工具节点：调用 kubectl 列出所有 namesrv pods。"""
    results = list(state.get("results", []))
    res = run_tool(TOOL_LIST_NAMESRV_PODS, execute=True)
    results.append({"action": res.get("command"), "result": res})
    return {**state, "results": results}


def _list_proxy_pods_node(state: TSState) -> TSState:
    """工具节点：调用 kubectl 列出所有 proxy pods。"""
    results = list(state.get("results", []))
    res = run_tool(TOOL_LIST_PROXY_PODS, execute=True)
    results.append({"action": res.get("command"), "result": res})
    return {**state, "results": results}


def _list_topics_node(state: TSState) -> TSState:
    """工具节点：调用 mqadmin topicList 列出全部主题。"""
    intent_data = state.get("intent_data", {})
    k8s_ns = "tce"
    keyword = intent_data.get("topic") or intent_data.get("keyword")
    res = run_tool(TOOL_LIST_TOPICS, k8s_namespace=k8s_ns, keyword=keyword, execute=True)
    results = list(state.get("results", []))
    results.append({"action": res.get("command"), "result": res})
    return {**state, "results": results}


def _get_broker_config_node(state: TSState) -> TSState:
    """工具节点：调用 mqadmin getBrokerConfig 查询 Broker 配置。"""
    intent_data = state.get("intent_data", {})
    broker_addr = intent_data.get("broker") or intent_data.get("broker_addr")
    if not broker_addr:
        return {**state, "error": "缺少 broker 地址（brokerAddr）。"}
    k8s_ns = "tce"
    admin_subcommand = f"getBrokerConfig -b {broker_addr}"
    res = run_tool(
        TOOL_GET_BROKER_CONFIG,
        k8s_namespace=k8s_ns,
        admin_subcommand=admin_subcommand,
        execute=True,
    )
    results = list(state.get("results", []))
    results.append({"action": res.get("command"), "result": res})
    return {**state, "results": results}


def _send_fail_check_node(state: TSState) -> TSState:
    """工具节点：根据实例类型构造 topic 查询命令，判断 topic 是否存在。"""

    user_msg = state["user_msg"]
    intent_data = state.get("intent_data", {})

    def _extract_kv(text, key):
        import re
        patterns = [
            rf"{key}\s*=\s*([\w\-\.]+)",
            rf"{key}\s*:\s*([\w\-\.]+)",
        ]
        for p in patterns:
            m = re.search(p, text, re.IGNORECASE)
            if m:
                return m.group(1)
        return None

    instance_id = intent_data.get("instance_id") or _extract_kv(user_msg, "instance_id")
    rocketmq_namespace = intent_data.get("namespace") or _extract_kv(user_msg, "namespace")
    topic = intent_data.get("topic") or _extract_kv(user_msg, "topic")

    if not instance_id or not topic:
        return {**state, "error": "缺少实例id或topic，无法继续排查。"}

    instance_lower = instance_id.lower()
    if instance_lower.startswith("rocketmq-"):
        if not rocketmq_namespace:
            return {**state, "error": "缺少 RocketMQ 命名空间（MQ_INT 开头）。"}
        k8s_ns = "tce"
        real_topic = f"{rocketmq_namespace}%{topic}"
    else:
        suffix = instance_id.split("-", 1)[1]
        k8s_ns = f"rmqnamesrv-{suffix}"
        real_topic = f"{instance_id.replace('-', '')}%{topic}"

    res = run_tool(TOOL_SEND_FAIL_CHECK, k8s_namespace=k8s_ns, real_topic=real_topic, execute=True)
    results = list(state.get("results", []))
    results.append({"action": res.get("command"), "result": res})
    return {**state, "results": results}


def _route_node(state: TSState) -> str:
    """路由节点：根据 next_tool 决定执行哪个工具节点。"""
    next_tool = state.get("next_tool", "")
    if next_tool == TOOL_SEND_FAIL_CHECK:
        return TOOL_SEND_FAIL_CHECK
    if next_tool == TOOL_LIST_BROKER_PODS:
        return TOOL_LIST_BROKER_PODS
    if next_tool == TOOL_LIST_NAMESRV_PODS:
        return TOOL_LIST_NAMESRV_PODS
    if next_tool == TOOL_LIST_PROXY_PODS:
        return TOOL_LIST_PROXY_PODS
    if next_tool == TOOL_LIST_TOPICS:
        return TOOL_LIST_TOPICS
    if next_tool == TOOL_GET_BROKER_CONFIG:
        return TOOL_GET_BROKER_CONFIG
    if next_tool in list_admin_tool_names():
        return "admin_tool"
    return "done"


def build_troubleshoot_graph() -> StateGraph:
    """构建 LangGraph 状态机：intent -> dispatch -> tool -> dispatch -> END。"""
    graph = StateGraph(TSState)
    graph.add_node("intent", _intent_node)
    graph.add_node("dispatch", _dispatch_node)
    graph.add_node(TOOL_LIST_BROKER_PODS, _list_broker_pods_node)
    graph.add_node(TOOL_LIST_NAMESRV_PODS, _list_namesrv_pods_node)
    graph.add_node(TOOL_LIST_PROXY_PODS, _list_proxy_pods_node)
    graph.add_node(TOOL_LIST_TOPICS, _list_topics_node)
    graph.add_node(TOOL_GET_BROKER_CONFIG, _get_broker_config_node)
    graph.add_node("admin_tool", _admin_tool_node)
    graph.add_node(TOOL_SEND_FAIL_CHECK, _send_fail_check_node)

    graph.set_entry_point("intent")
    graph.add_edge("intent", "dispatch")
    graph.add_conditional_edges("dispatch", _route_node, {
        TOOL_LIST_BROKER_PODS: TOOL_LIST_BROKER_PODS,
        TOOL_LIST_NAMESRV_PODS: TOOL_LIST_NAMESRV_PODS,
        TOOL_LIST_PROXY_PODS: TOOL_LIST_PROXY_PODS,
        TOOL_LIST_TOPICS: TOOL_LIST_TOPICS,
        TOOL_GET_BROKER_CONFIG: TOOL_GET_BROKER_CONFIG,
        TOOL_SEND_FAIL_CHECK: TOOL_SEND_FAIL_CHECK,
        "admin_tool": "admin_tool",
        "done": END,
    })
    graph.add_edge(TOOL_LIST_BROKER_PODS, "dispatch")
    graph.add_edge(TOOL_LIST_NAMESRV_PODS, "dispatch")
    graph.add_edge(TOOL_LIST_PROXY_PODS, "dispatch")
    graph.add_edge(TOOL_LIST_TOPICS, "dispatch")
    graph.add_edge(TOOL_GET_BROKER_CONFIG, "dispatch")
    graph.add_edge("admin_tool", "dispatch")
    graph.add_edge(TOOL_SEND_FAIL_CHECK, "dispatch")
    return graph


def _build_tool_queue(intents: List[str]) -> List[str]:
    """将 intents 变为工具执行队列，保证每个工具都有独立节点。"""
    queue: List[str] = []
    if TOOL_SEND_FAIL_CHECK in intents:
        queue.append(TOOL_SEND_FAIL_CHECK)
    if TOOL_LIST_BROKER_PODS in intents:
        queue.append(TOOL_LIST_BROKER_PODS)
    if TOOL_LIST_NAMESRV_PODS in intents:
        queue.append(TOOL_LIST_NAMESRV_PODS)
    if TOOL_LIST_PROXY_PODS in intents:
        queue.append(TOOL_LIST_PROXY_PODS)
    if TOOL_LIST_TOPICS in intents:
        queue.append(TOOL_LIST_TOPICS)
    if TOOL_GET_BROKER_CONFIG in intents:
        queue.append(TOOL_GET_BROKER_CONFIG)
    admin_tools = set(list_admin_tool_names())
    for intent in intents:
        if intent in admin_tools and intent not in queue:
            queue.append(intent)
    return queue


def _admin_tool_node(state: TSState) -> TSState:
    """通用 admin 工具节点：执行任意 mqadmin 命令。"""
    next_tool = state.get("next_tool", "")
    intent_data = state.get("intent_data", {})
    k8s_ns = intent_data.get("k8s_namespace") or "tce"
    admin_args = dict(intent_data.get("admin_args") or {})
    raw_args = intent_data.get("raw_args")

    user_msg = state.get("user_msg", "")

    def _extract_kv(text: str, key: str) -> Optional[str]:
        import re
        patterns = [
            rf"{key}\s*=\s*([\\w\\-\\.:%]+)",
            rf"{key}\s*:\s*([\\w\\-\\.:%]+)",
        ]
        for p in patterns:
            m = re.search(p, text, re.IGNORECASE)
            if m:
                return m.group(1)
        return None

    instance_id = intent_data.get("instance_id") or _extract_kv(user_msg, "instance_id")
    topic = (
        intent_data.get("topic")
        or _extract_kv(user_msg, "topic")
        or _extract_kv(user_msg, "traceTopic")
        or _extract_kv(user_msg, "lmq")
    )
    rocketmq_ns = intent_data.get("namespace") or _extract_kv(user_msg, "namespace")
    group = (
        intent_data.get("group")
        or _extract_kv(user_msg, "group")
        or _extract_kv(user_msg, "consumerGroup")
        or _extract_kv(user_msg, "producerGroup")
    )
    broker_addr = (
        intent_data.get("broker")
        or intent_data.get("broker_addr")
        or _extract_kv(user_msg, "broker")
        or _extract_kv(user_msg, "brokerAddr")
    )
    msg_id = _extract_kv(user_msg, "msgId") or _extract_kv(user_msg, "msg_id") or _extract_kv(user_msg, "messageId")
    msg_key = _extract_kv(user_msg, "msgKey") or _extract_kv(user_msg, "key") or _extract_kv(user_msg, "messageKey")
    queue_id = _extract_kv(user_msg, "queueId") or _extract_kv(user_msg, "queue_id")
    offset = _extract_kv(user_msg, "offset")
    cluster = _extract_kv(user_msg, "cluster") or _extract_kv(user_msg, "clusterName")
    client_id = _extract_kv(user_msg, "clientId") or _extract_kv(user_msg, "client_id")
    unit_name = _extract_kv(user_msg, "unitName") or _extract_kv(user_msg, "unit")
    instant = _extract_kv(user_msg, "instant")
    lmq = _extract_kv(user_msg, "lmq")
    trace_topic = _extract_kv(user_msg, "traceTopic")
    controller_addr = _extract_kv(user_msg, "controllerAddress") or _extract_kv(user_msg, "controller")
    queue_count = _extract_kv(user_msg, "count")
    begin_ts = _extract_kv(user_msg, "beginTimestamp") or _extract_kv(user_msg, "begin")
    end_ts = _extract_kv(user_msg, "endTimestamp") or _extract_kv(user_msg, "end")

    # Topic / group queries require instance_id; use it to pick k8s namespace
    requires_instance = next_tool in (
        "topicRoute",
        "topicStatus",
        "topicClusterList",
        "topicList",
        "queryMsgById",
        "queryMsgByKey",
        "queryMsgByUniqueKey",
        "queryMsgByOffset",
        "queryMsgTraceById",
        "printMsg",
        "producerConnection",
        "consumerConnection",
        "consumerProgress",
        "consumerStatus",
        "getConsumerConfig",
        "getConsumerOffset",
        "getConsumerOffsetByGroup",
    )
    if requires_instance:
        if not instance_id:
            return {
                **state,
                "error": f"{next_tool} 缺少必要参数: instance_id",
                "missing_params": ["instance_id"],
            }
        instance_lower = instance_id.lower()
        if instance_lower.startswith("rmq-"):
            suffix = instance_id.split("-", 1)[1]
            k8s_ns = f"rmqnamesrv-{suffix}"
        else:
            k8s_ns = "tce"
            if not rocketmq_ns:
                return {
                    **state,
                    "error": f"{next_tool} 缺少必要参数: namespace",
                    "missing_params": ["namespace"],
                }

    def _normalize_topic(raw_topic: Optional[str]) -> Optional[str]:
        if not raw_topic or not instance_id:
            return raw_topic
        instance_lower = instance_id.lower()
        if instance_lower.startswith("rmq-"):
            return f"{instance_id.replace('-', '')}%{raw_topic}"
        if instance_lower.startswith("rocketmq-") and rocketmq_ns:
            return f"{rocketmq_ns}%{raw_topic}"
        return raw_topic

    def _normalize_group(raw_group: Optional[str]) -> Optional[str]:
        if not raw_group or not instance_id:
            return raw_group
        instance_lower = instance_id.lower()
        if instance_lower.startswith("rmq-"):
            return f"{instance_id.replace('-', '')}%{raw_group}"
        if instance_lower.startswith("rocketmq-") and rocketmq_ns:
            return f"{rocketmq_ns}%{raw_group}"
        return raw_group

    real_topic = _normalize_topic(topic)
    real_group = _normalize_group(group)

    def _force_arg(short_flag: str, value: Optional[str], long_flags: Optional[List[str]] = None) -> None:
        if not value:
            return
        admin_args[short_flag] = value
        if long_flags:
            for lf in long_flags:
                admin_args.pop(lf, None)

    if next_tool in ("topicRoute", "topicStatus", "topicClusterList"):
        _force_arg("-t", real_topic, ["--topic"])
    if next_tool in ("producerConnection",):
        _force_arg("-g", real_group, ["--producerGroup"])
        _force_arg("-t", real_topic, ["--topic"])
    if next_tool in ("consumerConnection", "consumerStatus", "consumerProgress"):
        _force_arg("-g", real_group, ["--consumerGroup", "--group"])
    if next_tool in ("brokerStatus", "brokerConsumeStats", "getAcl", "listAcl", "getConsumerOffset"):
        if broker_addr and "-b" not in admin_args and "--brokerAddr" not in admin_args:
            admin_args["-b"] = broker_addr
    if next_tool in ("queryMsgById", "queryMsgByUniqueKey"):
        if msg_id and "-i" not in admin_args and "--msgId" not in admin_args:
            admin_args["-i"] = msg_id
        _force_arg("-t", real_topic, ["--topic"])
        _force_arg("-g", real_group, ["--consumerGroup", "--group"])
        if client_id and "-d" not in admin_args and "--clientId" not in admin_args:
            admin_args["-d"] = client_id
        if unit_name and "-u" not in admin_args and "--unitName" not in admin_args:
            admin_args["-u"] = unit_name
    if next_tool == "queryMsgByKey":
        if msg_key and "-k" not in admin_args and "--msgKey" not in admin_args:
            admin_args["-k"] = msg_key
        _force_arg("-t", real_topic, ["--topic"])
        if begin_ts and "-b" not in admin_args and "--beginTimestamp" not in admin_args:
            admin_args["-b"] = begin_ts
        if end_ts and "-e" not in admin_args and "--endTimestamp" not in admin_args:
            admin_args["-e"] = end_ts
    if next_tool == "queryMsgByOffset":
        if queue_id and "-i" not in admin_args and "--queueId" not in admin_args:
            admin_args["-i"] = queue_id
        if offset and "-o" not in admin_args and "--offset" not in admin_args:
            admin_args["-o"] = offset
        _force_arg("-t", real_topic, ["--topic"])
        if broker_addr and "-b" not in admin_args and "--brokerName" not in admin_args:
            admin_args["-b"] = broker_addr
    if next_tool == "queryMsgTraceById":
        if msg_id and "-i" not in admin_args:
            admin_args["-i"] = msg_id
        if trace_topic and "-t" not in admin_args and "--traceTopic" not in admin_args:
            admin_args["-t"] = trace_topic
        if begin_ts and "-b" not in admin_args and "--beginTimestamp" not in admin_args:
            admin_args["-b"] = begin_ts
        if end_ts and "-e" not in admin_args and "--endTimestamp" not in admin_args:
            admin_args["-e"] = end_ts
    if next_tool == "printMsg":
        _force_arg("-t", real_topic, ["--topic"])
        if begin_ts and "-b" not in admin_args:
            admin_args["-b"] = begin_ts
        if end_ts and "-e" not in admin_args:
            admin_args["-e"] = end_ts
    if next_tool == "brokerConsumeStats":
        if broker_addr and "-b" not in admin_args:
            admin_args["-b"] = broker_addr
    if next_tool == "browseMessage":
        _force_arg("-t", real_topic, ["--topic"])
        if lmq and "-l" not in admin_args:
            admin_args["-l"] = lmq
        if instant and "-i" not in admin_args:
            admin_args["-i"] = instant
        if queue_count and "-c" not in admin_args:
            admin_args["-c"] = queue_count
    if next_tool == "getConsumerConfig":
        _force_arg("-g", real_group, ["--groupName"])
    if next_tool in ("clusterAclConfigVersion", "haStatus", "getSyncStateSet", "getBrokerEpoch"):
        if cluster and "-c" not in admin_args and "--clusterName" not in admin_args:
            admin_args["-c"] = cluster
    if next_tool in ("getControllerMetaData", "getControllerConfig", "getSyncStateSet"):
        if controller_addr and "-a" not in admin_args and "--controllerAddress" not in admin_args:
            admin_args["-a"] = controller_addr
    if next_tool in ("getConsumerOffset",):
        _force_arg("-g", real_group, ["--group"])
        _force_arg("-t", real_topic, ["--topic"])
        if queue_id and "-q" not in admin_args:
            admin_args["-q"] = queue_id
        if broker_addr and "-b" not in admin_args:
            admin_args["-b"] = broker_addr
    if next_tool in ("getConsumerOffsetByGroup",):
        _force_arg("-g", real_group, ["--group"])
        if broker_addr and "-b" not in admin_args:
            admin_args["-b"] = broker_addr


    required_flags = get_admin_required(next_tool)
    skipped = set(state.get("skipped_params", []) or [])
    missing = []
    for flag in required_flags:
        if flag in skipped:
            continue
        if flag not in admin_args:
            missing.append(flag)
    if missing:
        return {
            **state,
            "error": f"{next_tool} 缺少必要参数: {', '.join(missing)}",
            "missing_params": missing,
            "missing_for_tool": next_tool,
        }

    state = {**state}
    if real_topic or real_group:
        state["resolved_real_topic"] = real_topic or ""
        state["resolved_real_group"] = real_group or ""
        state["resolved_instance_id"] = instance_id or ""
        state["resolved_namespace"] = rocketmq_ns or ""
    res = run_tool(
        next_tool,
        k8s_namespace=k8s_ns,
        admin_args=admin_args,
        raw_args=raw_args,
        execute=True,
    )
    results = list(state.get("results", []))
    results.append({"action": res.get("command"), "result": res})
    return {**state, "results": results}
