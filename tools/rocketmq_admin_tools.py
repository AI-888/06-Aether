"""
RocketMQ admin tools via kubectl exec into namesrv pod.
"""

from typing import Dict, Optional, Any, List, Callable
from datetime import datetime

from tools.kubectl_tools import run_kubectl

def _parse_raw_params(raw: str) -> List[str]:
    params: List[str] = []
    for line in raw.splitlines():
        line = line.strip()
        if not line.startswith("-"):
            continue
        # e.g. "-t,--topic <arg>         topic name"
        parts = line.split()
        if not parts:
            continue
        flag_part = parts[0]
        if "," in flag_part:
            short_flag, long_flag = flag_part.split(",", 1)
            params.append(short_flag)
            if long_flag:
                params.append(long_flag)
        else:
            params.append(flag_part)
    return params


def _parse_param_desc(raw: str) -> Dict[str, str]:
    """Parse flag -> description mapping from usage output."""
    mapping: Dict[str, str] = {}
    for line in raw.splitlines():
        line = line.rstrip()
        if not line.strip().startswith("-"):
            continue
        # e.g. "-t,--topic <arg>   topic name"
        parts = line.split(None, 2)
        if not parts:
            continue
        flag_part = parts[0]
        desc = parts[2].strip() if len(parts) >= 3 else ""
        if "," in flag_part:
            short_flag, long_flag = flag_part.split(",", 1)
            mapping[short_flag] = desc
            if long_flag:
                mapping[long_flag] = desc
        else:
            mapping[flag_part] = desc
    return mapping


def _parse_required_flags(raw: str) -> List[str]:
    """Parse required flags from usage line (flags not in brackets)."""
    usage_line = ""
    for line in raw.splitlines():
        if line.startswith("usage:"):
            usage_line = line
            break
    if not usage_line:
        return []
    # Remove bracketed optional segments like [ -n <arg> ]
    cleaned = ""
    depth = 0
    for ch in usage_line:
        if ch == "[":
            depth += 1
            continue
        if ch == "]":
            depth = max(0, depth - 1)
            continue
        if depth == 0:
            cleaned += ch
    # Extract short flags from remaining usage
    flags = []
    for token in cleaned.split():
        if token.startswith("-") and len(token) == 2:
            flags.append(token)
    return list(dict.fromkeys(flags))


