"""RCA 报告生成器。

生成结构化的 RCA 报告，支持 JSON 和 Markdown 两种输出格式。
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from typing import Any

from nanobot.rca.context import StepContext, StepTrace


@dataclass
class RCAReport:
    """RCA 报告结构。

    Attributes:
        fault_summary: 故障摘要
        root_cause: 根因判断
        confidence: 置信度 (0.0 - 1.0)
        execution_traces: 执行步骤轨迹
        recommendations: 建议修复操作
        skill_name: 使用的 Skill 名称
        skill_version: 使用的 Skill 版本
        start_time: 开始时间戳
        end_time: 结束时间戳
    """
    fault_summary: str = ""
    root_cause: str = ""
    confidence: float = 0.0
    execution_traces: list[StepTrace] = field(default_factory=list)
    recommendations: list[str] = field(default_factory=list)

    # 元数据
    skill_name: str | None = None
    skill_version: str | None = None
    start_time: float = 0.0
    end_time: float = 0.0

    @property
    def duration(self) -> float:
        """总执行耗时（秒）。"""
        return self.end_time - self.start_time

    def to_json(self) -> str:
        """输出结构化 JSON 格式。"""
        data = {
            "fault_summary": self.fault_summary,
            "root_cause": self.root_cause,
            "confidence": self.confidence,
            "recommendations": self.recommendations,
            "execution_traces": [t.to_dict() for t in self.execution_traces],
            "metadata": {
                "skill_name": self.skill_name,
                "skill_version": self.skill_version,
                "start_time": self.start_time,
                "end_time": self.end_time,
                "duration_seconds": round(self.duration, 3),
            },
        }
        return json.dumps(data, ensure_ascii=False, indent=2, default=str)

    def to_markdown(self) -> str:
        """输出人类可读 Markdown 格式。"""
        lines: list[str] = []
        lines.append("# RCA 分析报告")
        lines.append("")

        # 元数据
        if self.skill_name:
            lines.append(f"**使用 Skill**: {self.skill_name} v{self.skill_version or '?'}")
        lines.append(f"**总耗时**: {self.duration:.2f} 秒")
        lines.append("")

        # 故障摘要
        lines.append("## 故障摘要")
        lines.append("")
        lines.append(self.fault_summary or "无")
        lines.append("")

        # 根因判断
        lines.append("## 根因判断")
        lines.append("")
        lines.append(self.root_cause or "未能确定根因")
        lines.append("")
        lines.append(f"**置信度**: {self.confidence:.0%}")
        lines.append("")

        # 建议修复操作
        lines.append("## 建议修复操作")
        lines.append("")
        if self.recommendations:
            for i, rec in enumerate(self.recommendations, 1):
                lines.append(f"{i}. {rec}")
        else:
            lines.append("无建议")
        lines.append("")

        # 执行步骤轨迹
        lines.append("## 执行步骤轨迹")
        lines.append("")
        if self.execution_traces:
            lines.append("| 步骤 | 类型 | 状态 | 耗时 |")
            lines.append("|------|------|------|------|")
            for trace in self.execution_traces:
                status_emoji = "✅" if trace.status == "success" else "❌"
                lines.append(
                    f"| {trace.step_id} | {trace.step_type} | "
                    f"{status_emoji} {trace.status} | {trace.duration:.2f}s |"
                )
        else:
            lines.append("无执行记录")
        lines.append("")

        return "\n".join(lines)


class ReportGenerator:
    """报告生成器。

    从 StepContext 中汇总执行结果，生成完整的 RCA 报告。
    """

    @staticmethod
    def generate(
        ctx: StepContext,
        skill_name: str | None = None,
        skill_version: str | None = None,
        start_time: float = 0.0,
    ) -> RCAReport:
        """从 StepContext 汇总生成 RCA 报告。

        Args:
            ctx: 步骤执行上下文
            skill_name: Skill 名称
            skill_version: Skill 版本
            start_time: 执行开始时间戳

        Returns:
            生成的 RCA 报告对象
        """
        traces = ctx.get_all_traces()
        outputs = ctx.get_all_outputs()

        # 提取根因和建议（从最后的 RCD 或 conclusion 步骤）
        root_cause = ""
        solution = ""
        summary = ""
        recommendation = ""

        for step_id, output in outputs.items():
            if "root_cause" in output:
                root_cause = str(output["root_cause"])
            if "solution" in output:
                solution = str(output["solution"])
            if "summary" in output:
                summary = str(output["summary"])
            if "recommendation" in output:
                recommendation = str(output["recommendation"])

        # 构建建议列表
        recommendations: list[str] = []
        if solution:
            # 按换行分割 solution 为多条建议
            for line in solution.strip().split("\n"):
                line = line.strip()
                if line:
                    recommendations.append(line)
        if recommendation:
            recommendations.append(recommendation)

        # 确定置信度（基于执行成功率）
        total_steps = len(traces)
        success_steps = sum(1 for t in traces if t.status == "success")
        confidence = success_steps / total_steps if total_steps > 0 else 0.0

        return RCAReport(
            fault_summary=summary or f"基于 Skill '{skill_name}' 的自动化根因分析",
            root_cause=root_cause,
            confidence=confidence,
            execution_traces=traces,
            recommendations=recommendations,
            skill_name=skill_name,
            skill_version=skill_version,
            start_time=start_time,
            end_time=time.time(),
        )
