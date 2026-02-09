from typing import Dict, Any, List, TypedDict, Optional

from langgraph.graph import END
from langgraph.graph.state import StateGraph

from chains.intent_router_chain import run_intent_router_chain
from skills.skills_loader import load_skills_from_dir
from tools.tool_registry import (
    TOOL_LIST_BROKER_PODS,
    TOOL_LIST_NAMESRV_PODS,
    TOOL_LIST_PROXY_PODS,
    TOOL_SEND_FAIL_CHECK,
    TOOL_LIST_TOPICS,
    TOOL_GET_BROKER_CONFIG,
    list_admin_tool_names,
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
    # skills 内容（仅在工具调用前加载）
    skills_content: str


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
    if not state.get("skills_content"):
        skills_content = load_skills_from_dir()
        if skills_content:
            print(f"[{ts}] [Step 2] Skills loaded before tool call")
        return {**state, "tool_queue": queue, "next_tool": next_tool, "skills_content": skills_content}
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
    """工具节点：调用 MCP admin API 列出全部主题。"""
    res = run_tool(TOOL_LIST_TOPICS)
    results = list(state.get("results", []))
    results.append({"action": TOOL_LIST_TOPICS, "result": res})
    return {**state, "results": results}


def _get_broker_config_node(state: TSState) -> TSState:
    """工具节点：调用 MCP admin API 查询 Broker 配置。"""
    intent_data = state.get("intent_data", {})
    broker_addr = intent_data.get("broker") or intent_data.get("broker_addr")
    if not broker_addr:
        return {**state, "error": "缺少 broker 地址（brokerAddr）。"}
    res = run_tool(TOOL_GET_BROKER_CONFIG, brokerAddr=broker_addr)
    results = list(state.get("results", []))
    results.append({"action": TOOL_GET_BROKER_CONFIG, "result": res})
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
    """通用 admin 工具节点：执行 MCP admin API 工具。"""
    from tools.tool_registry import get_tool_def

    next_tool = state.get("next_tool", "")
    intent_data = state.get("intent_data", {})
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

    skipped = set(state.get("skipped_params", []) or intent_data.get("skipped_params", []) or [])

    requires_instance = next_tool in (
        "topicRoute",
        "topicStatus",
        "topicClusterList",
        "producerConnection",
        "consumerConnection",
        "consumerProgress",
        "consumerStatus",
        "getConsumerConfig",
        "getConsumerOffset",
    )
    if requires_instance and not instance_id and "instance_id" not in skipped:
        return {
            **state,
            "error": f"{next_tool} 缺少必要参数: instance_id",
            "missing_params": ["instance_id"],
            "missing_for_tool": next_tool,
        }
    if instance_id and instance_id.lower().startswith("rocketmq-") and not rocketmq_ns and "namespace" not in skipped:
        return {
            **state,
            "error": f"{next_tool} 缺少必要参数: namespace",
            "missing_params": ["namespace"],
            "missing_for_tool": next_tool,
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

    mcp_params: Dict[str, Any] = {}
    if next_tool in ("topicRoute", "topicStatus", "topicClusterList"):
        if real_topic:
            mcp_params["topic"] = real_topic
    if next_tool in ("consumerProgress",):
        if real_group:
            mcp_params["group"] = real_group
    if next_tool in ("consumerConnection", "consumerStatus"):
        if real_group:
            mcp_params["consumerGroup"] = real_group
    if next_tool in ("producerConnection",):
        if real_group:
            mcp_params["producerGroup"] = real_group
        if real_topic:
            mcp_params["topic"] = real_topic
    if next_tool in ("brokerStatus", "getBrokerConfig"):
        if broker_addr:
            mcp_params["brokerAddr"] = broker_addr

    tool_def = get_tool_def(next_tool)
    required_params = list(tool_def.params or [])
    for auto_param in ("nameserverAddressList", "ak", "sk"):
        if auto_param in required_params:
            required_params.remove(auto_param)
    missing: List[str] = []
    for param in required_params:
        if param in skipped:
            continue
        if param not in mcp_params:
            missing.append(param)
    if missing:
        return {
            **state,
            "error": f"{next_tool} 缺少必要参数: {', '.join(missing)}",
            "missing_params": missing,
            "missing_for_tool": next_tool,
        }

    state = {**state}
    if real_topic or real_group or instance_id or rocketmq_ns:
        state["resolved_real_topic"] = real_topic or ""
        state["resolved_real_group"] = real_group or ""
        state["resolved_instance_id"] = instance_id or ""
        state["resolved_namespace"] = rocketmq_ns or ""
    res = run_tool(next_tool, **mcp_params)
    results = list(state.get("results", []))
    results.append({"action": next_tool, "result": res})
    return {**state, "results": results}
