from typing import Dict, Any


def run_command_line_chain(context: Dict[str, Any]) -> Dict[str, Any]:
    """处理需要命令行交互的诊断场景"""
    analysis = context.get('analysis', {})

    # 根据分析结果生成命令行诊断方案
    command_line_plan = {
        "diagnosis_type": "command_line_interactive",
        "needs_command_line": True,
        "command_suggestions": "",
        "diagnosis_steps": generate_diagnosis_steps(analysis),
        "expected_output": "通过命令行收集系统状态信息进行进一步诊断"
    }

    return command_line_plan


def generate_diagnosis_steps(analysis: Dict[str, Any]) -> list:
    """根据分析结果生成具体的命令行诊断步骤"""
    suspected_root = analysis.get('suspected_root', 'unknown')
    recommended = analysis.get('recommended_next_actions', []) or []

    steps = []

    if recommended:
        return recommended

    if suspected_root == "network":
        steps = [
            "检查网络连接: ping controller和broker节点",
            "检查防火墙规则: iptables -L",
            "检查网络带宽: iftop或nethogs",
            "检查DNS解析: nslookup或dig"
        ]
    elif suspected_root == "disk_io":
        steps = [
            "检查磁盘空间: df -h",
            "检查磁盘IO性能: iostat -x 1",
            "检查文件系统: mount | grep rocketmq",
            "检查磁盘读写延迟: iotop"
        ]
    elif suspected_root == "jvm":
        steps = [
            "检查JVM内存: jstat -gc <pid>",
            "检查GC日志: tail -f gc.log",
            "检查线程状态: jstack <pid>",
            "检查JVM参数: jinfo <pid>"
        ]
    elif suspected_root == "metadata":
        steps = [
            "检查NameServer路由: mqadmin clusterList -n <namesrv>",
            "检查Topic路由: mqadmin topicRoute -n <namesrv> -t <topic>",
            "检查Broker状态: mqadmin brokerStatus -n <namesrv> -b <broker>",
            "查看NameServer日志: kubectl logs <namesrv-pod> -n rocketmq5"
        ]
    else:
        steps = [
            "系统基础检查: top, free -m, df -h",
            "网络连通性检查: ping主要节点",
            "服务状态检查: systemctl status rocketmq相关服务",
            "日志检查: tail -f broker.log和controller.log"
        ]

    return steps
