"""EvaluatorAgent —— 评测员（Phase 2 多智能体团队之一）

职责（设计文档 §2.5）：成书后跑"不崩"套件（七维，§1.2），输出**量化体检报告**
+ **自动回溯修复**（默认回退 5 章，可配置）。

七维"不崩"指标（§1.2）：
  1. 人设稳定  character_stability_high  = 0（不可放宽）
  2. 设定一致  setting_consistency_high  = 0（不可放宽）
  3. 伏笔闭环  foreshadow_recycle_rate    ≥ 0.90（默认，可配置）
  4. 连贯性    coherence                  ≥ 85/100（G2 收紧 80→85，可配置）
  5. 追读力    readability                ≥ 80/100（G2 收紧 75→80，可配置）
  6. 节奏      pacing_abnormal            ≤ 0.03（可配置）
  7. 逻辑漏洞  logic_holes                = 0（不可放宽）

指标计算策略（诚实且可测）：
- ``foreshadow_recycle_rate`` / ``pacing_abnormal``：完全确定性，从
  ``foreshadows.md`` 与章节字数直接算出（无需 LLM）。
- 其余四维（人设/设定/连贯/追读/逻辑）：经 ``score_fn`` 注入（生产环境接 LLM 评测）；
  无 LLM 时给"通过型"安全默认（与项目"降级不阻断"一致，并在报告中标注来源）。

自动回溯修复：
- 任一**硬指标**或 overall 不达标 → 调用 ``M10RollbackWorkflow.rollback_to_chapter``
  回退最近 ``rollback_window``（默认 5）章并归档。
- 回退次数超过 ``max_rollback_attempts``（默认 3）→ ``escalated=True``，停止并上报人工。
- 若提供 ``rewriter`` 回调，则在本 Agent 内完成"回退→重写→重评"闭环（Pipeline 用它）；
  不提供则仅回退并返回 ``RepairPlan`` 供上层消费（standalone 命令用它）。
"""

from __future__ import annotations

import re
import statistics
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Optional

from rich.console import Console

from agent.core.state_machine import StateMachine
from agent.workflows.m10_rollback import M10RollbackWorkflow


# ============================================================
# 报告结构
# ============================================================
@dataclass
class DimensionResult:
    """单维结果。"""

    name: str
    label: str
    value: float
    threshold: float
    direction: str  # ">=" 或 "<="（value 与 threshold 的关系）
    required: bool  # 不可放宽（硬指标）
    source: str = ""  # computed | llm | default
    # G2 容差带：硬门禁恒 0；仅 coherence（0-100 量纲）用 5 吸收 LLM 噪声，其余保持严格。
    soft_margin: float = 0.0

    @property
    def passed(self) -> bool:
        # G2：引入容差带 soft_margin，边界合格章节不被误杀；劣质章节（远低于阈值）仍被抓。
        if self.direction == ">=":
            return self.value >= self.threshold - self.soft_margin - 1e-9
        return self.value <= self.threshold + self.soft_margin + 1e-9

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "label": self.label,
            "value": self.value,
            "threshold": self.threshold,
            "direction": self.direction,
            "required": self.required,
            "source": self.source,
            "soft_margin": self.soft_margin,
            "passed": self.passed,
        }


# G2 soft_margin 注入映射：构造 DimensionResult 时按维度名查表注入容差带。
# 主理人拍板：仅 coherence（0-100 量纲）用 5.0 吸收 LLM 噪声；
# readability 与确定性维度（[0,1] 量纲）、三硬门禁一律 0.0，避免门禁被静默关闭。
_SOFT_MARGIN = {
    "character_stability_high": 0.0,  # 硬门禁（不可放宽）
    "setting_consistency_high": 0.0,  # 硬门禁
    "logic_holes": 0.0,  # 硬门禁
    "coherence": 5.0,  # 0-100 评分维（吸收 LLM 噪声）
    "readability": 0.0,  # 0-100 评分维（主理人拍板：保持严格）
    "foreshadow_recycle_rate": 0.0,  # 确定性 0-1 维（保持严格）
    "pacing_abnormal": 0.0,  # 确定性 0-1 维
}


@dataclass
class RepairPlan:
    """回溯修复方案。"""

    target_chapter: int
    chapters_to_rewrite: list[int]
    reason: str
    rolled_back: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "target_chapter": self.target_chapter,
            "chapters_to_rewrite": list(self.chapters_to_rewrite),
            "reason": self.reason,
            "rolled_back": self.rolled_back,
        }


