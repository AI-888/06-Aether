from chains.command_line_chain import run_command_line_chain
from chains.controller_chain import run_controller_chain
from chains.end_chain import end_chain
from models import RocketMQAnalysis, BrokerLogAnalysis


def route(analysis):
    """路由函数，根据分析结果选择相应的处理链"""

    # 处理新的RocketMQAnalysis模型
    if isinstance(analysis, RocketMQAnalysis):
        return route_rocketmq_analysis(analysis)

    # 处理旧的BrokerLogAnalysis模型（向后兼容）
    elif isinstance(analysis, BrokerLogAnalysis):
        return route_broker_log_analysis(analysis)

    # 处理字典格式（从API调用）
    elif isinstance(analysis, dict):
        # 尝试转换为RocketMQAnalysis
        try:
            rocketmq_analysis = RocketMQAnalysis(**analysis)
            return route_rocketmq_analysis(rocketmq_analysis)
        except:
            # 如果转换失败，尝试BrokerLogAnalysis
            try:
                broker_analysis = BrokerLogAnalysis(**analysis)
                return route_broker_log_analysis(broker_analysis)
            except:
                return end_chain

    else:
        return end_chain


def route_rocketmq_analysis(analysis: RocketMQAnalysis):
    """路由RocketMQ分析结果"""
    # 如果有推荐的下一步动作，进入命令行诊断链
    if analysis.recommended_next_actions:
        return run_command_line_chain

    return end_chain


def route_broker_log_analysis(analysis: BrokerLogAnalysis):
    """路由Broker日志分析结果（向后兼容）"""
    if analysis.next_state == "CHECK_CONTROLLER":
        return run_controller_chain
    elif analysis.next_state == "NEED_COMMAND_LINE":
        return run_command_line_chain
    return end_chain
