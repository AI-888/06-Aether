def run_controller_chain(context: dict):
    return {
        "root_cause": "controller heartbeat timeout",
        "suggestion": "check controller JVM GC and network",
    }