@dataclass
class NovelHealthReport:
    """全书"不崩"体检报告。"""

    overall_pass: bool
    score: float = 0.0
    dimensions: list[DimensionResult] = field(default_factory=list)
    rolled_back: bool = False
    rollback_attempts: int = 0
    escalated: bool = False
    escalated_reason: str = ""
    repair: Optional[RepairPlan] = None
    notes: list[str] = field(default_factory=list)

    def dimension(self, name: str) -> Optional[DimensionResult]:
        return next((d for d in self.dimensions if d.name == name), None)

    def to_dict(self) -> dict[str, Any]:
        return {
            "overall_pass": self.overall_pass,
            "score": round(self.score, 2),
            "dimensions": [d.to_dict() for d in self.dimensions],
            "rolled_back": self.rolled_back,
            "rollback_attempts": self.rollback_attempts,
            "escalated": self.escalated,
            "escalated_reason": self.escalated_reason,
            "repair": self.repair.to_dict() if self.repair else None,
            "notes": list(self.notes),
        }

    def to_markdown(self) -> str:
        lines = ["# 全书「不崩」体检报告"]
        lines.append("")
        verdict = "✅ 通过" if self.overall_pass else (
            "⚠️ 需人工介入" if self.escalated else "🔧 已触发自动回溯"
        )
        lines.append(f"- **总评**：{verdict}（综合分 {self.score:.1f}/100）")
        lines.append(f"- **回溯次数**：{self.rollback_attempts}　**已回退**：{self.rolled_back}")
        if self.escalated:
            lines.append(f"- **上报原因**：{self.escalated_reason}")
        lines.append("")
        lines.append("| 维度 | 指标 | 实测 | 合格线 | 达标 |")
        lines.append("|---|---|---|---|---|")
        for d in self.dimensions:
            mark = "✓" if d.passed else "✗"
            lines.append(
                f"| {d.label} | {d.name} | {d.value} | "
                f"{d.direction} {d.threshold} | {mark} |"
            )
        if self.repair:
            lines.append("")
            lines.append(
                f"**修复方案**：回退至第 {self.repair.target_chapter} 章，"
                f"重写 {self.repair.chapters_to_rewrite}，原因：{self.repair.reason}"
            )
        for n in self.notes:
            lines.append(f"- {n}")
        return "\n".join(lines)


# score_fn 签名：(维度名, project_dir) -> 该维度原始值（float）
ScoreFn = Callable[[str, str], float]
# rewriter 签名：(待重写章节号列表) -> None
RewriterFn = Callable[[list[int]], None]