ADMIN_COMMAND_SPECS: Dict[str, Dict[str, Any]] = {
    "topicRoute": {
        "desc": "Examine topic route info.",
        "raw": "usage: mqadmin topicRoute [-h] [-l] [-n <arg>] -t <arg>\n -h,--help                Print help\n -l,--list                Use list format to print data\n -n,--namesrvAddr <arg>   Name server address list, eg: '192.168.0.1:9876;192.168.0.2:9876'\n -t,--topic <arg>         topic name\n",
    },
    "topicStatus": {
        "desc": "Examine topic Status info.",
        "raw": "usage: mqadmin topicStatus [-b <arg>] [-c <arg>] [-h] [-n <arg>] -t <arg>\n -b,--brokerAddr <arg>    Broker address in format of IP:PORT\n -c,--cluster <arg>       cluster name or lmq parent topic, lmq is used to find the route.\n -h,--help                Print help\n -n,--namesrvAddr <arg>   Name server address list, eg: '192.168.0.1:9876;192.168.0.2:9876'\n -t,--topic <arg>         topic name\n",
    },
    "topicClusterList": {
        "desc": "Get cluster info for topic.",
        "raw": "usage: mqadmin topicClusterList [-h] [-n <arg>] -t <arg>\n -h,--help                Print help\n -n,--namesrvAddr <arg>   Name server address list, eg: '192.168.0.1:9876;192.168.0.2:9876'\n -t,--topic <arg>         topic name\n",
    },
    "brokerStatus": {
        "desc": "Fetch broker runtime status data.",
        "raw": "usage: mqadmin brokerStatus -b <arg> | -c <arg>  [-h] [-n <arg>]\n -b,--brokerAddr <arg>    Broker address\n -c,--clusterName <arg>   which cluster\n -h,--help                Print help\n -n,--namesrvAddr <arg>   Name server address list, eg: '192.168.0.1:9876;192.168.0.2:9876'\n",
    },
    "queryMsgById": {
        "desc": "Query Message by Id.",
        "raw": "usage: mqadmin queryMsgById [-c <arg>] [-d <arg>] [-f <arg>] [-g <arg>] [-h] -i <arg> [-n <arg>] [-s <arg>] -t\n       <arg> [-u <arg>]\n -c,--cluster <arg>         Cluster name or lmq parent topic, lmq is used to find the route.\n -d,--clientId <arg>        The consumer's client id\n -f,--bodyFormat <arg>      print message body by the specified format\n -g,--consumerGroup <arg>   consumer group name\n -h,--help                  Print help\n -i,--msgId <arg>           Message Id\n -n,--namesrvAddr <arg>     Name server address list, eg: '192.168.0.1:9876;192.168.0.2:9876'\n -s,--sendMessage <arg>     resend message\n -t,--topic <arg>           topic name\n -u,--unitName <arg>        unit name\n",
    },
    "queryMsgByKey": {
        "desc": "Query Message by Key.",
        "raw": "usage: mqadmin queryMsgByKey [-b <arg>] [-c <arg>] [-e <arg>] [-h] -k <arg> [-m <arg>] [-n <arg>] -t <arg>\n -b,--beginTimestamp <arg>   Begin timestamp(ms). default:0, eg:1676730526212\n -c,--cluster <arg>          Cluster name or lmq parent topic, lmq is used to find the route.\n -e,--endTimestamp <arg>     End timestamp(ms). default:Long.MAX_VALUE, eg:1676730526212\n -h,--help                   Print help\n -k,--msgKey <arg>           Message Key\n -m,--maxNum <arg>           The maximum number of messages returned by the query, default:64\n -n,--namesrvAddr <arg>      Name server address list, eg: '192.168.0.1:9876;192.168.0.2:9876'\n -t,--topic <arg>            Topic name\n",
    },
    "queryMsgByUniqueKey": {
        "desc": "Query Message by Unique key.",
        "raw": "usage: mqadmin queryMsgByUniqueKey [-a] [-c <arg>] [-d <arg>] [-g <arg>] [-h] -i <arg> [-n <arg>] -t <arg>\n -a,--showAll               Print all message, the limit is 32\n -c,--cluster <arg>         Cluster name or lmq parent topic, lmq is used to find the route.\n -d,--clientId <arg>        The consumer's client id\n -g,--consumerGroup <arg>   consumer group name\n -h,--help                  Print help\n -i,--msgId <arg>           Message Id\n -n,--namesrvAddr <arg>     Name server address list, eg: '192.168.0.1:9876;192.168.0.2:9876'\n -t,--topic <arg>           The topic of msg\n",
    },
    "queryMsgByOffset": {
        "desc": "Query Message by offset.",
        "raw": "usage: mqadmin queryMsgByOffset -b <arg> [-c <arg>] [-cluster <arg>] [-f <arg>] [-h] -i <arg> [-n <arg>] -o\n       <arg> -t <arg>\n -b,--brokerName <arg>    Broker Name\n -c,--count <arg>         Maximum message count.\n -cluster <arg>           Cluster name or lmq parent topic, lmq is used to find the route.\n -f,--bodyFormat <arg>    print message body by the specified format\n -h,--help                Print help\n -i,--queueId <arg>       Queue Id\n -n,--namesrvAddr <arg>   Name server address list, eg: '192.168.0.1:9876;192.168.0.2:9876'\n -o,--offset <arg>        Queue Offset\n -t,--topic <arg>         topic name\n",
    },
    "queryMsgTraceById": {
        "desc": "Query a message trace.",
        "raw": "usage: mqadmin queryMsgTraceById [-b <arg>] [-c <arg>] [-e <arg>] [-h] -i <arg> [-n <arg>] [-t <arg>]\n -b,--beginTimestamp <arg>   Begin timestamp(ms). default:0, eg:1676730526212\n -c,--maxNum <arg>           The maximum number of messages returned by the query, default:64\n -e,--endTimestamp <arg>     End timestamp(ms). default:Long.MAX_VALUE, eg:1676730526212\n -h,--help                   Print help\n -i,--msgId <arg>            Message Id\n -n,--namesrvAddr <arg>      Name server address list, eg: '192.168.0.1:9876;192.168.0.2:9876'\n -t,--traceTopic <arg>       The name value of message trace topic\n",
    },
    "printMsg": {
        "desc": "Print Message Detail.",
        "raw": "usage: mqadmin printMsg [-b <arg>] [-c <arg>] [-d <arg>] [-e <arg>] [-h] [-l <arg>] [-n <arg>] [-s <arg>] -t\n       <arg>\n -b,--beginTimestamp  <arg>   Begin timestamp[currentTimeMillis|yyyy-MM-dd#HH:mm:ss:SSS]\n -c,--charsetName  <arg>      CharsetName(eg: UTF-8,GBK)\n -d,--printBody  <arg>        print body\n -e,--endTimestamp  <arg>     End timestamp[currentTimeMillis|yyyy-MM-dd#HH:mm:ss:SSS]\n -h,--help                    Print help\n -l,--lmqParentTopic <arg>    Lmq parent topic, lmq is used to find the route.\n -n,--namesrvAddr <arg>       Name server address list, eg: '192.168.0.1:9876;192.168.0.2:9876'\n -s,--subExpression  <arg>    Subscribe Expression(eg: TagA || TagB)\n -t,--topic <arg>             topic name\n",
    },
    "brokerConsumeStats": {
        "desc": "Fetch broker consume stats data.",
        "raw": "usage: mqadmin brokerConsumeStats -b <arg> [-h] [-l <arg>] [-n <arg>] [-o <arg>] [-t <arg>]\n -b,--brokerAddr <arg>      Broker address\n -h,--help                  Print help\n -l,--level <arg>           threshold of print diff\n -n,--namesrvAddr <arg>     Name server address list, eg: '192.168.0.1:9876;192.168.0.2:9876'\n -o,--order <arg>           order topic\n -t,--timeoutMillis <arg>   request timeout Millis\n",
    },
    "browseMessage": {
        "desc": "Browse LMQ messages of the specified LMQ from the specified instant.",
        "raw": "usage: mqadmin browseMessage [-c <arg>] [-g <arg>] [-h] -i <arg> -l <arg> -n <arg> [-p <arg>] -t <arg>\n -c,--count <arg>          Number of messages to browse, optional and by default, 100\n -g,--group <arg>          Group name, without the '%LMQ%' prefix, optional and by default, 'admin'\n -h,--help                 Print help\n -i,--instant <arg>        Instant[millisSinceEpoch|yyyy-MM-dd#HH:mm:ss:SSS]\n -l,--lmq <arg>            LMQ name, without the '%LMQ%' prefix, e.g. 'DEFAULT_INSTANCE%#'\n -n,--nsAddr <arg>         Address of the name server\n -p,--printPayload <arg>   Print message payload or not, by default, false\n -t,--topic <arg>          topic to lookup route\n",
    },
    "producerConnection": {
        "desc": "Query producer's socket connection and client version.",
        "raw": "usage: mqadmin producerConnection -g <arg> [-h] [-n <arg>] -t <arg>\n -g,--producerGroup <arg>   producer group name\n -h,--help                  Print help\n -n,--namesrvAddr <arg>     Name server address list, eg: '192.168.0.1:9876;192.168.0.2:9876'\n -t,--topic <arg>           topic name\n",
    },
    "consumerConnection": {
        "desc": "Query consumer's socket connection, client version and subscription.",
        "raw": "usage: mqadmin consumerConnection [-b <arg>] -g <arg> [-h] [-n <arg>]\n -b,--brokerAddr <arg>      broker address\n -g,--consumerGroup <arg>   consumer group name\n -h,--help                  Print help\n -n,--namesrvAddr <arg>     Name server address list, eg: '192.168.0.1:9876;192.168.0.2:9876'\n",
    },
    "consumerStatus": {
        "desc": "Query consumer's internal data structure.",
        "raw": "usage: mqadmin consumerStatus [-b <arg>] -g <arg> [-h] [-i <arg>] [-n <arg>] [-s]\n -b,--brokerAddr <arg>      broker address\n -g,--consumerGroup <arg>   consumer group name\n -h,--help                  Print help\n -i,--clientId <arg>        The consumer's client id\n -n,--namesrvAddr <arg>     Name server address list, eg: '192.168.0.1:9876;192.168.0.2:9876'\n -s,--jstack                Run jstack command in the consumer progress\n",
    },
    "clusterList": {"desc": "List cluster infos.", "raw": ""},
    "topicList": {"desc": "Fetch all topic list from name server.", "raw": ""},
    "statsAll": {"desc": "Topic and Consumer tps stats.", "raw": ""},
    "allocateMQ": {"desc": "Allocate MQ.", "raw": ""},
    "checkMsgSendRT": {"desc": "Check message send response time.", "raw": ""},
    "clusterRT": {"desc": "List All clusters Message Send RT.", "raw": ""},
    "getNamesrvConfig": {"desc": "Get configs of name server.", "raw": ""},
    "getBrokerConfig": {"desc": "Get broker config by cluster or special broker.", "raw": ""},
    "getConsumerConfig": {
        "desc": "Get consumer config by subscription group name.",
        "raw": "usage: mqadmin getConsumerConfig -g <arg> [-h] [-n <arg>]\n -g,--groupName <arg>     subscription group name\n -h,--help                Print help\n -n,--namesrvAddr <arg>   Name server address list, eg: '192.168.0.1:9876;192.168.0.2:9876'\n",
    },
    "clusterAclConfigVersion": {
        "desc": "List all of acl config version information in cluster.",
        "raw": "usage: mqadmin clusterAclConfigVersion -b <arg> | -c <arg>  [-h] [-n <arg>]\n -b,--brokerAddr <arg>    query acl config version for which broker\n -c,--clusterName <arg>   query acl config version for specified cluster\n -h,--help                Print help\n -n,--namesrvAddr <arg>   Name server address list, eg: '192.168.0.1:9876;192.168.0.2:9876'\n",
    },
    "haStatus": {
        "desc": "Fetch ha runtime status data.",
        "raw": "usage: mqadmin haStatus [-b <arg>] [-c <arg>] [-h] [-i <arg>] [-n <arg>]\n -b,--brokerAddr <arg>    which broker to fetch\n -c,--clusterName <arg>   which cluster\n -h,--help                Print help\n -i,--interval <arg>      the interval(second) of get info\n -n,--namesrvAddr <arg>   Name server address list, eg: '192.168.0.1:9876;192.168.0.2:9876'\n",
    },
    "getSyncStateSet": {
        "desc": "Fetch syncStateSet for target brokers.",
        "raw": "usage: mqadmin getSyncStateSet -a <arg> [-b <arg>] [-c <arg>] [-h] [-i <arg>] [-n <arg>]\n -a,--controllerAddress <arg>   the address of controller\n -b,--brokerName <arg>          which broker to fetch\n -c,--clusterName <arg>         which cluster\n -h,--help                      Print help\n -i,--interval <arg>            the interval(second) of get info\n -n,--namesrvAddr <arg>         Name server address list, eg: '192.168.0.1:9876;192.168.0.2:9876'\n",
    },
    "getBrokerEpoch": {
        "desc": "Fetch broker epoch entries.",
        "raw": "usage: mqadmin getBrokerEpoch [-b <arg>] [-c <arg>] [-h] [-i <arg>] [-n <arg>]\n -b,--brokerName <arg>    which broker to fetch\n -c,--clusterName <arg>   which cluster\n -h,--help                Print help\n -i,--interval <arg>      the interval(second) of get info\n -n,--namesrvAddr <arg>   Name server address list, eg: '192.168.0.1:9876;192.168.0.2:9876'\n",
    },
    "getControllerMetaData": {
        "desc": "Get controller cluster's metadata.",
        "raw": "usage: mqadmin getControllerMetaData -a <arg> [-h] [-n <arg>]\n -a,--controllerAddress <arg>   the address of controller\n -h,--help                      Print help\n -n,--namesrvAddr <arg>         Name server address list, eg: '192.168.0.1:9876;192.168.0.2:9876'\n",
    },
    "getControllerConfig": {
        "desc": "Get controller config.",
        "raw": "usage: mqadmin getControllerConfig -a <arg> [-h] [-n <arg>]\n -a,--controllerAddress <arg>   Controller address list, eg: 192.168.0.1:9878;192.168.0.2:9878\n -h,--help                      Print help\n -n,--namesrvAddr <arg>         Name server address list, eg: '192.168.0.1:9876;192.168.0.2:9876'\n",
    },
    "getAcl": {
        "desc": "Get acl from cluster.",
        "raw": "usage: mqadmin getAcl -b <arg> | -c <arg>  [-h] [-n <arg>] [-s <arg>]\n -b,--brokerAddr <arg>    get acl for which broker\n -c,--clusterName <arg>   get acl for specified cluster\n -h,--help                Print help\n -n,--namesrvAddr <arg>   Name server address list, eg: '192.168.0.1:9876;192.168.0.2:9876'\n -s,--subject <arg>       the subject of acl to get\n",
    },
    "listAcl": {
        "desc": "List acl from cluster.",
        "raw": "usage: mqadmin listAcl -b <arg> | -c <arg>  [-h] [-n <arg>] [-r <arg>] [-s <arg>]\n -b,--brokerAddr <arg>    list acl for which broker.\n -c,--clusterName <arg>   list acl for specified cluster.\n -h,--help                Print help\n -n,--namesrvAddr <arg>   Name server address list, eg: '192.168.0.1:9876;192.168.0.2:9876'\n -r,--resource <arg>      the resource of acl to filter.\n -s,--subject <arg>       the subject of acl to filter.\n",
    },
    "consumer": {"desc": "Query consumer's connection, status, etc.", "raw": ""},
    "getConsumerOffset": {
        "desc": "Get consumer offset by group.topic.queueId.",
        "raw": "usage: mqadmin getConsumerOffset -b <arg> -g <arg> [-h] [-n <arg>] -q <arg> -t <arg>\n -b,--brokerAddr <arg>    set the broker address\n -g,--group <arg>         set consumer group\n -h,--help                Print help\n -n,--namesrvAddr <arg>   Name server address list, eg: '192.168.0.1:9876;192.168.0.2:9876'\n -q,--queueId <arg>       set the queue id\n -t,--topic <arg>         set the topic\n",
    }
}

