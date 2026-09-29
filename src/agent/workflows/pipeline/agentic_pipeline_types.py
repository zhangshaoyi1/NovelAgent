"""AgenticPipelineWorkflow 的数据类型与纯函数（自 agentic_pipeline.py 拆出）"""

from __future__ import annotations

import json
import re
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


def _issue_anchors(text: str) -> set[int]:
    """从 issue 文本提取章节锚点（ch078 / 第78章 → {78}）。"""
    return {
        int(m.group(1) or m.group(2))
        for m in re.finditer(r"ch0*(\d+)|第\s*(\d+)\s*章", text or "")
    }


def _dim_issue_lines(
    dim: Any, max_issues: int = 8, max_desc: int = 200, only_chapter: int | None = None
) -> list[str]:
    """从失败维度的评分证据中提取逐条问题明细（HA-Eval 修复 2026-09-08）。

    ``EvalEvidence.issues`` 是评审 LLM 列举的 ``[{type, severity, desc}]``——
    哪个角色崩了人设、哪处设定矛盾、哪条逻辑断了，都在这里。此前这些明细
    评完即丢，hint 只带维度级数字汇总，Writer 只能盲猜重犯。
    证据不可信（confidence=0，如缓存串值）时不输出，避免误导重写。

    ``only_chapter``（2026-09-25）：定向修复一次重写多章时，按章过滤 issue——
    此前每章都拿到**全量**失败明细，重写 ch82 的 Writer 也在按 ch80 的问题改稿
    ⇒ 多章重写互相踩踏（实测修复一轮把 83.5 分修到 55.0）。过滤口径：issue
    锚定了本章、或未锚定任何章（全局性问题）才保留；锚定到别章的由别章的重写解决。
    """
    ev = getattr(dim, "evidence", None)
    if ev is None:
        return []
    if float(getattr(ev, "confidence", 1.0)) <= 0.0:
        return []
    lines: list[str] = []
    rationale = str(getattr(ev, "rationale", "") or "").strip()
    if rationale and only_chapter is None:
        lines.append(f"  · 评审理由：{rationale[:max_desc]}")
    for it in (getattr(ev, "issues", None) or [])[: max_issues * 3]:
        if not isinstance(it, dict):
            continue
        desc = str(it.get("desc", "") or "").strip()
        if not desc:
            continue
        if only_chapter is not None:
            anchors = _issue_anchors(desc) | _issue_anchors(str(it.get("quote", "") or ""))
            if anchors and only_chapter not in anchors:
                continue
        sev = str(it.get("severity", "") or "").strip()
        typ = str(it.get("type", "") or "").strip()
        tag = f"[{sev}] " if sev else ""
        prefix = f"{typ}：" if typ else ""
        # ★ 2026-09-21（灵荒工坊实验）：quote（原文定位）此前被丢弃——计数维
        #   （人设/设定/逻辑）的 issues **必须带 quote 才计入 value**（
        #   ``_count_gated_issues``），却只把 desc 编进 hint ⇒ Writer 知道
        #   "有 3 处冲突"但定位不到原文，只能盲改 ⇒ 重写引入新冲突（实测
        #   设定冲突 1→0→3 越修越多）。补上 quote 让 Writer 精确定位。
        quote = str(it.get("quote", "") or "").strip()
        tail = f"｜原文：「{quote[:80]}」" if quote else ""
        lines.append(f"  · {tag}{prefix}{desc[:max_desc]}{tail}")
    return lines


def build_rewrite_hint(
    report: Any, chapter_nums: list[int], only_chapter: int | None = None
) -> str:
    """把上一轮全书体检的失败项编译成写给 Writer 的针对性修正提示。

    回溯重写若不带反馈，Writer 只会盲目重生成、极易再次不达标而触发无谓上报。
    这里把未达标维度、回溯原因与重写章节区间浓缩为可读指令，让重写「对症」。
    修复（2026-09-08）：附带评审 LLM 的逐条问题明细（来自 EvalEvidence.issues），
    让 Writer 精确避开上一版的具体错误点，而不是只知道"这个维度不达标"。

    ``only_chapter``（2026-09-25）：定向修复一次重写多章时按章过滤 issue 明细
    （锚定别章的问题由别章的重写解决），避免每章都背全部问题的锅而互相踩踏。
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
            issue_lines = _dim_issue_lines(d, only_chapter=only_chapter)
            if only_chapter is not None and not issue_lines:
                # 2026-09-29（灵荒工坊 ch213-217 实测）：维度整体未达标却对本章
                # 说"问题在其他章"，两句连读自相矛盾（Writer 反馈"指令互相抵消"）。
                # 兜底句必须与维度级行呼应：承认本章未定位到具体问题点、
                # 失标可能由窗口内其他章引起，明确本章勿盲目改动。
                issue_lines = [
                    "  · 上行维度整体未达标，但未定位到**本章**的具体问题点——"
                    "失标可能由窗口内其他章引起，由其他章的重写解决；"
                    "本章只需与前后章保持一致，勿为本维度盲目改动情节。"
                ]
            lines.extend(issue_lines)
            # 2026-09-10（回滚率削减·P0）：只给"哪里错了"会催生保守灌水，补正向指引
            try:
                from agent.core.quality.eval_lessons import guidance_for

                lines.append(f"  · 正向做法：{guidance_for(d.name, d.label)}")
            except Exception:  # noqa: BLE001 - 指引缺失不影响重写
                pass  # noqa: SILENT_DEGRADE
    reason = getattr(report, "escalated_reason", "") or ""
    if reason:
        lines.append(f"上下文：{reason}")
    plan = getattr(report, "repair", None)
    if plan is not None:
        r = getattr(plan, "reason", "") or ""
        if r:
            lines.append(f"回溯原因：{r}")
    appeal = getattr(report, "appeal", None) or {}
    for s in (appeal.get("suggestions") or [])[:3]:
        if isinstance(s, str) and s.strip():
            lines.append(f"- 读者吸引力建议：{s.strip()[:200]}")
    lines.append(
        "【既定事实约束（重写红线）】本次未回退的章节与设定台账是**既定事实**："
        "重写章节必须与它们保持一致，不得为绕开冲突而改动其他章节已确立的"
        "人物言行/境界/金手指规则/事件结果。若冲突源于重写章与保留章矛盾，"
        "以保留章为准修改重写章；若冲突源于重写章内部自相矛盾，按世界观设定修复。"
    )
    lines.append(
        "请在重写时针对以上维度改善（如补全伏笔回收、修复人设/设定冲突、"
        "提升连贯与追读节奏、控制注水），并保持与世界观/角色档案一致。"
    )
    return "\n".join(lines)


# G10（拍板 3）：预算档位降档方向 quality→balanced→economy（模块级，供测试引用）
_DOWNGRADE_ORDER: list[str] = ["quality", "balanced", "economy"]
