import json
from typing import Dict, Any

from langchain_core.prompts import PromptTemplate

from tools.tool_registry import (
    build_tools_prompt,
    list_tool_names,
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
    intents_list = list_tool_names() + list_tool_names() + ["unknown"]
    intents_lines = "\n".join([f"- {name}" for name in intents_list])
    safe_base = base_prompt.replace("{", "{{").replace("}", "}}")
    template = f"""
        {safe_base}

        {tool_list_text}

        你是意图路由器。请根据用户输入识别要执行的操作意图。
        
        可选 intents：
        {intents_lines}
        
        要求：
        - 如果用户要求“全部/都需要/全量”，返回所有可执行 intents（除 unknown）。
        - 如果缺少必要参数，也要返回 intents，但在参数里留空。
        - 只输出 JSON，不要输出解释性文字。
                
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

    intents = data.get("intents", []) or []
    lower = user_msg.lower()

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
    data["intents"] = intents or data.get("intents", [])
    if not data["intents"]:
        data["info_only"] = True
    return data
