"""AgenticPipelineWorkflow 的数据类型与纯函数（自 agentic_pipeline.py 拆出）"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Optional

from agent.core.engine.state_machine import Event, State, StateMachine, TRANSITIONS


def _now_iso() -> str:
    """返回当前 ISO 时间字符串（G14 告警标记用）。"""
    from datetime import datetime

    return datetime.now().isoformat(timespec="seconds")


@dataclass
class PipelineResult:
    """全流程自主结果。"""

    planned: bool = False
    chapters_written: int = 0
    final_chapter: int = 0
    health_report: Optional[dict[str, Any]] = None
    escalated: bool = False
    escalated_reason: str = ""
    blocked: bool = False
    block_reason: str = ""
    engine: str = "Agentic"
    # G4 新增字段
    tripped: bool = False  # 熔断标志
    schema_degraded: bool = False  # Schema 降级标志
    # G6 新增字段（B5-3 修复：Guardrails 结果进结构化结果，供 --json 审计）
    guardrails: Optional[dict[str, Any]] = None
    # G7 新增字段（成本透明，拍板 4）：run 收尾填充；--no-cost 时 CLI 置 None
    cost: Optional[dict[str, Any]] = None
    # ---- G8 新增字段（主线推进 + 结局模式，拍板 6）：run 收尾填充；关闭开关置 None ----
    mainline: Optional[dict[str, Any]] = None
    ending: Optional[dict[str, Any]] = None
    # ---- G9 新增字段（进度事件流 + 失败自助恢复，拍板 2/5/6）：run 收尾填充 ----
    progress_file: Optional[str] = None        # progress.json 绝对路径；--no-progress 置 null
    failures: list[dict[str, Any]] = field(default_factory=list)
    stream: Optional[dict[str, Any]] = None    # 渲染元信息；--no-stream 置 null
    summary: Optional[dict[str, Any]] = None   # build_run_summary 结果（运行摘要）

    def to_dict(self) -> dict[str, Any]:
        return {
            "planned": self.planned,
            "chapters_written": self.chapters_written,
            "final_chapter": self.final_chapter,
            "health_report": self.health_report,
            "escalated": self.escalated,
            "escalated_reason": self.escalated_reason,
            "blocked": self.blocked,
            "block_reason": self.block_reason,
            "engine": self.engine,
            "tripped": self.tripped,
            "schema_degraded": self.schema_degraded,
            "guardrails": self.guardrails,
            # ---- G7（只增不删）：成本汇总 ----
            "cost": self.cost,
            # ---- G8（只增不删）：主线推进 + 结局模式 ----
            "mainline": self.mainline,
            "ending": self.ending,
            # ---- G9（只增不删）：进度事件流 + 失败自助恢复 ----
            "progress_file": self.progress_file,
            "failures": self.failures,
            "stream": self.stream,
            "summary": self.summary,
        }


# 注入的 writer 工作流：有 run() -> 结果对象（含 chapter_num/chapter_text 等），
# 且会自行推进状态机进度并落盘。测试时可替换为 stub。
WriterWorkflow = Any
EditorLike = Any
EvaluatorLike = Any
PlannerLike = Any


@dataclass
class _PlanStepResult:
    """单步规划结果（供 ``_safe_step`` 返回）。"""

    ok: bool
    value: Any = None


# 状态规范链（用于 ``_advance_state_to`` 单向推进）。
_CANON = [
    State.INIT, State.CONFIGURING, State.DISCUSSING, State.ARCHITECTING,
    State.ARCH_CONFIRMED, State.OUTLINING, State.CHARACTER_DESIGN,
    State.WRITING, State.PAUSED, State.COMPLETED, State.ARCH_REVISION,
]
_EVENTS = [
    Event.START, Event.DISCUSS, Event.GENERATE_ARCHITECTURE,
    Event.CONFIRM_ARCHITECTURE, Event.GENERATE_OUTLINE,
    Event.DESIGN_CHARACTERS, Event.WRITE,
]


def build_rewrite_hint(report: Any, chapter_nums: list[int]) -> str:
    """把上一轮全书体检的失败项编译成写给 Writer 的针对性修正提示。

    回溯重写若不带反馈，Writer 只会盲目重生成、极易再次不达标而触发无谓上报。
    这里把未达标维度、回溯原因与重写章节区间浓缩为可读指令，让重写「对症」。
    """
    if report is None:
        return ""
    failed = [d for d in getattr(report, "dimensions", []) or [] if not d.passed]
    lo = chapter_nums[0] if chapter_nums else "?"
    hi = f"–{chapter_nums[-1]}" if chapter_nums else ""
    lines = [
        "【全书体检未达标 · 针对性重写要求】",
        f"以下章节被回退并重写：第 {lo}{hi} 章。",
    ]
    if failed:
        lines.append("上轮未达标维度（请在本轮重写中重点修正）：")
        for d in failed:
            arrow = "≥" if d.direction == ">=" else "≤"
            lines.append(
                f"- {d.label}（{d.name}）：实测 {d.value} {arrow} 合格线 {d.threshold}"
            )
    reason = getattr(report, "escalated_reason", "") or ""
    if reason:
        lines.append(f"上下文：{reason}")
    plan = getattr(report, "repair", None)
    if plan is not None:
        r = getattr(plan, "reason", "") or ""
        if r:
            lines.append(f"回溯原因：{r}")
    lines.append(
        "请在重写时针对以上维度改善（如补全伏笔回收、修复人设/设定冲突、"
        "提升连贯与追读节奏、控制注水），并保持与世界观/角色档案一致。"
    )
    return "\n".join(lines)


# G10（拍板 3）：预算档位降档方向 quality→balanced→economy（模块级，供测试引用）
_DOWNGRADE_ORDER: list[str] = ["quality", "balanced", "economy"]
