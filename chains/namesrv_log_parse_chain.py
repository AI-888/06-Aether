from typing import Dict, Any, List


KEYWORDS = {
    "network": ["connection refused", "timeout", "heartbeat timeout"],
    "metadata": ["route info missing", "register broker", "route info"],
    "disk_io": ["disk full", "io exception"],
    "jvm": ["gc", "outofmemory", "heap", "stop-the-world"],
}


def _collect_evidence(log_text: str) -> List[str]:
    evidence = []
    lower = log_text.lower()
    for _, words in KEYWORDS.items():
        for w in words:
            if w in lower:
                evidence.append(w)
    return list(dict.fromkeys(evidence))


def _infer_root(evidence: List[str]) -> str:
    for root, words in KEYWORDS.items():
        for w in words:
            if w in evidence:
                return root
    return "unknown"


def run_namesrv_log_parse_chain(context: Dict[str, Any]) -> Dict[str, Any]:
    """解析 NameServer 日志文本，输出关键信息。"""
    log_text = context.get("log_text", "") or context.get("log", "")
    if not log_text:
        return {
            "scope": "namesrv_log",
            "suspected_root": "unknown",
            "key_evidence": ["缺少日志文本，请提供 namesrv 日志片段"],
            "next_actions": ["提供 /root/logs/rocketmqlogs 中的 namesrv 日志片段"],
        }

    evidence = _collect_evidence(log_text)
    suspected_root = _infer_root(evidence)
    return {
        "scope": "namesrv_log",
        "suspected_root": suspected_root,
        "key_evidence": evidence,
        "next_actions": [],
    }