# Add parsed params list to specs
for name, spec in ADMIN_COMMAND_SPECS.items():
    spec["params"] = _parse_raw_params(spec.get("raw", ""))
    spec["required"] = _parse_required_flags(spec.get("raw", ""))
    spec["param_desc"] = _parse_param_desc(spec.get("raw", ""))


def _make_admin_func(command: str) -> Callable[..., Dict[str, str]]:
    def _runner(
        k8s_namespace: str,
        admin_args: Optional[Dict[str, Any]] = None,
        raw_args: Optional[List[str]] = None,
        namesrv_addr: str = "127.0.0.1:9876",
        namesrv_pod: str = "ocloud-tdmq-rocketmq5-namesrv-0",
        namesrv_container: str = "ocloud-tdmq-rocketmq5-namesrv",
        execute: bool = False,
    ) -> Dict[str, str]:
        return run_mqadmin_tool(
            command=command,
            k8s_namespace=k8s_namespace,
            admin_args=admin_args,
            raw_args=raw_args,
            namesrv_addr=namesrv_addr,
            namesrv_pod=namesrv_pod,
            namesrv_container=namesrv_container,
            execute=execute,
        )
    _runner.__name__ = f"run_{command}"
    _runner.__doc__ = f"Admin tool for mqadmin {command}."
    return _runner


