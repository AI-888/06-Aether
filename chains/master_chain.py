from typing import Dict, Any, List, Tuple, Optional

from chains.namesrv_log_parse_chain import run_namesrv_log_parse_chain
from chains.broker_admin_api_chain import run_broker_admin_api_chain
from chains.namesrv_admin_api_chain import run_namesrv_admin_api_chain
from chains.kubectl_pods_chain import run_kubectl_pods_chain
from chains.kubectl_svc_chain import run_kubectl_svc_chain
from chains.broker_logs_chain import run_broker_logs_chain
from chains.namesrv_logs_chain import run_namesrv_logs_chain
from chains.broker_jvm_chain import run_broker_jvm_chain
from chains.namesrv_jvm_chain import run_namesrv_jvm_chain
from chains.intent_router_chain import run_intent_router_chain


def _append_step(result_list: List[Dict[str, Any]], name: str, output: Dict[str, Any]):
    result_list.append({"step": name, "output": output})


def _pick_first_match(lines: List[str], keywords: List[str]) -> Optional[str]:
    for line in lines:
        lower = line.lower()
        if all(k in lower for k in keywords):
            return line
    return None


def _infer_namesrv_from_svc(output: str, namespace: Optional[str]) -> Optional[str]:
    if not output:
        return None
    lines = [ln for ln in output.splitlines() if ln.strip()]
    line = _pick_first_match(lines, ["namesrv"])
    if not line:
        return None
    parts = line.split()
    if not parts:
        return None
    svc_name = parts[0]
    # try to find port 9876, otherwise return svc name only
    port = "9876" if "9876" in line else None
    if namespace and port:
        return f"{svc_name}.{namespace}:{port}"
    if namespace:
        return f"{svc_name}.{namespace}"
    if port:
        return f"{svc_name}:{port}"
    return svc_name


def _infer_broker_from_pods(output: str) -> Optional[str]:
    if not output:
        return None
    lines = [ln for ln in output.splitlines() if ln.strip()]
    line = _pick_first_match(lines, ["broker"])
    if not line:
        return None
    parts = line.split()
    if not parts:
        return None
    return parts[0]


def _infer_namesrv_pod_from_pods(output: str) -> Optional[str]:
    if not output:
        return None
    lines = [ln for ln in output.splitlines() if ln.strip()]
    line = _pick_first_match(lines, ["namesrv"])
    if not line:
        return None
    parts = line.split()
    if not parts:
        return None
    return parts[0]


