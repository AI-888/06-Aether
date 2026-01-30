# chains/end_chain.py
"""
RocketMQ ISR Agent 的最终结论 Chain
- 收集 Evidence
- 输出结构化 Incident / RCA 报告
"""

from typing import Dict, Any, List


def end_chain(context: Dict[str, Any]) -> Dict[str, Any]:
    """生成最终 ISR 故障结论"""
    evidences: List[Dict[str, Any]] = context.get('evidences', [])
    analysis: Dict[str, Any] = context.get('analysis', {})

    incident_report = {
        "incident": "RocketMQ ISR 异常",
        "root_cause": analysis.get('reason', 'unknown'),
        "suspected_root": analysis.get('suspected_root', 'unknown'),
        "evidence_count": len(evidences),
        "evidences": evidences,
        "suggested_action": [
            "检查 broker 与 controller 网络",
            "检查 broker 磁盘 IO",
            "评估替换或扩容异常 broker",
        ],
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