ADMIN_TOOL_RUNNERS: Dict[str, Callable[..., Dict[str, str]]] = {
    name: _make_admin_func(name) for name in ADMIN_COMMAND_SPECS.keys()
}


def get_admin_command_specs() -> Dict[str, Dict[str, Any]]:
    """Return admin command specs with params and descriptions."""
    return ADMIN_COMMAND_SPECS


def get_admin_required_flags(command: str) -> List[str]:
    """Return required short flags for an admin command."""
    spec = ADMIN_COMMAND_SPECS.get(command, {})
    return spec.get("required", [])


def get_admin_param_desc(command: str) -> Dict[str, str]:
    """Return flag -> description mapping for an admin command."""
    spec = ADMIN_COMMAND_SPECS.get(command, {})
    return spec.get("param_desc", {})


def run_mqadmin_command(
    k8s_namespace: str,
    admin_subcommand: str,
    namesrv_addr: str = "127.0.0.1:9876",
    namesrv_pod: str = "ocloud-tdmq-rocketmq5-namesrv-0",
    namesrv_container: str = "ocloud-tdmq-rocketmq5-namesrv",
    execute: bool = False,
) -> Dict[str, str]:
    """Run a mqadmin command inside the namesrv pod via kubectl exec."""
    subcmd = (
        f"exec -n {k8s_namespace} {namesrv_pod} "
        f"-c {namesrv_container} -- "
        f"bin/mqadmin {admin_subcommand} -n {namesrv_addr}"
    )
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(
        f"[{ts}] [Tool Call] rocketmq_admin input: "
        f"{{'namespace': '{k8s_namespace}', 'pod': '{namesrv_pod}', "
        f"'container': '{namesrv_container}', 'cmd': 'bin/mqadmin {admin_subcommand} -n {namesrv_addr}', "
        f"'execute': {execute}}}"
    )
    result = run_kubectl(subcmd, execute=execute)
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{ts}] [Tool Call] rocketmq_admin output: {result}")
    return result


