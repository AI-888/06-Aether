import os
import sys
from datetime import datetime

from langchain_community.chat_models import ChatOllama

from chains.troubleshoot_state_machine import build_troubleshoot_graph
from knowledge_base import build_index, load_index, search
from tools.tool_registry import get_admin_param_descs


def load_prompt_from_file():
    """从 prompts 目录加载所有提示词文件内容，并拼接为一个字符串。"""
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


def _load_knowledge_index():
    """加载或构建 RocketMQ 知识库索引。"""
    data_dir = os.path.join(os.path.dirname(__file__), "rocketmq-knowledge")
    index_path = os.path.join(os.path.dirname(__file__), "knowledge_base", "index.json")
    if os.path.isfile(index_path):
        return load_index(index_path)
    if os.path.isdir(data_dir):
        return build_index(data_dir, index_path)
    return None


def _print_step(step_no: int, title: str, message: str = "") -> None:
    """统一步骤日志输出。"""
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print("\n" + "=" * 72)
    print(f"[{ts}] [Step {step_no}] {title}")
    print("=" * 72)
    if message:
        print(message)


def _build_kb_context(index, user_msg: str, top_k: int = 3) -> str:
    """基于知识库检索结果拼接上下文与摘要。"""
    if not index:
        return ""
    results = search(index, user_msg, top_k=top_k)
    if not results:
        return ""
    _print_step(1, "Knowledge Base 检索摘要")
    for idx, item in enumerate(results, 1):
        title = item.get("title", "")
        category = item.get("category", "unknown")
        heading = item.get("heading", "")
        score = item.get("score", 0.0)
        loc = item.get("path", "")
        if heading:
            print(f"{idx}. [{category}] {title} / {heading} (score={score:.3f})")
        else:
            print(f"{idx}. [{category}] {title} (score={score:.3f})")
        print(f"   {loc}")
    parts = ["=== Knowledge Base ==="]
    for item in results:
        snippet = item["text"].strip()
        if len(snippet) > 1200:
            snippet = snippet[:1200] + "..."
        title = item.get("title", "")
        heading = item.get("heading", "")
        category = item.get("category", "unknown")
        label = f"[{category}] {title}"
        if heading:
            label = f"{label} / {heading}"
        parts.append(f"{label}\n{item['path']}\n{snippet}")
    return "\n\n".join(parts)


def _ask(prompt: str) -> str:
    """同步向用户提问补参。"""
    return input(prompt).strip()


