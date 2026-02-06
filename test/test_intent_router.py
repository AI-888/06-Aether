import os
import unittest

from chains.intent_router_chain import run_intent_router_chain
from tools.tool_registry import (
    TOOL_LIST_BROKER_PODS,
    TOOL_SEND_FAIL_CHECK,
    TOOL_LIST_TOPICS,
    TOOL_GET_BROKER_CONFIG,
)


class DummyResp:
    def __init__(self, content: str):
        self.content = content


class DummyLLM:
    def __init__(self, content: str):
        self._content = content

    def invoke(self, _):
        return DummyResp(self._content)


class IntentRouterTests(unittest.TestCase):
    @staticmethod
    def _load_all_prompts() -> str:
        prompts_dir = os.path.join(os.path.dirname(__file__), "..", "prompts")
        if not os.path.isdir(prompts_dir):
            return ""
        parts = []
        for fname in os.listdir(prompts_dir):
            if not fname.endswith(".prompt"):
                continue
            path = os.path.join(prompts_dir, fname)
            try:
                with open(path, "r", encoding="utf-8") as f:
                    content = f.read().strip()
                    if content:
                        parts.append(f"=== {fname} ===\n{content}\n")
            except Exception as e:
                print(f"Error loading prompts {path}: {e}")
                continue
        return "\n".join(parts)

    def test_list_broker_pods(self):
        llm = DummyLLM(f'{{"intents":["{TOOL_LIST_BROKER_PODS}"],"namespace":"rocketmq5"}}')
        base_prompt = self._load_all_prompts()
        data = run_intent_router_chain(llm, "列出rocketmq broker全部pod", base_prompt=base_prompt)
        self.assertIn(TOOL_LIST_BROKER_PODS, data.get("intents", []))

    def test_send_fail_check_rule_fallback(self):
        llm = DummyLLM('{"intents":[]}')  # force fallback
        base_prompt = self._load_all_prompts()
        data = run_intent_router_chain(llm, "发送消息失败，帮我看看", base_prompt=base_prompt)
        self.assertIn(TOOL_SEND_FAIL_CHECK, data.get("intents", []))

    def test_list_topics_rule_fallback(self):
        llm = DummyLLM('{"intents":[]}')  # force fallback
        base_prompt = self._load_all_prompts()
        data = run_intent_router_chain(llm, "列出全部topic", base_prompt=base_prompt)
        self.assertIn(TOOL_LIST_TOPICS, data.get("intents", []))

    def test_broker_config_rule_fallback(self):
        llm = DummyLLM('{"intents":[]}')  # force fallback
        base_prompt = self._load_all_prompts()
        data = run_intent_router_chain(llm, "查询 broker 配置", base_prompt=base_prompt)
        self.assertIn(TOOL_GET_BROKER_CONFIG, data.get("intents", []))


if __name__ == "__main__":
    unittest.main()
