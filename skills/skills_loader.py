import os


def load_skills_from_dir() -> str:
    """从 skills 目录自动加载全部技能说明文件。"""
    skills_dir = os.path.dirname(__file__)
    all_skills = []
    try:
        if not os.path.exists(skills_dir):
            return ""
        for filename in os.listdir(skills_dir):
            if not filename.lower().endswith(".md"):
                continue
            file_path = os.path.join(skills_dir, filename)
            try:
                with open(file_path, "r", encoding="utf-8") as f:
                    content = f.read().strip()
                    if content:
                        all_skills.append(f"=== skills/{filename} ===\n{content}\n")
                        print(f"已加载skills文件: {filename}")
            except Exception as e:
                print(f"警告: 读取技能文件 {filename} 时出错: {e}")
        return "\n".join(all_skills) if all_skills else ""
    except Exception as e:
        print(f"读取 skills 目录时出错: {e}")
        return ""
