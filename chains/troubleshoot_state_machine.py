from typing import Dict, Any, List, TypedDict

from langgraph.graph import END
from langgraph.graph.state import StateGraph

from chains.intent_router_chain import run_intent_router_chain
from skills.skills_loader import load_skills_from_dir
from tools.tool_registry import (
    list_tool_names,
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
        llm = state.get("llm")
        user_msg = state["user_msg"]
        prompt_content = state.get("prompt_content", "")
        
        # 检查LLM是否可用
        if not llm:
            # 如果LLM不可用，创建默认的意图数据
            intent_data = {
                "intents": [],
                "info_only": True,
                "error": "LLM不可用，无法进行意图识别"
            }
            intents = []
        else:
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


def _ensure_skills(state: TSState) -> TSState:
    """在工具调用前加载 skills，仅加载一次。"""
    if state.get("skills_content"):
        return state
    skills_content = load_skills_from_dir()
    if skills_content:
        from datetime import datetime
        ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        print(f"[{ts}] [Step 2] Skills loaded before tool call")
    return {**state, "skills_content": skills_content}











def _route_node(state: TSState) -> str:
    """路由节点：根据 next_tool 决定执行哪个工具节点。"""
    next_tool = state.get("next_tool", "")
    
    if next_tool in list_tool_names():
        return "admin_tool"
    return "end_check"


def _end_check_node(state: TSState) -> TSState:
    """结束判断节点：基于智能规则判断是否应该结束执行流程。"""
    from datetime import datetime
    
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    
    # 调用智能结束判断器
    end_decision = _intelligent_end_check(state)
    need_continue = end_decision.get("need_continue", True)
    reason = end_decision.get("reason", "")
    next_action = end_decision.get("next_action", "continue_tool")
    
    if not need_continue:
        # print(f"[{ts}] [End Check] 判断结束: {reason}")
        return {**state, "end_reason": reason, "should_end": True, "end_decision": end_decision}
    
    print(f"[{ts}] [End Check] 继续执行: {reason}, 下一步: {next_action}")
    return {**state, "should_end": False, "end_decision": end_decision}


def _intelligent_end_check(state: TSState) -> Dict[str, Any]:
    """智能结束判断器：基于规则判断是否终止任务。"""
    
    # 1. 检查用户明确停止指令
    user_msg = state.get("user_msg", "").lower()
    stop_keywords = ["停止", "结束", "不用了", "取消", "stop", "end", "cancel"]
    if any(keyword in user_msg for keyword in stop_keywords):
        return {
            "need_continue": False,
            "reason": "用户明确要求停止执行",
            "next_action": "finish"
        }
    
    # 2. 检查错误状态
    if state.get("error"):
        return {
            "need_continue": False,
            "reason": f"执行过程中出现错误: {state.get('error')}",
            "next_action": "finish"
        }
    
    # 3. 检查工具调用失败
    results = state.get("results", [])
    for result in results:
        result_data = result.get("result", {})
        if result_data.get("error") or result_data.get("failed"):
            return {
                "need_continue": False,
                "reason": "工具/MCP调用失败",
                "next_action": "finish"
            }
    
    # 4. 检查执行步骤限制
    if len(results) >= 10:  # 最大执行步骤限制
        return {
            "need_continue": False,
            "reason": f"达到最大执行步骤限制({len(results)}步)",
            "next_action": "finish"
        }
    
    # 5. 检查是否需要追问用户信息（优先级较高）
    missing_params = state.get("missing_params", [])
    if missing_params:
        skipped_params = state.get("skipped_params", [])
        # 如果用户已经跳过了所有缺失参数，则结束
        if skipped_params and len(skipped_params) >= len(missing_params):
            return {
                "need_continue": False,
                "reason": "缺少关键信息且用户拒绝补充",
                "next_action": "finish"
            }
        # 否则需要用户补充信息
        return {
            "need_continue": True,
            "reason": "需要用户补充关键信息",
            "next_action": "ask_user"
        }
    
    # 6. 检查核心需求是否已满足
    intents = state.get("intents", [])
    executed_tools = [result.get("action") for result in results if result.get("action")]
    remaining_intents = [intent for intent in intents if intent not in executed_tools]
    
    if not remaining_intents:
        return {
            "need_continue": False,
            "reason": "用户核心需求已完全满足",
            "next_action": "finish"
        }
    
    # 8. 检查工具队列状态
    tool_queue = state.get("tool_queue", [])
    if not tool_queue:
        return {
            "need_continue": False,
            "reason": "工具队列已空，任务完成",
            "next_action": "finish"
        }
    
    # 9. 检查结果是否已经足够得出结论
    if _has_sufficient_results(state):
        return {
            "need_continue": False,
            "reason": "已有足够结果得出结论",
            "next_action": "finish"
        }
    
    # 10. 默认继续执行
    return {
        "need_continue": True,
        "reason": "需求未完全满足，需继续调用工具",
        "next_action": "continue_tool"
    }


def _has_sufficient_results(state: TSState) -> bool:
    """判断当前结果是否已经足够得出结论。"""
    results = state.get("results", [])
    
    # 如果有错误结果，通常已经足够
    for result in results:
        if result.get("result", {}).get("error"):
            return True
    
    # 检查是否有明确的成功/失败结论
    success_indicators = ["success", "正常", "running", "healthy"]
    failure_indicators = ["error", "失败", "not found", "异常"]
    
    for result in results:
        result_text = str(result.get("result", {})).lower()
        
        # 检查成功指标
        if any(indicator in result_text for indicator in success_indicators):
            return True
        
        # 检查失败指标
        if any(indicator in result_text for indicator in failure_indicators):
            return True
    
    # 如果有多个相关结果，可能已经足够
    if len(results) >= 3:
        # 检查结果的相关性
        related_results = 0
        for result in results:
            action = result.get("action", "")
            if any(keyword in action for keyword in ["list", "get", "check"]):
                related_results += 1
        
        if related_results >= 2:
            return True
    
    return False


def _final_route_node(state: TSState) -> TSState:
    """最终路由节点：根据智能结束判断结果决定下一步动作。"""
    end_decision = state.get("end_decision", {})
    next_action = end_decision.get("next_action", "continue_tool")
    
    if next_action == "finish":
        return {**state, "next_node": END}
    elif next_action == "ask_user":
        # 这里可以添加用户交互逻辑，暂时先继续执行
        return {**state, "next_node": "dispatch"}
    else:  # continue_tool
        return {**state, "next_node": "dispatch"}


def build_troubleshoot_graph() -> StateGraph:
    """构建 LangGraph 状态机：intent -> dispatch -> tool -> dispatch -> end_check -> END。"""
    graph = StateGraph(TSState)
    graph.add_node("intent", _intent_node)
    graph.add_node("dispatch", _dispatch_node)
    graph.add_node("admin_tool", _admin_tool_node)
    
    graph.add_node("end_check", _end_check_node)

    graph.set_entry_point("intent")
    graph.add_edge("intent", "dispatch")
    graph.add_conditional_edges("dispatch", _route_node, {
        "admin_tool": "admin_tool",
        "end_check": "end_check",
    })
    
    # 添加工具节点到dispatch的连接
    graph.add_edge("admin_tool", "dispatch")
    graph.add_edge("end_check", "final_route")
    graph.add_node("final_route", _final_route_node)
    graph.add_conditional_edges("final_route", lambda state: state.get("next_node", "dispatch"), {
        "dispatch": "dispatch",
        END: END,
    })
    
    # 添加dispatch到end_check的直接连接，确保每个工具执行后都进行结束判断
    graph.add_edge("dispatch", "end_check")
    return graph


def _build_tool_queue(intents: List[str]) -> List[str]:
    """将 intents 变为工具执行队列。"""
    queue: List[str] = []
    admin_tools = set(list_tool_names())
    for intent in intents:
        if intent in admin_tools:
            queue.append(intent)
    return queue


def _admin_tool_node(state: TSState) -> TSState:
    """通用 admin 工具节点：执行 MCP admin API 工具。"""
    from tools.tool_registry import get_tool_def
    from tools.mcp_client import get_mcp_defaults

    state = _ensure_skills(state)
    next_tool = state.get("next_tool", "")
    intent_data = state.get("intent_data", {})
    skipped = set(state.get("skipped_params", []) or intent_data.get("skipped_params", []) or [])

    tool_def = get_tool_def(next_tool)
    
    # 获取系统自动提供的默认参数
    mcp_defaults = get_mcp_defaults()
    
    # 动态构建参数映射，使用工具定义中的参数列表
    mcp_params: Dict[str, Any] = {}
    tool_params = tool_def.params or []
    
    for param in tool_params:
        val = intent_data.get(param)
        if val:
            mcp_params[param] = val

    # 动态判断必传参数：根据工具定义中的参数列表判断
    # 排除系统自动提供的参数和用户已跳过的参数
    missing: List[str] = []
    for param in tool_params:
        # 如果参数由系统自动提供，则不需要用户传入
        if param in mcp_defaults and mcp_defaults[param]:
            continue
        # 如果用户已跳过该参数，则不需要检查
        if param in skipped:
            continue
        # 检查参数是否已提供
        if param not in mcp_params:
            missing.append(param)
    
    if missing:
        return {
            **state,
            "error": f"{next_tool} 缺少必要参数: {', '.join(missing)}",
            "missing_params": missing,
            "missing_for_tool": next_tool,
        }

    # 合并系统默认参数
    for param, default_val in mcp_defaults.items():
        if param in tool_def.params and param not in mcp_params:
            mcp_params[param] = default_val

    state = {**state}
    res = run_tool(next_tool, **mcp_params)
    results = list(state.get("results", []))
    results.append({"action": next_tool, "result": res})
    return {**state, "results": results}
