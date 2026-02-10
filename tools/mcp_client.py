import json
import os
from datetime import datetime
from typing import Any, Dict, Optional

import requests

DEFAULT_CONFIG = {
    "base_url": "http://9.134.241.105:6868/sse",
    "call_path": "/message",
    "call_mode": "tool_call",  # tool_call | bare
    "ak": "",
    "sk": "",
    "nameserverAddressList": [],
    "timeout_sec": 30,
}


def load_mcp_config(config_path: Optional[str] = None) -> Dict[str, Any]:
    if not config_path:
        config_path = os.path.join(os.path.dirname(__file__), "..", "config", "mcp.json")
        config_path = os.path.abspath(config_path)
    if not os.path.isfile(config_path):
        return DEFAULT_CONFIG.copy()
    with open(config_path, "r", encoding="utf-8") as f:
        data = json.load(f)
    cfg = DEFAULT_CONFIG.copy()
    cfg.update(data or {})
    return cfg


def get_mcp_defaults(config_path: Optional[str] = None) -> Dict[str, Any]:
    cfg = load_mcp_config(config_path)
    return {
        "nameserverAddressList": cfg.get("nameserverAddressList", []),
        "ak": cfg.get("ak", ""),
        "sk": cfg.get("sk", ""),
    }


def call_mcp_tool(tool_name: str, arguments: Dict[str, Any], config_path: Optional[str] = None) -> Dict[str, Any]:
    cfg = load_mcp_config(config_path)
    base_url = cfg.get("base_url", "").rstrip("/")
    call_path = cfg.get("call_path", "/message")
    url = f"{base_url}{call_path}"
    call_mode = cfg.get("call_mode", "tool_call")

    if call_mode == "bare":
        payload = arguments
    else:
        payload = {"name": tool_name, "arguments": arguments}

    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{ts}] [Tool Call] mcp input: {{'tool': '{tool_name}', 'payload': {payload}}}")
    resp = requests.post(url, json=payload, timeout=cfg.get("timeout_sec", 30))
    resp.raise_for_status()
    try:
        data = resp.json()
    except Exception:
        data = {"raw": resp.text}
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    summary = _summarize_response(data)
    print(f"[{ts}] [Tool Call] mcp output: {summary}")
    return data


def _summarize_response(resp: Dict[str, Any]) -> Dict[str, Any]:
    """Return a compact summary for logging."""
    if not isinstance(resp, dict):
        return {"preview": str(resp)[:300]}
    if "errorCode" in resp or "errorMessage" in resp:
        summary = {
            "errorCode": resp.get("errorCode"),
            "errorMessage": resp.get("errorMessage"),
        }
        data = resp.get("data")
        if data is not None:
            preview = str(data)
            summary["data_preview"] = preview[:300]
        return summary
    preview = str(resp)
    return {"preview": preview[:300]}
