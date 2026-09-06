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
- 任一**硬指标**或 overall 不达标 → 经注入的 ``rollback_provider`` 调用
  ``M10RollbackWorkflow.rollback_to_chapter``（D-J：DI 注入，未注入时懒加载兜底）
  回退最近 ``rollback_window``（默认 5）章并归档。
- 回退次数超过 ``max_rollback_attempts``（默认 3）→ ``escalated=True``，停止并上报人工。
- 若提供 ``rewriter`` 回调，则在本 Agent 内完成"回退→重写→重评"闭环（Pipeline 用它）；
  不提供则仅回退并返回 ``RepairPlan`` 供上层消费（standalone 命令用它）。
"""

from __future__ import annotations

import json
import re
import statistics
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Optional, Protocol

from rich.console import Console

from agent.core.story.chapters import iter_chapter_texts  # G6：公共章节读取 helper（根因 B6-3）
from agent.core.engine.state_machine import StateMachine
# D-J（2026-08-29）：不再在 agents 层直接 import workflows（违反依赖方向）。
# 回退能力经 ``rollback_provider`` 构造注入（见 RollbackProvider），未注入时懒加载兜底。
from agent.core.quality.scoring.reader_appeal import (  # G5：迷爱看六维双闸
    ReaderAppealScorer,
    APPEAL_DIMENSIONS,
    APPEAL_PASS_LINE,
    APPEAL_DIM_FLOOR,
    APPEAL_GATE_PREFIX,
    APPEAL_LABELS,
    gate_chapter,
    gate_first_chapters,   # G6：B4 黄金三章门禁
    GOLDEN_GATE_PREFIX,    # G6：golden_* 维度名前缀
    _verdict,
)


# ============================================================
# 报告结构
# ============================================================
from agent.agents.evaluator_types import (  # noqa: F401
    _SOFT_MARGIN,
    DimensionResult,
    NovelHealthReport,
    RepairPlan,
    RewriterFn,
    RollbackProvider,
    ScoreFn,
)
from agent.agents.evaluator_dims import _EvaluatorDimensionsMixin
from agent.agents.evaluator_metrics import _EvaluatorMetricsMixin
class EvaluatorAgent(
    _EvaluatorMetricsMixin,
    _EvaluatorDimensionsMixin,
):
    """评测员 Agent：全书"不崩"终审 + 自动回溯修复。

    Args:
        project_dir: 小说项目目录。
        console: rich 控制台。
        score_fn: 注入四维特值（人设/设定/连贯/追读/逻辑）；不传用安全默认。
        rollback_window: 每次回溯回退的章数（默认 5，可配置）。
        max_rollback_attempts: 最大回溯次数（默认 3）；超过则上报人工。
        auto_rollback: 是否在不达标时自动回溯（默认 True）。
        quality_targets: 覆盖默认七维合格线（来自 MasterPlan）。
        rollback_provider: 回退能力实现（D-J 反转：由 workflow 层注入
            ``m10_rollback.M10RollbackWorkflow``）；None 时懒加载兜底。
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
        # ---- D-J（2026-08-29）：回退能力 DI（默认 None → 懒加载 M10RollbackWorkflow 兜底）----
        rollback_provider: "RollbackProvider | None" = None,
        # ---- G5 新增：迷爱看六维双闸注入 ----
        appeal_scorer: "ReaderAppealScorer" | None = None,
        appeal_gate: bool = True,
        appeal_threshold: int = 60,
        appeal_window: int = 1,
        # ---- G6 新增：B4 黄金三章 + B6 防注水（与 appeal_* 并列独立）----
        golden_scorer: "ReaderAppealScorer | None" = None,  # 复用六维评分器（同一实例，评前三章）
        golden_three_gate: bool = True,                     # B4 开关（默认开）
        golden_three_threshold: int = 60,                   # 综合合格线（--golden-three-threshold）
        golden_three_floor: int = 40,                       # 单维触底线（--golden-three-floor）
        padding_gate: bool = True,                          # B6 开关（默认开）
        padding_threshold: float = 0.30,                    # 重复句占比阈值（--padding-threshold）
        # ---- G7 新增：人话总结层展示开关（默认开；--no-human-summary 关闭）----
        human_summary: bool = True,
        # ---- G8 新增：主线推进 + 结局收敛验收维度（拍板 3/6，默认全开可关）----
        mainline_gate: bool = True,
        ending_gate: bool = True,
        mainline_window: int = 5,
        ending_ratio: float = 0.25,
    ) -> None:
        self.project_dir = Path(project_dir)
        self.console = console or Console()
        self.score_fn = score_fn
        self.rollback_window = max(1, rollback_window)
        self.max_rollback_attempts = max(1, max_rollback_attempts)
        self.auto_rollback = auto_rollback
        # D-J：回退能力 DI（None → trigger_rollback 懒加载 M10RollbackWorkflow 兜底）
        self._rollback_provider: "RollbackProvider | None" = rollback_provider
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
        # G5：迷爱看六维双闸
        self.appeal_scorer = appeal_scorer
        self.appeal_gate = appeal_gate
        self.appeal_threshold = max(1, appeal_threshold)
        self.appeal_window = max(1, appeal_window)
        # G5：最近一次六维评分缓存（供 NovelHealthReport.appeal 子块填充，修正点 B）
        self._last_appeal_report = None
        self._last_appeal_source = "llm"
        # G6：B4 黄金三章 + B6 防注水
        self.golden_scorer = golden_scorer
        self.golden_three_gate = golden_three_gate
        self.golden_three_threshold = max(1, golden_three_threshold)
        self.golden_three_floor = max(1, golden_three_floor)
        self.padding_gate = padding_gate
        self.padding_threshold = max(0.0, min(1.0, padding_threshold))
        # G6：最近一次黄金三章评分缓存（供 golden_three 子块 + escalated 明细）
        self._last_golden_report = None
        self._last_golden_source = "llm"
        # G6：最近一次防注水统计缓存（供 padding 子块填充）
        self._last_padding_stats = None
        # G7：人话总结层展示开关（_evaluate_once 末尾决定是否填充 report.summary）
        self.human_summary = human_summary
        # G8：主线推进 + 结局收敛（钳制语义：window≥1，ratio∈[0,0.5]）
        self.mainline_gate = bool(mainline_gate)
        self.ending_gate = bool(ending_gate)
        self.mainline_window = max(1, int(mainline_window))
        self.ending_ratio = max(0.0, min(0.5, float(ending_ratio)))

    # ---------------------------------------------------------------- G7 人话总结层
    # 主理人拍板 1/2 + 补充边界 4：确定性模板拼装、零 LLM、措辞保守。
    # 素材全部取自既有结构化数据（失败维 DimensionResult + appeal/golden 子块 suggestions）。
    _SUMMARY_REASONS = {
        "character_stability_high": "人设出现前后矛盾，建议核对角色档案并统一言行/动机",
        "setting_consistency_high": "设定被打破，建议回查世界观设定并修复冲突",
        "foreshadow_recycle_rate": "伏笔回收率不足，建议安排已埋伏笔的回收或标注废弃",
        "coherence": "连贯性偏低，建议检查章节衔接与叙事流畅度",
        "readability": "追读力不足，建议在章末加强悬念/钩子",
        "pacing_abnormal": "异常章节比例偏高（注水/赶进度），建议平衡章节篇幅",
        "logic_holes": "存在逻辑漏洞，建议修复因果硬伤",
        "appeal_hook_strength": "章末钩子强度不足，建议加强章末悬念/反转",
        "appeal_payoff_density": "爽点密度偏低，建议增加打脸/反转/成长兑现",
        "appeal_immersion": "代入感不足，建议稳定视角、增加细节可信度",
        "appeal_character_arc": "人物弧光弱，建议给角色成长/转变",
        "appeal_world_novelty": "世界观新颖度不足，建议强化设定记忆点",
        "appeal_emotion_curve": "情绪曲线平，建议制造节奏起伏",
        "golden_hook_strength": "前三章章末钩子强度不足，建议加强开局悬念/反转",
        "golden_payoff_density": "前三章爽点密度偏低，建议开局即有兑现",
        "golden_immersion": "前三章代入感不足，建议稳定视角、增加细节可信度",
        "golden_character_arc": "前三章人物弧光弱，建议给角色成长/转变",
        "golden_world_novelty": "前三章世界观新颖度不足，建议强化设定记忆点",
        "golden_emotion_curve": "前三章情绪曲线平，建议制造节奏起伏",
        "padding_repetition_abnormal": "重复句占比偏高，建议删减车轱辘话/合并相似句",
        # ---- G8（补充边界 6）：主线推进 + 结局收敛归因（只增不删）----
        "mainline_progress": "支线推进不足：已访问支线数未达下限，建议推进/切换更多支线后再收尾",
        "ending_convergence": "结局收敛不达标：未进入结局模式或结局段伏笔回收不足，建议末段集中回收伏笔并向架构结局收束",
    }

    def _build_summary(self, report: NovelHealthReport) -> dict:
        """把既有结构化数据翻译成人话总结：一句话总评 + 仅失败维归因 + 差多少 + 下一步。

        素材全部取自既有数据：report.dimensions 失败维（label/value/threshold/direction）、
        report.appeal/golden_three 子块（one_liner + suggestions）、report.padding 子块。
        零 LLM、零网络（拍板 1）；量化差距严格按 direction 与表格同源（补充边界 4）。
        """
        failed = [d for d in report.dimensions if not d.passed]
        # 离线/全通过分支（拍板 2：给「全部达标（离线通过未实测）」一句话，不制造恐慌）
        if not failed:
            offline = any(
                getattr(d, "source", "") == "offline" for d in report.dimensions
            )
            return {
                "headline": "全部达标（离线通过未实测）" if offline else "全部达标",
                "failures": [],
                "next_steps": [],
                "offline": offline,
                "all_passed": True,
            }

        failures: list[dict] = []
        for d in failed:
            gap = self._summary_gap(d)                        # §2.3：按 direction 严格计算
            reason = self._summary_reason(d)                  # 维度级归因模板
            suggestion, src = self._summary_suggestion(d, report)  # LLM 优先 + 模板兜底
            arrow = "＞" if d.direction == "<=" else "＜"
            failures.append({
                "dimension": d.name,
                "label": d.label,
                "value": d.value,
                "threshold": d.threshold,
                "direction": d.direction,
                "gap": gap,
                "line": f"{d.label}：实测 {d.value} {arrow} 合格线 {d.threshold}（差 {gap}）",
                "reason": reason,
                "suggestion": suggestion,
                "suggestion_source": src,
                "source": d.source,
            })

        # 下一步建议汇总：LLM suggestions 优先（appeal/golden 子块），无则模板；去重、限 5 条
        next_steps: list[str] = []
        for f in failures:
            s = f["suggestion"]
            if s and s not in next_steps:
                next_steps.append(s)
        next_steps = next_steps[:5]

        return {
            "headline": f"全书不达标：{len(failures)} 个维度未过线（综合分 {report.score:.1f}/100）",
            "failures": failures,
            "next_steps": next_steps,
            "offline": False,
            "all_passed": False,
        }

    @staticmethod
    def _summary_gap(d: DimensionResult) -> float:
        """量化差距（与表格同源）：direction 语义正确。
        ">=" 越高越好：未达标 = value < threshold，差 = threshold - value（>0）；
        "<=" 越低越好：未达标 = value > threshold，差 = value - threshold（>0）。
        已达标维不进 failures，故 gap 恒 > 0。"""
        if d.direction == ">=":
            return round(max(0.0, d.threshold - d.value), 4)
        return round(max(0.0, d.value - d.threshold), 4)

    def _summary_reason(self, d: DimensionResult) -> str:
        """维度级归因模板（措辞保守：「未达标」非「崩了」）。"""
        return self._SUMMARY_REASONS.get(d.name, f"{d.label}未达标")

    def _summary_suggestion(self, d: DimensionResult, report: NovelHealthReport) -> tuple[str, str]:
        """建议来源：LLM suggestions 优先（appeal/golden 子块，注明 source=llm）；无则模板兜底。"""
        llm_pool: list[str] = []
        for sub in (report.appeal, report.golden_three):
            if sub and sub.get("suggestions"):
                llm_pool.extend(str(s) for s in sub["suggestions"])
        if llm_pool:
            return llm_pool[0], "llm"
        # 模板兜底：维度级（§2.3 表）；padding 子块无 suggestions → 走模板
        tpl = self._SUMMARY_REASONS.get(d.name)
        return tpl if tpl else f"建议针对「{d.label}」优化", "template"

    # ---------------------------------------------------------------- 指标
    def _last_written(self) -> int:
        sm = StateMachine(self.project_dir)
        try:
            sm.load()
            return int((sm.progress or {}).get("total_written", 0))
        except Exception:  # noqa: BLE001
            return 0

    def _resolve_rollback(self) -> "RollbackProvider":
        """返回回退能力：仅使用构造注入的 provider。

        D-J 反转（2026-08-29）后所有生产调用方（CLI/service/agentic_pipeline）
        均显式注入；未注入即构造缺失，如实报错，不再懒加载 workflows 兜底
        （消除 agents→workflows 越层依赖，见 R6 红线）。
        """
        if self._rollback_provider is None:
            raise RuntimeError(
                "EvaluatorAgent 未注入 rollback_provider：请由 workflow/CLI/service 层"
                "构造 m10_rollback.M10RollbackWorkflow 后经 rollback_provider= 注入"
            )
        return self._rollback_provider

    def trigger_rollback(self, last_written: int | None = None) -> Optional[RepairPlan]:
        """回退最近 ``rollback_window`` 章并归档，返回修复方案；无法回退则返回 None。"""
        last = self._last_written() if last_written is None else last_written
        if last <= 0:
            return None
        target = max(1, last - self.rollback_window + 1)
        try:
            res = self._resolve_rollback().rollback_to_chapter(target)
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
                pass  # noqa: SILENT_DEGRADE
        return report

    def evaluate_with_repair(self, rewriter: RewriterFn) -> NovelHealthReport:
        """闭环：体检 →（不达标）回退 → 针对性重写 → 重评，直至通过或上报人工。

        每轮不达标都会把 ``last_failed_report`` 暴露给上层，使 Pipeline 的
        ``rewriter`` 能据此把失败维度编译成针对性提示传给 Writer，而不是盲目重写。

        G6 拍板 2 例外：**golden_* 失败维存在时禁止触发 G1 末窗回退**（根因 B4-3：
        回溯窗口 ``[-rollback_window:]`` 只覆盖末 N 章，修不到开头），直接 escalated 附前三章明细。
        七维/六维失败维照旧走原回溯闭环（G1 语义不破坏）。
        """
        attempts = 0
        report = self._evaluate_once()
        while not report.overall_pass:
            # 暴露当前失败报告，供 rewriter 编译针对性修正提示
            self.last_failed_report = report
            # ---- G6 拍板 2：黄金三章失败 → 直接 escalated，跳过 trigger_rollback（不消耗回溯预算）----
            golden_failed = [
                d for d in report.dimensions
                if d.name.startswith(GOLDEN_GATE_PREFIX) and not d.passed
            ]
            if golden_failed:
                report.escalated = True
                report.rollback_attempts = attempts
                detail = self._golden_escalation_detail(report)
                report.escalated_reason = (
                    "黄金三章门禁失败（" + "、".join(d.label for d in golden_failed) + "）。"
                    "回溯窗口只覆盖末 N 章、无法修复开头，已禁止无效回退；"
                    "请人工重写第 1-3 章。明细：\n" + detail
                )
                return report
            # ---- G8（拍板 3）：mainline_*/ending_* 失败直通 escalated（仿 G6 golden，禁止末窗回退）----
            g8_failed = [
                d for d in report.dimensions
                if d.name.startswith(("mainline_", "ending_")) and not d.passed
            ]
            if g8_failed:
                report.escalated = True
                report.rollback_attempts = attempts
                detail = self._g8_structural_escalation_detail(report)
                report.escalated_reason = (
                    "全局结构门禁失败（" + "、".join(d.label for d in g8_failed) + "）："
                    "支线推进不足或结局收敛不达标是全局结构问题，末窗回退窗口修不到，"
                    "已禁止无效回退并上报人工。明细：\n" + detail
                )
                return report
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

