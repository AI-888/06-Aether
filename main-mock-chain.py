# main_real_agent.py
"""
RocketMQ ISR Agent - 真实调用版
- 使用真实的 chains 目录中的 chain
- 使用真实的 tools 目录中的 tool
- 保留 FSM / Router / Tool
"""

import json
import os
from typing import List, Dict

from langchain.chat_models import ChatOpenAI

from chains.broker_log_chain import build_chain
from chains.controller_chain import run_controller_chain
from chains.end_chain import end_chain
from models import BrokerLogAnalysis
from router import route
from tools.shell_mysql_tool import run_shell, query_mysql


# -----------------------------
# Tool 集成
# -----------------------------

def check_broker_status(broker_id: str) -> Dict:
    """使用shell工具检查broker状态"""
    try:
        # 模拟检查broker状态的命令
        result = run_shell(f"echo 'Checking broker {broker_id} status'")
        return {
            "tool": "check_broker_status",
            "broker_id": broker_id,
            "result": result,
            "status": "running" if "running" in result.lower() else "unknown"
        }
    except Exception as e:
        return {"tool": "check_broker_status", "error": str(e)}


def query_broker_metrics(broker_id: str) -> Dict:
    """使用MySQL工具查询broker指标"""
    try:
        # 模拟查询broker指标
        metrics = query_mysql(
            host="127.0.0.1",
            port=3306,
            user="root",
            password="password",
            database="rmq",
            sql=f"SELECT * FROM broker_metrics WHERE broker_id = '{broker_id}' ORDER BY timestamp DESC LIMIT 5;"
        )
        return {
            "tool": "query_broker_metrics",
            "broker_id": broker_id,
            "metrics": metrics
        }
    except Exception as e:
        return {"tool": "query_broker_metrics", "error": str(e)}


# -----------------------------
# AgentExecutor - 真实流程
# -----------------------------

def run_real_agent():
    """运行真实的Agent流程"""
    
    # 从环境变量获取OpenAI API密钥
    openai_api_key = os.getenv("OPENAI_API_KEY")
    if not openai_api_key:
        raise ValueError("请设置环境变量 OPENAI_API_KEY")
    
    llm = ChatOpenAI(model="gpt-4o-mini", temperature=0, openai_api_key=openai_api_key)
    
    evidences: List[Dict] = []
    
    # 示例broker日志
    broker_log = """
WARN remove broker from ISR, brokerId=2, reason=heartbeat timeout
INFO broker 2 not in sync state
"""
    
    # 1. 使用真实的broker_log_chain
    broker_chain = build_chain(llm)
    raw_result = broker_chain.invoke({"broker_log": broker_log})
    
    # 解析结果
    analysis = BrokerLogAnalysis.parse_raw(raw_result["text"])
    evidences.append({
        "state": "BROKER_LOG_ANALYSIS",
        "output": analysis.dict(),
        "tool_used": "broker_log_chain"
    })
    
    # 2. 使用工具检查broker状态
    tool_result = check_broker_status("broker-2")
    evidences.append({
        "state": "TOOL_CHECK_BROKER",
        "output": tool_result,
        "tool_used": "shell_mysql_tool"
    })
    
    # 3. 路由到下一个chain
    next_step = route(analysis)
    
    if next_step == run_controller_chain:
        # 使用真实的controller_chain
        controller_result = run_controller_chain({"analysis": analysis.dict()})
        evidences.append({
            "state": "CONTROLLER_CHAIN",
            "output": controller_result,
            "tool_used": "controller_chain"
        })
        
        # 使用工具查询broker指标
        metrics_result = query_broker_metrics("broker-2")
        evidences.append({
            "state": "TOOL_QUERY_METRICS",
            "output": metrics_result,
            "tool_used": "shell_mysql_tool"
        })
    
    # 4. 使用真实的end_chain生成最终报告
    final_report = end_chain({
        "analysis": analysis.dict(),
        "evidences": evidences
    })
    
    print(json.dumps(final_report, indent=2, ensure_ascii=False))


# -----------------------------
# 运行示例
# -----------------------------

if __name__ == '__main__':
    run_real_agent()