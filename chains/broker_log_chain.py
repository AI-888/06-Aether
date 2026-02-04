import json
import os
from typing import Dict

from langchain_core.prompts import PromptTemplate

from models import RocketMQAnalysis


def _read_prompt(filename: str) -> str:
    prompt_file = os.path.join(os.path.dirname(__file__), "..", "prompts", filename)
    try:
        with open(prompt_file, "r", encoding="utf-8") as f:
            return f.read()
    except FileNotFoundError:
        return ""


def _strip_output_section(text: str) -> str:
    marker = "输出 JSON"
    idx = text.find(marker)
    if idx == -1:
        return text
    return text[:idx].rstrip()


def _extract_json(text: str) -> Dict:
    try:
        return json.loads(text)
    except Exception:
        start = text.find("{")
        end = text.rfind("}")
        if start != -1 and end != -1 and end > start:
            return json.loads(text[start:end + 1])
        return {}


class _SimpleResponse:
    def __init__(self, content: str):
        self.content = content


class RouterAnalysisChain:
    def __init__(self, llm):
        base_prompt = _read_prompt("base.prompt")
        router_prompt = _read_prompt("router.prompt")

        self.router_chain = PromptTemplate.from_template(
            f"""
{base_prompt}

{router_prompt}

用户输入内容：
{{user_msg}}

请严格只输出 JSON，不要输出任何解释性文字。
"""
        ) | llm

        self.base_prompt = base_prompt
        self.llm = llm

    def _build_analysis_template(self, scope: str) -> PromptTemplate:
        prompt_map = {
            "producer": "producer.prompt",
            "consumer": "consumer.prompt",
            "topic": "topic.prompt",
            "group": "consumer_group.prompt",
            "namesrv": "namesrv.prompt",
            "broker": "broker.prompt",
            "proxy": "proxy.prompt",
        }

        component_prompt = _strip_output_section(
            _read_prompt(prompt_map.get(scope, "base.prompt"))
        )

        template = f"""
{self.base_prompt}

{component_prompt}

==============================
当前分析任务
==============================
请基于以上 RocketMQ 专家知识，分析以下输入内容。输入可能包含：
- Broker / NameServer 日志片段
- mqadmin 命令输出
- kubectl 命令输出
- 用户描述的问题

用户输入内容：
{{user_msg}}

请只输出 JSON，不要输出解释性文字。JSON 格式必须严格符合：
{{{{
  "problem_scope": "object|component",
  "suspected_object": "producer|consumer|group|topic|null",
  "suspected_component": "namesrv|broker|proxy|null",
  "key_evidence": [
    "关键日志",
    "关键 admin 输出"
  ],
  "suspected_root": "network|disk_io|jvm|replication|metadata|config|unknown",
  "recommended_next_actions": [
    "执行 mqadmin 命令",
    "查看某日志",
    "kubectl 操作"
  ],
  "confidence": 0.0
}}}}

注意：
- `suspected_root` 必须是上述枚举值之一，不要输出带“|”的选项串
- 不确定时降低 confidence
- 信息不足时说明缺失信息并给出下一步动作
- 不要臆造任何日志或命令结果
"""
        return PromptTemplate.from_template(template)

    def invoke(self, inputs: Dict[str, str]):
        user_msg = inputs.get("user_msg", "")

        print("=== Router LLM上下文（平衡模式） ===")
        print({"user_msg": user_msg})
        router_resp = self.router_chain.invoke({"user_msg": user_msg})
        print("=== Router LLM原始输出 ===")
        print(router_resp.content)
        router_data = _extract_json(router_resp.content)
        is_rmq = router_data.get("is_rocketmq_issue", True)
        scope = router_data.get("problem_scope", "mixed")
        print("=== 路由解析结果 ===")
        print(router_data)

        if not is_rmq or scope == "non_rocketmq":
            analysis = RocketMQAnalysis(
                problem_scope="component",
                suspected_object="null",
                suspected_component="null",
                suspected_root="unknown",
                confidence=0.0,
                key_evidence=["非 RocketMQ 问题或信息不足，无法继续诊断"],
                recommended_next_actions=["请补充 RocketMQ 相关日志或 mqadmin/kubectl 输出"]
            )
            return _SimpleResponse(analysis.model_dump_json())

        analysis_prompt = self._build_analysis_template(scope)
        analysis_chain = analysis_prompt | self.llm
        return analysis_chain.invoke({"user_msg": user_msg})


def build_chain(llm):
    """构建RocketMQ分析链（先路由再分析）"""
    return RouterAnalysisChain(llm)


def parse_analysis_result(raw_response) -> RocketMQAnalysis:
    """解析分析结果"""
    try:
        raw_content = raw_response.content
        try:
            return RocketMQAnalysis.model_validate_json(raw_content)
        except Exception:
            # 尝试截取 JSON 片段
            start = raw_content.find("{")
            end = raw_content.rfind("}")
            if start != -1 and end != -1 and end > start:
                candidate = raw_content[start:end + 1]
                try:
                    return RocketMQAnalysis.model_validate_json(candidate)
                except Exception:
                    # 兜底：若枚举字段输出为“a|b|c”，尝试修复为 unknown
                    try:
                        data = json.loads(candidate)
                    except Exception:
                        raise
                    if isinstance(data, dict):
                        if isinstance(data.get("suspected_root"), str) and "|" in data.get("suspected_root", ""):
                            data["suspected_root"] = "unknown"
                            data.setdefault("key_evidence", [])
                            data["key_evidence"].append("模型输出包含枚举选项串，已回退为unknown")
                    return RocketMQAnalysis.model_validate(data)
            raise
    except Exception as e:
        # 如果解析失败，返回默认分析结果
        return RocketMQAnalysis(
            problem_scope="component",
            suspected_object="null",
            suspected_component="null",
            suspected_root="unknown",
            confidence=0.0,
            key_evidence=[f"解析失败: {str(e)}"],
            recommended_next_actions=[]
        )