def run_master_chain(context: Dict[str, Any]) -> Dict[str, Any]:
    """
    总控顺序 Chain：串联日志解析、admin API、kubectl 查询。

    支持的 context 字段：
    - execute: bool，是否执行命令（默认 False）
    - namesrv: str
    - broker: str
    - topic: str
    - log_text / broker_log / namesrv_log: 日志文本
    - mqadmin_bin: str
    - namespace: str
    """
    execute = bool(context.get("execute", False))
    llm = context.get("llm")
    namesrv = context.get("namesrv")
    broker = context.get("broker")
    topic = context.get("topic")
    namespace = context.get("namespace")
    ak = context.get("ak")
    sk = context.get("sk")

    steps: List[Dict[str, Any]] = []
    merged_context = dict(context)

    # 0) LLM 意图路由
    intents: List[str] = []
    if llm:
        intent_result = run_intent_router_chain(
            llm=llm,
            user_msg=context.get("user_msg", ""),
            base_prompt=context.get("base_prompt", ""),
        )
        _append_step(steps, "intent_router", intent_result)
        merged_context["intent_router"] = intent_result
        intents = intent_result.get("intents", []) or []
        namespace = intent_result.get("namespace") or namespace
        topic = intent_result.get("topic") or topic
        broker = intent_result.get("broker") or broker
        namesrv = intent_result.get("namesrv") or namesrv

    # 1) NameServer 日志解析（若输入显式包含日志）
    namesrv_log_text = context.get("namesrv_log") or context.get("log_text")
    if namesrv_log_text:
        out = run_namesrv_log_parse_chain({"log_text": namesrv_log_text})
        _append_step(steps, "namesrv_log_parse", out)
        merged_context["namesrv_log_parse"] = out

    # 根据意图执行对应链
    if "list_broker_pods" in intents or "broker_logs" in intents or "broker_jvm" in intents or "namesrv_admin_cluster" in intents or "namesrv_admin_topic_route" in intents or "broker_admin_config" in intents or "broker_admin_status" in intents:
        out = run_kubectl_pods_chain({"execute": execute, "namespace": namespace})
        _append_step(steps, "kubectl_pods", out)
        merged_context["kubectl_pods"] = out
        if not broker and out.get("output"):
            broker = _infer_broker_from_pods(out.get("output", ""))
            if broker:
                merged_context["broker"] = broker
        if out.get("output"):
            namesrv_pod = _infer_namesrv_pod_from_pods(out.get("output", ""))
            if namesrv_pod:
                merged_context["namesrv_pod"] = namesrv_pod

    if "list_rocketmq_services" in intents or "namesrv_logs" in intents or "namesrv_admin_cluster" in intents or "namesrv_admin_topic_route" in intents or "namesrv_jvm" in intents or "broker_admin_config" in intents or "broker_admin_status" in intents:
        out = run_kubectl_svc_chain({"execute": execute, "namespace": namespace})
        _append_step(steps, "kubectl_svc", out)
        merged_context["kubectl_svc"] = out
        if not namesrv and out.get("output"):
            namesrv = _infer_namesrv_from_svc(out.get("output", ""), namespace)
            if namesrv:
                merged_context["namesrv"] = namesrv

    if "broker_logs" in intents:
        out = run_broker_logs_chain({
            "execute": execute,
            "namespace": namespace,
            "tail": context.get("tail", 200),
        })
        _append_step(steps, "broker_logs", out)
        merged_context["broker_logs"] = out

    if "namesrv_logs" in intents:
        out = run_namesrv_logs_chain({
            "execute": execute,
            "namespace": namespace,
            "tail": context.get("tail", 200),
        })
        _append_step(steps, "namesrv_logs", out)
        merged_context["namesrv_logs"] = out

    if "namesrv_admin_cluster" in intents and namesrv:
        out = run_namesrv_admin_api_chain({
            "namesrv": namesrv,
            "action": "clusterList",
            "execute": execute,
            "ak": ak,
            "sk": sk,
            "namespace": namespace,
            "namesrv_pod": merged_context.get("namesrv_pod"),
        })
        _append_step(steps, "namesrv_admin_api", out)
        merged_context["namesrv_admin_api"] = out

    if "namesrv_admin_topic_route" in intents and namesrv:
        out = run_namesrv_admin_api_chain({
            "namesrv": namesrv,
            "action": "topicRoute",
            "topic": topic,
            "execute": execute,
            "ak": ak,
            "sk": sk,
            "namespace": namespace,
            "namesrv_pod": merged_context.get("namesrv_pod"),
        })
        _append_step(steps, "namesrv_topic_route", out)
        merged_context["namesrv_topic_route"] = out

    if "broker_admin_status" in intents and namesrv and broker:
        out = run_broker_admin_api_chain({
            "namesrv": namesrv,
            "broker": broker,
            "action": "brokerStatus",
            "execute": execute,
            "ak": ak,
            "sk": sk,
            "namespace": namespace,
            "namesrv_pod": merged_context.get("namesrv_pod"),
        })
        _append_step(steps, "broker_status", out)
        merged_context["broker_status"] = out

    if "broker_admin_config" in intents and namesrv and broker:
        out = run_broker_admin_api_chain({
            "namesrv": namesrv,
            "broker": broker,
            "action": "getBrokerConfig",
            "execute": execute,
            "ak": ak,
            "sk": sk,
            "namespace": namespace,
            "namesrv_pod": merged_context.get("namesrv_pod"),
        })
        _append_step(steps, "broker_config", out)
        merged_context["broker_config"] = out

    if "broker_jvm" in intents:
        out = run_broker_jvm_chain({
            "namespace": namespace,
            "pod": context.get("pod") or broker,
            "container": context.get("container", "broker"),
            "execute": execute,
        })
        _append_step(steps, "broker_jvm", out)
        merged_context["broker_jvm"] = out

    if "namesrv_jvm" in intents:
        out = run_namesrv_jvm_chain({
            "namespace": namespace,
            "pod": context.get("pod") or namesrv,
            "container": context.get("container", "namesrv"),
            "execute": execute,
        })
        _append_step(steps, "namesrv_jvm", out)
        merged_context["namesrv_jvm"] = out

    return {
        "scope": "master_chain",
        "execute": execute,
        "steps": steps,
        "context": merged_context,
    }