class EvaluatorAgent:
    """评测员 Agent：全书"不崩"终审 + 自动回溯修复。

    Args:
        project_dir: 小说项目目录。
        console: rich 控制台。
        score_fn: 注入四维特值（人设/设定/连贯/追读/逻辑）；不传用安全默认。
        rollback_window: 每次回溯回退的章数（默认 5，可配置）。
        max_rollback_attempts: 最大回溯次数（默认 3）；超过则上报人工。
        auto_rollback: 是否在不达标时自动回溯（默认 True）。
        quality_targets: 覆盖默认七维合格线（来自 MasterPlan）。
    """

    def __init__(
        self,
        project_dir: str | Path,
        console: Console | None = None,
        score_fn: ScoreFn | None = None,
        rollback_window: int = 5,
        max_rollback_attempts: int = 3,
        auto_rollback: bool = True,
        quality_targets: dict[str, float] | None = None,
    ) -> None:
        self.project_dir = Path(project_dir)
        self.console = console or Console()
        self.score_fn = score_fn
        self.rollback_window = max(1, rollback_window)
        self.max_rollback_attempts = max(1, max_rollback_attempts)
        self.auto_rollback = auto_rollback
        # 最近一次「不达标」体检报告（供 Pipeline 的 rewriter 编译针对性重写提示）
        self.last_failed_report: "Optional[NovelHealthReport]" = None
        qt = dict(quality_targets or {})
        self.qt = {
            "character_stability_high": float(qt.get("character_stability_high", 0)),
            "setting_consistency_high": float(qt.get("setting_consistency_high", 0)),
            "foreshadow_recycle_rate": float(qt.get("foreshadow_recycle_rate", 0.90)),
            # G2 收紧 80→85 / 75→80（与 planner_agent.QualityTargets 默认、_PLANNER_SYSTEM 三处同步）
            "coherence": float(qt.get("coherence", 85.0)),
            "readability": float(qt.get("readability", 80.0)),
            "pacing_abnormal": float(qt.get("pacing_abnormal", 0.03)),
            "logic_holes": float(qt.get("logic_holes", 0)),
        }

    # ---------------------------------------------------------------- 指标
    def _score(self, name: str) -> float:
        if self.score_fn is not None:
            try:
                return float(self.score_fn(name, str(self.project_dir)))
            except Exception:  # noqa: BLE001
                pass
        # 安全默认（无 LLM）：硬指标 0 通过，评分维度给满分。
        if name in ("character_stability_high", "setting_consistency_high", "logic_holes"):
            return 0.0
        if name in ("coherence", "readability"):
            return 100.0
        return 0.0

    def _metric_foreshadow_recycle(self) -> tuple[float, dict[str, int]]:
        """确定性：伏笔回收率。"""
        f_file = self.project_dir / "foreshadows.md"
        resolved = 0
        unresolved = 0
        if f_file.exists():
            for line in f_file.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line.startswith("|") or line.startswith("|---") or line.startswith("| ID"):
                    continue
                cells = [c.strip() for c in line.split("|")[1:-1]]
                if len(cells) < 5:
                    continue
                state = cells[4]
                if state == "已回收":
                    resolved += 1
                elif state not in ("已废弃",):
                    unresolved += 1
        denom = resolved + unresolved
        rate = (resolved / denom) if denom > 0 else 1.0
        return rate, {"resolved": resolved, "unresolved": unresolved, "total": denom}

    def _metric_pacing(self) -> tuple[float, dict[str, Any]]:
        """确定性：异常章节比例（注水/赶进度）。"""
        chapters_dir = self.project_dir / "chapters"
        counts: list[int] = []
        if chapters_dir.exists():
            for f in sorted(chapters_dir.glob("ch*.md")):
                try:
                    text = f.read_text(encoding="utf-8")
                except OSError:
                    continue
                # 去 frontmatter
                if text.startswith("---"):
                    text = text.split("---", 2)[-1]
                counts.append(len(re.sub(r"\s", "", text)))
        if not counts:
            return 0.0, {"chapters": 0, "abnormal": 0}
        median = statistics.median(counts)
        if median <= 0:
            return 0.0, {"chapters": len(counts), "abnormal": 0}
        abnormal = sum(1 for c in counts if c < 0.5 * median or c > 2.0 * median)
        return abnormal / len(counts), {
            "chapters": len(counts),
            "abnormal": abnormal,
            "median": median,
        }

    # ---------------------------------------------------------------- 单轮评测
    def _evaluate_once(self) -> NovelHealthReport:
        recycle, fstat = self._metric_foreshadow_recycle()
        pacing, pstat = self._metric_pacing()
        char_high = self._score("character_stability_high")
        setting_high = self._score("setting_consistency_high")
        logic = self._score("logic_holes")
        coherence = self._score("coherence")
        readability = self._score("readability")

        dims = [
            DimensionResult(
                "character_stability_high", "人设稳定", char_high,
                self.qt["character_stability_high"], "<=", True, "llm/default",
                soft_margin=_SOFT_MARGIN.get("character_stability_high", 0.0),
            ),
            DimensionResult(
                "setting_consistency_high", "设定一致", setting_high,
                self.qt["setting_consistency_high"], "<=", True, "llm/default",
                soft_margin=_SOFT_MARGIN.get("setting_consistency_high", 0.0),
            ),
            DimensionResult(
                "foreshadow_recycle_rate", "伏笔闭环", recycle,
                self.qt["foreshadow_recycle_rate"], ">=", False, "computed",
                soft_margin=_SOFT_MARGIN.get("foreshadow_recycle_rate", 0.0),
            ),
            DimensionResult(
                "coherence", "连贯性", coherence,
                self.qt["coherence"], ">=", False, "llm/default",
                soft_margin=_SOFT_MARGIN.get("coherence", 0.0),
            ),
            DimensionResult(
                "readability", "追读力", readability,
                self.qt["readability"], ">=", False, "llm/default",
                soft_margin=_SOFT_MARGIN.get("readability", 0.0),
            ),
            DimensionResult(
                "pacing_abnormal", "节奏异常", pacing,
                self.qt["pacing_abnormal"], "<=", False, "computed",
                soft_margin=_SOFT_MARGIN.get("pacing_abnormal", 0.0),
            ),
            DimensionResult(
                "logic_holes", "逻辑漏洞", logic,
                self.qt["logic_holes"], "<=", True, "llm/default",
                soft_margin=_SOFT_MARGIN.get("logic_holes", 0.0),
            ),
        ]
        for d in dims:
            if d.name in ("foreshadow_recycle_rate", "pacing_abnormal"):
                d.source = "computed"

        failed = [d for d in dims if not d.passed]
        hard_failed = [d for d in failed if d.required]
        overall = len(failed) == 0

        # 综合分：各维归一化后平均
        norm = []
        for d in dims:
            if d.direction == ">=":
                norm.append(min(1.0, d.value / d.threshold) if d.threshold else 1.0)
            else:
                if d.threshold <= 0:
                    norm.append(1.0 if d.value <= 0 else 0.0)
                else:
                    norm.append(max(0.0, 1.0 - d.value / d.threshold))
        score = (sum(norm) / len(norm)) * 100 if norm else 100.0

        report = NovelHealthReport(overall_pass=overall, score=score, dimensions=dims)
        report.notes.append(
            f"伏笔：已回收 {fstat['resolved']} / 未结 {fstat['unresolved']}；"
            f"节奏：{pstat['chapters']} 章中异常 {pstat['abnormal']} 章"
        )
        if hard_failed:
            report.notes.append(
                "硬指标不达标（不可放宽）：" + "、".join(d.label for d in hard_failed)
            )
        return report

    # ---------------------------------------------------------------- 回溯
    def _last_written(self) -> int:
        sm = StateMachine(self.project_dir)
        try:
            sm.load()
            return int((sm.progress or {}).get("total_written", 0))
        except Exception:  # noqa: BLE001
            return 0

    def trigger_rollback(self, last_written: int | None = None) -> Optional[RepairPlan]:
        """回退最近 ``rollback_window`` 章并归档，返回修复方案；无法回退则返回 None。"""
        last = self._last_written() if last_written is None else last_written
        if last <= 0:
            return None
        target = max(1, last - self.rollback_window + 1)
        try:
            wf = M10RollbackWorkflow(self.project_dir)
            res = wf.rollback_to_chapter(target)
        except Exception as e:  # noqa: BLE001
            self.console.print(f"[red]回溯失败：{e}[/red]")
            return None
        plan = RepairPlan(
            target_chapter=target,
            chapters_to_rewrite=list(range(target, last + 1)),
            reason=f"「不崩」硬指标/总分不达标，自动回溯最近 {self.rollback_window} 章",
            rolled_back=res.success,
        )
        return plan

    # ---------------------------------------------------------------- 主入口
    def evaluate(self) -> NovelHealthReport:
        """单次全书体检（不自动重写；需要时回退并返回 RepairPlan）。"""
        report = self._evaluate_once()
        if report.overall_pass or not self.auto_rollback:
            return report
        plan = self.trigger_rollback()
        if plan is None:
            report.escalated = True
            report.escalated_reason = "无可回退章节（尚未写出章节），请人工检查设定/规划。"
            return report
        report.rolled_back = plan.rolled_back
        report.repair = plan
        report.rollback_attempts = 1
        if self.memory_log:
            try:
                self.memory_log("rollback", f"回退至第 {plan.target_chapter} 章", plan.to_dict())
            except Exception:  # noqa: BLE001
                pass
        return report

    def evaluate_with_repair(self, rewriter: RewriterFn) -> NovelHealthReport:
        """闭环：体检 →（不达标）回退 → 针对性重写 → 重评，直至通过或上报人工。

        每轮不达标都会把 ``last_failed_report`` 暴露给上层，使 Pipeline 的
        ``rewriter`` 能据此把失败维度编译成针对性提示传给 Writer，而不是盲目重写。
        """
        attempts = 0
        report = self._evaluate_once()
        while not report.overall_pass:
            # 暴露当前失败报告，供 rewriter 编译针对性修正提示
            self.last_failed_report = report
            if attempts >= self.max_rollback_attempts:
                report.escalated = True
                report.rollback_attempts = attempts
                report.escalated_reason = (
                    f"回溯 {attempts} 次仍不达标，已超过上限 "
                    f"{self.max_rollback_attempts}，需人工介入。"
                )
                return report
            plan = self.trigger_rollback()
            if plan is None or not plan.rolled_back:
                report.escalated = True
                report.escalated_reason = "无可回退章节，请人工检查设定/规划。"
                return report
            report.rolled_back = True
            report.rollback_attempts = attempts + 1
            try:
                rewriter(plan.chapters_to_rewrite)
            except Exception as e:  # noqa: BLE001
                report.escalated = True
                report.escalated_reason = f"重写失败：{e}"
                return report
            attempts += 1
            report = self._evaluate_once()
        # 闭环成功收尾：把累计回溯次数回写到最终通过报告，便于审计/复盘
        report.rollback_attempts = attempts
        report.rolled_back = attempts > 0
        return report

    # 可选：把回溯事件写进 Memory（由 Pipeline 注入）
    memory_log: Optional[Callable[[str, str, Any], Any]] = None
