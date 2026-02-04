"""
Minimal MCP SSE client for tool calls.
"""

from typing import Any, Dict, Optional, Tuple
import json
import time
import urllib.request
import urllib.error


def _post_json(url: str, payload: Dict[str, Any]) -> Tuple[int, str]:
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            body = resp.read().decode("utf-8", errors="replace")
            return resp.status, body
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", errors="replace")
    except Exception as e:
        return 0, f"POST failed: {e}"


def _read_sse_endpoint(sse_url: str, timeout_s: int = 10) -> Optional[str]:
    try:
        stream = urllib.request.urlopen(sse_url, timeout=10)
    except Exception:
        return None

    start = time.time()
    event = None
    data_lines = []

    while time.time() - start < timeout_s:
        line = stream.readline()
        if not line:
            time.sleep(0.1)
            continue
        line = line.decode("utf-8", errors="replace").strip("\n")
        if line.startswith("event:"):
            event = line[len("event:"):].strip()
        elif line.startswith("data:"):
            data_lines.append(line[len("data:"):].strip())
        elif line.strip() == "":
            if event == "endpoint" and data_lines:
                return "\n".join(data_lines).strip()
            event = None
            data_lines = []
    return None


def call_mcp_tool(
    sse_url: str,
    tool_name: str,
    arguments: Dict[str, Any],
) -> Dict[str, Any]:
    endpoint = _read_sse_endpoint(sse_url)
    if not endpoint:
        return {"error": f"Failed to resolve MCP endpoint from {sse_url}"}

    # initialize
    init_payload = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "initialize",
        "params": {
            "protocolVersion": "2024-11-05",
            "capabilities": {},
            "clientInfo": {"name": "rocketmq-agent", "version": "0.1"},
        },
    }
    _post_json(endpoint, init_payload)

    # tools/call
    call_payload = {
        "jsonrpc": "2.0",
        "id": 2,
        "method": "tools/call",
        "params": {"name": tool_name, "arguments": arguments},
    }
    status, body = _post_json(endpoint, call_payload)
    return {"status": status, "body": body}