def process_user_msg(user_msg, prompt_content, llm, kb_index=None):
    """单次请求入口：调用状态机执行排障，并格式化输出结果。"""
    _print_step(0, "输入内容", user_msg)

    kb_context = _build_kb_context(kb_index, user_msg)
    if kb_context:
        _print_step(2, "Knowledge Base 上下文已注入")
        prompt_content = f"{prompt_content}\n\n{kb_context}"

    graph = build_troubleshoot_graph().compile()
    state = graph.invoke({
        "user_msg": user_msg,
        "prompt_content": prompt_content,
        "llm": llm,
        "results": [],
    })

    if state.get("intent_data"):
        _print_step(3, "意图识别结果", str(state["intent_data"]))

    if state.get("error"):
        while state.get("error") and state.get("missing_params"):
            red_err = f"\033[31m{state['error']}\033[0m"
            _print_step(3, "信息不足，无法继续", red_err)
            answers = {}
            param_descs = {}
            if state.get("missing_for_tool"):
                param_descs = get_admin_param_descs(state["missing_for_tool"])
            skipped = False
            skipped_params = []
            for p in state["missing_params"]:
                desc = param_descs.get(p, "")
                if p == "instance_id":
                    desc = "RocketMQ 实例ID（格式 rmq-xxxx 或 rocketmq-xxxx）"
                elif p == "namespace":
                    desc = "RocketMQ 命名空间（MQ_INT 开头）"
                label = f"{p}"
                if desc:
                    label = f"{p} ({desc})"
                # allow empty input: treat as blank and continue
                val = _ask(f"\033[31m请输入 {label} (可留空): \033[0m")
                if not val:
                    skipped = True
                    skipped_params.append(p)
                answers[p] = val
            intent_data = dict(state.get("intent_data", {}))
            admin_args = dict(intent_data.get("admin_args") or {})
            for k, v in answers.items():
                if not v:
                    continue
                # only flags are passed to admin_args; instance_id/namespace are semantic fields
                if k.startswith("-"):
                    admin_args[k] = v
                # also fill semantic fields for normalization
                if k in ("-t", "--topic"):
                    intent_data["topic"] = v
                elif k in ("-g", "--group", "--groupName", "--consumerGroup", "--producerGroup"):
                    intent_data["group"] = v
                elif k in ("-b", "--brokerAddr", "--brokerName"):
                    intent_data["broker"] = v
                elif k in ("-i", "--msgId"):
                    intent_data["msg_id"] = v
                elif k in ("-k", "--msgKey"):
                    intent_data["msg_key"] = v
                elif k in ("-q", "--queueId"):
                    intent_data["queue_id"] = v
                elif k in ("-o", "--offset"):
                    intent_data["offset"] = v
                elif k in ("-c", "--cluster", "--clusterName"):
                    intent_data["cluster"] = v
                elif k == "instance_id":
                    intent_data["instance_id"] = v
                elif k == "namespace":
                    intent_data["namespace"] = v
                elif k == "topic":
                    intent_data["topic"] = v
                elif k in ("group", "consumerGroup", "producerGroup"):
                    intent_data["group"] = v
                elif k == "brokerAddr":
                    intent_data["broker"] = v
            intent_data["admin_args"] = admin_args
            if skipped_params:
                intent_data["skipped_params"] = skipped_params
            if "intents" not in intent_data:
                intent_data["intents"] = state.get("intents", [])
            state = graph.invoke({
                "user_msg": user_msg,
                "prompt_content": prompt_content,
                "llm": llm,
                "results": [],
                "intent_data": intent_data,
                "intents": intent_data.get("intents", []),
                "skipped_params": skipped_params,
            })
            if skipped and state.get("error"):
                _print_step(3, "补参已跳过", "用户选择留空，按跳过参数继续执行。")
                # continue loop only if still missing non-skipped params
        if state.get("error"):
            red_err = f"\033[31m{state['error']}\033[0m"
            _print_step(3, "信息不足，无法继续", red_err)
            return

    if state.get("resolved_real_topic") or state.get("resolved_real_group") or state.get("resolved_instance_id"):
        rt = state.get("resolved_real_topic") or "-"
        rg = state.get("resolved_real_group") or "-"
        iid = state.get("resolved_instance_id") or "-"
        ns = state.get("resolved_namespace") or "-"
        _print_step(3, "真实参数", f"instance_id={iid}; namespace={ns}; real_topic={rt}; real_group={rg}")

    results = state.get("results", [])
    if results:
        try:
            skills_context = state.get("skills_content", "")
            fmt_prompt = (
                "请将以下工具执行结果整理为可读的摘要，输出纯文本，不要Markdown。\n\n"
                f"{skills_context}\n\n{results}" if skills_context else f"{results}"
            )
            fmt_resp = llm.invoke(fmt_prompt)
            _print_step(4, "工具结果格式化输出", getattr(fmt_resp, "content", str(fmt_resp)))
        except Exception as e:
            print(f"[Format Error] {e}")
    else:
        intent_data = state.get("intent_data", {})
        intents = intent_data.get("intents", []) or []
        if intent_data.get("info_only") or not intents:
            try:
                answer_prompt = (
                    "基于知识库内容回答用户问题，输出纯文本，不要Markdown。"
                )
                resp = llm.invoke(f"{answer_prompt}\n\n用户问题：{user_msg}")
                _print_step(4, "知识问答输出", getattr(resp, "content", str(resp)))
            except Exception as e:
                print(f"[Answer Error] {e}")
        else:
            _print_step(4, "未识别到可执行意图")


def interactive_mode(prompt_content, llm, kb_index):
    """交互模式：循环读取用户输入并执行排障。"""
    print("=== RocketMQ智能诊断Agent（状态机版） ===")
    if prompt_content:
        file_count = prompt_content.count("===")
        print(f"✓ 已加载 {file_count} 个提示词文件")
    if kb_index:
        print("✓ 已加载 RocketMQ 知识库索引")
    print("\033[31m请输入内容进行分析（输入'quit'或'退出'结束程序）\033[0m")
    print("-" * 50)

    while True:
        try:
            user_input = input("\n\033[31m请输入内容: \033[0m").strip()
            if user_input.lower() in ["quit", "退出", "exit"]:
                print("感谢使用，再见！")
                break
            if not user_input:
                print("输入不能为空，请重新输入")
                continue
            process_user_msg(user_input, prompt_content, llm, kb_index=kb_index)
        except KeyboardInterrupt:
            print("\n\n程序被中断，再见！")
            break
        except Exception as e:
            print(f"处理过程中出现错误: {e}")


def main():
    """主入口：加载 prompt、初始化 LLM、进入命令行或交互模式。"""
    prompt_content = load_prompt_from_file()
    kb_index = _load_knowledge_index()
    llm = ChatOllama(model="qwen2.5:1.5b", temperature=0)

    if len(sys.argv) > 1:
        process_user_msg(sys.argv[1], prompt_content, llm, kb_index=kb_index)
    else:
        interactive_mode(prompt_content, llm, kb_index)


if __name__ == "__main__":
    main()
