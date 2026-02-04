"""
RocketMQ Admin API tools.
"""

import json
import os
from typing import Optional, Dict, Any

from tools.kubectl_tools import run_kubectl_exec
from tools.mcp_client import call_mcp_tool


def _resolve_mcp_tool(action: str) -> str:
    mapping = {
        "clusterList": "getClusterInfo",
        "topicRoute": "examineTopicRouteInfo",
        "topicStatus": "examineTopicStats",
        "brokerStatus": "getBrokerRuntimeStats",
        "getBrokerConfig": "getBrokerConfig",
        "consumerProgress": "examineConsumeStats",
        "consumerStatus": "examineSubscriptionGroupConfig",
        "consumerConnection": "examineConsumerConnectionInfo",
    }
    return mapping.get(action, action)


def _build_mcp_args(
        namesrv: Optional[str],
        broker: Optional[str],
        topic: Optional[str],
        group: Optional[str],
        ak: Optional[str],
        sk: Optional[str],
        extra_args: Optional[str],
) -> Dict[str, Any]:
    args: Dict[str, Any] = {}
    if namesrv:
        args["nameserverAddressList"] = [s.strip() for s in namesrv.split(",") if s.strip()]
    if broker:
        args["brokerAddr"] = broker
        args["addr"] = broker
    if topic:
        args["topic"] = topic
        args["topicName"] = topic
    if group:
        args["group"] = group
        args["consumerGroup"] = group
    if ak:
        args["ak"] = ak
    if sk:
        args["sk"] = sk
    if extra_args:
        try:
            extra = json.loads(extra_args)
            if isinstance(extra, dict):
                args.update(extra)
        except Exception:
            pass
    return args


def _parse_tools_yaml(text: str) -> Dict[str, str]:
    """
    Minimal YAML parser for ak/sk in /root/conf/tools.yml.
    Accepts lines like: ak: xxx, sk: yyy
    """
    result: Dict[str, str] = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or ":" not in line:
            continue
        key, value = line.split(":", 1)
        key = key.strip()
        value = value.strip().strip("\"' ")
        if key in ("ak", "sk"):
            result[key] = value
    return result


def _load_ak_sk_from_k8s(
        namespace: Optional[str],
        namesrv_pod: Optional[str],
        container: str = "ocloud-tdmq-rocketmq5-namesrv",
        path: str = "/root/conf/tools.yml",
        execute: bool = False,
) -> Dict[str, str]:
    if not namespace or not namesrv_pod:
        return {}
    cmd = f"cat {path}"
    resp = run_kubectl_exec(namespace, namesrv_pod, container, cmd, execute=execute)
    if resp.get("executed") != "true":
        return {}
    return _parse_tools_yaml(resp.get("output", ""))


def call_admin_api(
        action: str,
        namesrv: Optional[str] = None,
        broker: Optional[str] = None,
        topic: Optional[str] = None,
        group: Optional[str] = None,
        ak: Optional[str] = None,
        sk: Optional[str] = None,
        namespace: Optional[str] = None,
        namesrv_pod: Optional[str] = None,
        namesrv_container: str = "ocloud-tdmq-rocketmq5-namesrv",
        tools_conf_path: str = "/root/conf/tools.yml",
        extra_args: Optional[str] = None,
        execute: bool = False,
) -> Dict[str, Any]:
    """
    Always call MCP tool API.
    """
    mcp_url = os.environ.get("MCP_SSE_URL", "").strip()
    if not ak or not sk:
        creds = _load_ak_sk_from_k8s(
            namespace=namespace,
            namesrv_pod=namesrv_pod,
            container=namesrv_container,
            path=tools_conf_path,
            execute=execute,
        )
        ak = ak or creds.get("ak")
        sk = sk or creds.get("sk")

    if not mcp_url:
        return {
            "error": "MCP_SSE_URL is not set. Admin API requires MCP tool API.",
            "action": action,
        }
    if not ak or not sk:
        return {
            "error": "MCP requires ak/sk. Please provide namespace + namesrv_pod to load /root/conf/tools.yml.",
            "action": action,
        }
    tool = _resolve_mcp_tool(action)
    args = _build_mcp_args(namesrv, broker, topic, group, ak, sk, extra_args)
    return {
        "tool": tool,
        "arguments": args,
        "mcp": call_mcp_tool(mcp_url, tool, args),
    }


def producer_admin_api(
        action: str,
        namesrv: Optional[str] = None,
        topic: Optional[str] = None,
        producer: Optional[str] = None,
        ak: Optional[str] = None,
        sk: Optional[str] = None,
        extra_args: Optional[str] = None,
        execute: bool = False,
) -> Dict[str, str]:
    return call_admin_api(
        action=action,
        namesrv=namesrv,
        topic=topic,
        ak=ak,
        sk=sk,
        extra_args=extra_args,
        execute=execute,
    )


def consumer_admin_api(
        action: str,
        namesrv: Optional[str] = None,
        group: Optional[str] = None,
        consumer: Optional[str] = None,
        ak: Optional[str] = None,
        sk: Optional[str] = None,
        extra_args: Optional[str] = None,
        execute: bool = False,
) -> Dict[str, str]:
    return call_admin_api(
        action=action,
        namesrv=namesrv,
        group=group,
        ak=ak,
        sk=sk,
        extra_args=extra_args,
        execute=execute,
    )


def topic_admin_api(
        action: str,
        namesrv: Optional[str] = None,
        topic: Optional[str] = None,
        ak: Optional[str] = None,
        sk: Optional[str] = None,
        extra_args: Optional[str] = None,
        execute: bool = False,
) -> Dict[str, str]:
    return call_admin_api(
        action=action,
        namesrv=namesrv,
        topic=topic,
        ak=ak,
        sk=sk,
        extra_args=extra_args,
        execute=execute,
    )


def consumer_group_admin_api(
        action: str,
        namesrv: Optional[str] = None,
        group: Optional[str] = None,
        ak: Optional[str] = None,
        sk: Optional[str] = None,
        extra_args: Optional[str] = None,
        execute: bool = False,
) -> Dict[str, str]:
    return call_admin_api(
        action=action,
        namesrv=namesrv,
        group=group,
        ak=ak,
        sk=sk,
        extra_args=extra_args,
        execute=execute,
    )


def broker_admin_api(
        action: str,
        namesrv: Optional[str] = None,
        broker: Optional[str] = None,
        ak: Optional[str] = None,
        sk: Optional[str] = None,
        extra_args: Optional[str] = None,
        execute: bool = False,
) -> Dict[str, str]:
    return call_admin_api(
        action=action,
        namesrv=namesrv,
        broker=broker,
        ak=ak,
        sk=sk,
        extra_args=extra_args,
        execute=execute,
    )


def namesrv_admin_api(
        action: str,
        namesrv: Optional[str] = None,
        topic: Optional[str] = None,
        ak: Optional[str] = None,
        sk: Optional[str] = None,
        extra_args: Optional[str] = None,
        execute: bool = False,
) -> Dict[str, str]:
    return call_admin_api(
        action=action,
        namesrv=namesrv,
        topic=topic,
        ak=ak,
        sk=sk,
        extra_args=extra_args,
        execute=execute,
    )
