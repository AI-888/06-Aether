"""Skill RAG 搜索结果过滤器。

从 RAG 搜索结果中移除被 SOP Skill 步骤包含的 Atomic Skill，
避免同一能力在搜索结果中重复出现（既作为独立 Atomic Skill，又被 SOP 步骤引用）。
"""

from __future__ import annotations

from typing import Any

from loguru import logger


def filter_redundant_atomic_skills(
    skill_results: list[dict[str, Any]],
    skill_loader: Any,
) -> list[dict[str, Any]]:
    """从 RAG 搜索结果中移除被 SOP Skill 步骤包含的 Atomic Skill。

    判定"包含"的条件（满足任一即视为被包含）：
    - SOP 的某步骤 type=skill，skill 字段 == Atomic Skill 的 name
    - SOP 的某步骤 type=tool，tool 字段命中 Atomic Skill 的 tools 任一项

    Args:
        skill_results: RAG search_skills() 返回的原始结果列表
        skill_loader: RCASkillLoader 实例（用于加载 Skill 对象获取 steps）

    Returns:
        过滤后的结果列表
    """
    if len(skill_results) <= 1:
        return skill_results

    # Step 1: 收集所有 SOP Skill 的 steps 中引用的 skill_name 和 tool_name
    sop_referenced_skills: set[str] = set()   # SOP steps 中 type=skill 引用的名称
    sop_referenced_tools: set[str] = set()    # SOP steps 中 type=tool 引用的名称

    for result in skill_results:
        meta = result.get("metadata", {}) or {}
        if meta.get("skill_type") != "sop":
            continue
        skill_name = meta.get("skill_name", "")
        if not skill_name:
            continue
        skill_obj = skill_loader.get_skill(skill_name)
        if not skill_obj or not hasattr(skill_obj, "steps") or not skill_obj.steps:
            continue
        for step in skill_obj.steps:
            if step.type.value == "skill" and step.skill:
                sop_referenced_skills.add(step.skill)
            if step.type.value == "tool" and step.tool:
                sop_referenced_tools.add(step.tool)

    # 没有 SOP 或 SOP 没有引用任何东西，原样返回
    if not sop_referenced_skills and not sop_referenced_tools:
        return skill_results

    # Step 2: 过滤 Atomic Skill
    filtered: list[dict[str, Any]] = []
    for result in skill_results:
        meta = result.get("metadata", {}) or {}
        if meta.get("skill_type") != "atomic":
            # 非 Atomic（SOP 或未知类型）保留
            filtered.append(result)
            continue

        atomic_name = meta.get("skill_name", "")

        # 条件 1：Atomic Skill 的 name 被 SOP 的 step.skill 直接引用
        if atomic_name in sop_referenced_skills:
            logger.info(
                f"[SKILL-FILTER] 移除 Atomic Skill '{atomic_name}'"
                f"（被 SOP step.skill 引用）"
            )
            continue

        # 条件 2：Atomic Skill 绑定的 tool 被 SOP 的 step.tool 引用
        atomic_obj = skill_loader.get_skill(atomic_name)
        if atomic_obj:
            atomic_tools: list[str] = []
            tools_attr = getattr(atomic_obj, "tools", None)
            if isinstance(tools_attr, list):
                atomic_tools.extend(
                    t for t in tools_attr
                    if isinstance(t, str) and t
                )

            matched_tool = next((t for t in atomic_tools if t in sop_referenced_tools), None)
            if matched_tool:
                logger.info(
                    f"[SKILL-FILTER] 移除 Atomic Skill '{atomic_name}'"
                    f"（其 tool '{matched_tool}' 被 SOP step.tool 引用）"
                )
                continue

        filtered.append(result)

    removed_count = len(skill_results) - len(filtered)
    if removed_count > 0:
        logger.info(
            f"[SKILL-FILTER] 过滤完成: {len(skill_results)} → {len(filtered)} "
            f"(移除 {removed_count} 个被 SOP 包含的 Atomic Skill)"
        )

    return filtered