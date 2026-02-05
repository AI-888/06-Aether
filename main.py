import os
import sys

from langchain_community.chat_models import ChatOllama

from chains import run_intent_router_chain
from tools.kubectl_tools import run_kubectl


def load_prompt_from_file():
    """从prompts目录加载所有提示词文件内容"""
    prompts_dir = os.path.join(os.path.dirname(__file__), "prompts")
    all_prompts = []

    try:
        if not os.path.exists(prompts_dir):
            print(f"警告: 提示词目录不存在 {prompts_dir}")
            return ""

        for filename in os.listdir(prompts_dir):
            if not filename.endswith(".prompt"):
                continue
            file_path = os.path.join(prompts_dir, filename)
            try:
                with open(file_path, "r", encoding="utf-8") as f:
                    content = f.read().strip()
                    if content:
                        all_prompts.append(f"=== {filename} ===\n{content}\n")
            except Exception as e:
                print(f"警告: 读取文件 {filename} 时出错: {e}")

        return "\n".join(all_prompts) if all_prompts else ""

    except Exception as e:
        print(f"读取提示词目录时出错: {e}")
        return ""


def _extract_kv(text, key):
    import re
    patterns = [
        rf"{key}\\s*=\\s*([\\w\\-\\.]+)",
        rf"{key}\\s*:\\s*([\\w\\-\\.]+)",
    ]
    for p in patterns:
        m = re.search(p, text, re.IGNORECASE)
        if m:
            return m.group(1)
    return None


def process_user_msg(user_msg, prompt_content, llm, interactive=False):
    print("\n" + "=" * 72)
    print("== 输入内容")
    print("=" * 72)
    print(user_msg + "\n")

    # 意图识别
    intent_data = run_intent_router_chain(llm, user_msg, base_prompt=prompt_content)
    intents = intent_data.get("intents", []) or []

    print("\n" + "=" * 72)
    print("== 意图识别结果")
    print("=" * 72)
    print(intent_data)

    results = []

    # Case 1: kubectl 列表
    if "list_broker_pods" in intents:
        cmd = "kubectl get pods -Ao wide | grep rocketmq5-broker"
        res = run_kubectl(cmd[len("kubectl "):], execute=True)
        results.append({"action": cmd, "result": res})
    if "list_namesrv_pods" in intents:
        cmd = "kubectl get pods -Ao wide | grep rocketmq5-namesrv"
        res = run_kubectl(cmd[len("kubectl "):], execute=True)
        results.append({"action": cmd, "result": res})
    if "list_proxy_pods" in intents:
        cmd = "kubectl get pods -Ao wide | grep rocketmq5-proxy"
        res = run_kubectl(cmd[len("kubectl "):], execute=True)
        results.append({"action": cmd, "result": res})

    # Case 2: 发送消息失败 -> 校验 topic 是否存在
    if "send_fail_check" in intents:
        instance_id = intent_data.get("instance_id") or _extract_kv(user_msg, "instance_id")
        namespace = intent_data.get("namespace") or _extract_kv(user_msg, "namespace")
        topic = intent_data.get("topic") or _extract_kv(user_msg, "topic")

        def _ask(prompt):
            return input(prompt).strip() if interactive else ""

        if not instance_id:
            if interactive:
                instance_id = _ask("请输入实例id（格式 rmq-xxxx 或 rocketmq-xxxx）：")
            if not instance_id:
                print("\n" + "=" * 72)
                print("== 信息不足，无法继续")
                print("=" * 72)
                print("缺少实例id。")
                return

        if not topic:
            if interactive:
                topic = _ask("请输入 topic：")
            if not topic:
                print("\n" + "=" * 72)
                print("== 信息不足，无法继续")
                print("=" * 72)
                print("缺少 topic。")
                return

        # rmq-xxxx 实例不需要 namespace
        if not (instance_id or "").lower().startswith("rmq-"):
            if not namespace:
                if interactive:
                    namespace = _ask("请输入命名空间（MQ_INT 开头）：")
                if not namespace:
                    print("\n" + "=" * 72)
                    print("== 信息不足，无法继续")
                    print("=" * 72)
                    print("缺少命名空间。")
                    return

        if instance_id.lower().startswith("rocketmq-"):
            ns = "tce"
            real_topic = f"{namespace}%{topic}"
        else:
            ns = namespace
            real_topic = f"{instance_id.replace('-', '')}%{topic}"

        cmd = (
            f"kubectl exec -it -n {ns} ocloud-tdmq-rocketmq5-namesrv-0 "
            f"-c ocloud-tdmq-rocketmq5-namesrv -- "
            f"bin/mqadmin topicList -n 127.0.0.1:9876 | grep {real_topic}"
        )
        res = run_kubectl(cmd[len("kubectl "):], execute=True)
        results.append({"action": cmd, "result": res})

        # 诊断 topic 是否存在
        output = res.get("output", "")
        if output and real_topic in output:
            print("\n" + "=" * 72)
            print("== 诊断结论")
            print("=" * 72)
            print("topic 存在，继续下一步排查。")
        else:
            print("\n" + "=" * 72)
            print("== 诊断结论")
            print("=" * 72)
            print("topic 不存在。请确认 topic 配置或创建主题。")

    # LLM 格式化输出
    if results:
        try:
            fmt_prompt = (
                "请将以下工具执行结果整理为可读的摘要，输出纯文本，不要Markdown。\n\n"
                f"{results}"
            )
            fmt_resp = llm.invoke(fmt_prompt)
            print("\n" + "=" * 72)
            print("== 工具结果格式化输出")
            print("=" * 72)
            print(getattr(fmt_resp, "content", str(fmt_resp)))
        except Exception as e:
            print(f"[Format Error] {e}")
    else:
        print("\n" + "=" * 72)
        print("== 未识别到可执行意图")
        print("=" * 72)


def interactive_mode(prompt_content, llm):
    print("=== RocketMQ智能诊断Agent（精简版） ===")
    if prompt_content:
        file_count = prompt_content.count("===")
        print(f"✓ 已加载 {file_count} 个提示词文件")
    print("请输入内容进行分析（输入'quit'或'退出'结束程序）")
    print("-" * 50)

    while True:
        try:
            user_input = input("\n请输入内容: ").strip()
            if user_input.lower() in ["quit", "退出", "exit"]:
                print("感谢使用，再见！")
                break
            if not user_input:
                print("输入不能为空，请重新输入")
                continue
            process_user_msg(user_input, prompt_content, llm, interactive=True)
        except KeyboardInterrupt:
            print("\n\n程序被中断，再见！")
            break
        except Exception as e:
            print(f"处理过程中出现错误: {e}")


def main():
    prompt_content = load_prompt_from_file()
    llm = ChatOllama(model="qwen2.5:1.5b", temperature=0)

    if len(sys.argv) > 1:
        process_user_msg(sys.argv[1], prompt_content, llm)
    else:
        interactive_mode(prompt_content, llm)


if __name__ == "__main__":
    main()
