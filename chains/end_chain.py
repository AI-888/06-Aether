# chains/end_chain.py
"""
RocketMQ Agent 的最终结论 Chain
- 收集 Evidence
- 输出结构化结论
"""

from typing import Dict, Any, List


def end_chain(context: Dict[str, Any]) -> Dict[str, Any]:
    """生成最终结论"""
    evidences: List[Dict[str, Any]] = context.get('evidences', [])
    analysis: Dict[str, Any] = context.get('analysis', {})

    incident_report = {
        "incident": "RocketMQ 故障分析结论",
        "suspected_root": analysis.get('suspected_root', 'unknown'),
        "evidence_count": len(evidences),
        "evidences": evidences,
        "suggested_action": analysis.get('recommended_next_actions', []),
        "confidence": analysis.get('confidence', 0.0),
    }

    return incident_report


# -----------------------------
# 示例运行
# -----------------------------

if __name__ == '__main__':
    ctx = {
        "analysis": {
            "reason": "heartbeat timeout",
            "suspected_root": "controller",
            "confidence": 0.9
        },
        "evidences": [
            {"state": "CHECK_META", "output": {"is_isr_related": True}},
            {"state": "CHECK_CONTROLLER_LOG", "output": {"log": "remove broker-b from ISR"}}
        ]
    }

    report = end_chain(ctx)