def run_mqadmin_tool(
    command: str,
    k8s_namespace: str,
    admin_args: Optional[Dict[str, Any]] = None,
    raw_args: Optional[List[str]] = None,
    namesrv_addr: str = "127.0.0.1:9876",
    namesrv_pod: str = "ocloud-tdmq-rocketmq5-namesrv-0",
    namesrv_container: str = "ocloud-tdmq-rocketmq5-namesrv",
    execute: bool = False,
) -> Dict[str, str]:
    """Run a named mqadmin command with provided flags/args."""
    parts: List[str] = [command]
    if admin_args:
        for key, value in admin_args.items():
            flag = key if str(key).startswith("-") else f"-{key}"
            if value is None or value == "":
                parts.append(flag)
            else:
                parts.append(f"{flag} {value}")
    if raw_args:
        parts.extend(raw_args)
    admin_subcommand = " ".join(parts)
    return run_mqadmin_command(
        k8s_namespace=k8s_namespace,
        admin_subcommand=admin_subcommand,
        namesrv_addr=namesrv_addr,
        namesrv_pod=namesrv_pod,
        namesrv_container=namesrv_container,
        execute=execute,
    )


def run_mqadmin_topic_list(
    k8s_namespace: str,
    keyword: Optional[str] = None,
    namesrv_addr: str = "127.0.0.1:9876",
    namesrv_pod: str = "ocloud-tdmq-rocketmq5-namesrv-0",
    namesrv_container: str = "ocloud-tdmq-rocketmq5-namesrv",
    execute: bool = False,
) -> Dict[str, str]:
    """List topics via mqadmin topicList (optionally grepping by keyword)."""
    admin_subcommand = "topicList"
    if not keyword:
        return run_mqadmin_command(
            k8s_namespace=k8s_namespace,
            admin_subcommand=admin_subcommand,
            namesrv_addr=namesrv_addr,
            namesrv_pod=namesrv_pod,
            namesrv_container=namesrv_container,
            execute=execute,
        )
    subcmd = (
        f"exec -n {k8s_namespace} {namesrv_pod} "
        f"-c {namesrv_container} -- "
        f"bin/mqadmin {admin_subcommand} -n {namesrv_addr} | grep {keyword}"
    )
    return run_kubectl(subcmd, execute=execute)
