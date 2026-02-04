import os
import sys

from langchain_community.chat_models import ChatOllama

from chains.broker_log_chain import build_chain, parse_analysis_result
from chains import run_master_chain
from router import route


def load_prompt_from_file():
    """从prompts目录加载所有提示词文件内容"""
    prompts_dir = os.path.join(os.path.dirname(__file__), "prompts")
    all_prompts = []

    try:
        # 检查目录是否存在
        if not os.path.exists(prompts_dir):
            print(f"警告: 提示词目录不存在 {prompts_dir}")
            return ""

        # 遍历目录中的所有.prompt文件
        for filename in os.listdir(prompts_dir):
            file_path = os.path.join(prompts_dir, filename)
            try:
                with open(file_path, 'r', encoding='utf-8') as f:
                    content = f.read().strip()
                    if content:
                        # 添加文件标题和内容
                        all_prompts.append(f"=== {filename} ===\n{content}\n")
            except Exception as e:
                print(f"警告: 读取文件 {filename} 时出错: {e}")

        # 合并所有提示词内容
        if all_prompts:
            return "\n".join(all_prompts)
        else:
            print("警告: 未找到任何.prompt文件")
            return ""

    except Exception as e:
        print(f"读取提示词目录时出错: {e}")
        return ""


def main():
    # 加载提示词内容
    prompt_content = load_prompt_from_file()

    # 使用ollama本地模型
    llm = ChatOllama(model="qwen2.5:1.5b", temperature=0)
    chain = build_chain(llm)

    # 检查是否有命令行参数
    if len(sys.argv) > 1:
        # 命令行参数模式
        user_msg = sys.argv[1]
        process_user_msg(user_msg, chain, prompt_content, llm)
    else:
        # 交互式对话模式
        interactive_mode(chain, prompt_content, llm)


def process_user_msg(user_msg, chain, prompt_content, llm):
    """处理用户消息"""
    print(f"分析输入内容:\n{user_msg}\n")

    # 如果有提示词内容，显示加载状态
    if prompt_content:
        # 统计加载的文件数量
        file_count = prompt_content.count("===")
        print(f"✓ 已加载 {file_count} 个RocketMQ专家提示词文件")

    # 从用户输入中提取 namespace/topic/group（格式：key=value 或 key: value）
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

    namespace = _extract_kv(user_msg, "namespace")
    topic = _extract_kv(user_msg, "topic")
    group = _extract_kv(user_msg, "group")

    # 调用总控顺序 Chain（执行命令）
    master_result = run_master_chain({
        "user_msg": user_msg,
        "log_text": user_msg,
        "execute": True,
        "namespace": namespace,
        "topic": topic,
        "group": group,
        "llm": llm,
        "base_prompt": prompt_content,
    })
    print("=== Master Chain 输出 ===")
    print(master_result)

    # 调用分析链（打印上下文与原始输出）
    model_input = {
        "user_msg": user_msg,
        "intents": master_result.get("context", {}).get("intent_router", {}),
    }
    print("=== LLM上下文（平衡模式） ===")
    print(model_input)
    raw_response = chain.invoke(model_input)
    print("=== LLM原始输出 ===")
    print(raw_response.content)

    # 解析分析结果
    analysis = parse_analysis_result(raw_response)

    # 显示分析结果（解析后的JSON）
    print("=== Agent分析结果 ===")
    print(f"问题范围: {analysis.problem_scope.value}")
    print(f"疑似对象: {analysis.suspected_object.value if analysis.suspected_object else 'null'}")
    print(f"疑似组件: {analysis.suspected_component.value if analysis.suspected_component else 'null'}")
    print(f"怀疑根因: {analysis.suspected_root.value}")
    print(f"置信度: {analysis.confidence:.2f}")

    # 显示关键证据
    if analysis.key_evidence:
        print("\n关键证据:")
        for i, evidence in enumerate(analysis.key_evidence, 1):
            print(f"  {i}. {evidence}")

    # 显示建议操作
    if analysis.recommended_next_actions:
        print("\n建议的下一步操作:")
        for i, action in enumerate(analysis.recommended_next_actions, 1):
            print(f"  {i}. {action}")

    # 路由到相应的处理链
    try:
        next_step = route(analysis)
        if next_step:
            result = next_step({"analysis": analysis.model_dump()})

            # 显示路由结果
            if result.get("diagnosis_type") == "command_line_interactive":
                print("\n=== 命令行诊断方案 ===")
                for i, step in enumerate(result.get("diagnosis_steps", []), 1):
                    print(f"{i}. {step}")
                print(f"\n预期输出: {result.get('expected_output', '')}")
            else:
                print(f"\n=== 路由结果 ===")
                print(f"处理结果: {result}")
    except Exception as e:
        print(f"路由处理异常: {e}")


def interactive_mode(chain, prompt_content, llm):
    """交互式对话模式"""
    print("=== RocketMQ智能诊断Agent ===")
    if prompt_content:
        # 统计加载的文件数量
        file_count = prompt_content.count("===")
        print(f"✓ 已加载 {file_count} 个专业RocketMQ专家提示词文件")
    print("功能：支持分析以下内容：")
    print("  - Broker / NameServer 日志片段")
    print("  - mqadmin 命令输出")
    print("  - kubectl 命令输出")
    print("  - 用户描述的问题")
    print("请输入内容进行分析（输入'quit'或'退出'结束程序）")
    print("-" * 50)

    while True:
        try:
            user_input = input("\n请输入内容: ").strip()

            if user_input.lower() in ['quit', '退出', 'exit']:
                print("感谢使用，再见！")
                break

            if not user_input:
                print("输入不能为空，请重新输入")
                continue

            process_user_msg(user_input, chain, prompt_content, llm)

        except KeyboardInterrupt:
            print("\n\n程序被中断，再见！")
            break
        except Exception as e:
            print(f"处理过程中出现错误: {e}")


if __name__ == "__main__":
    main()
