"""EvaluatorAgent 拆分的 Mixin（机械搬移，行为零改动）。

拆分背景：单文件 1200+ 行不利维护（对齐 m5 / agentic_pipeline 拆分模式）。
主文件 ``evaluator.py`` 保留类定义、``__init__``、汇总与主入口；
本文件承载对应方法组。仅供 ``EvaluatorAgent`` 继承组合，不要单独使用。
"""

from __future__ import annotations



import json
from pathlib import Path
from typing import Any

from agent.agents.evaluator_types import DimensionResult, NovelHealthReport


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
)


# ============================================================
# 评估维度作用域声明（2026-09-08 架构化，替代 G8 硬编码特判）
# ============================================================
# 每个全书级评估维度必须在此登记作用域，未登记默认 "window"（每轮窗口均评）。
#   - window:      窗口级维度，每次 autowrite 结尾评估都适用；
#   - book_ending: 全书收尾验收维，仅当进度进入结局窗口才启用——中途窗口
#     被完结标准审判必然假失败（2026-09-07 五灵破归档事故：1200 章书写到
#     16 章，支线 1/5 需≥3、结局段自 ch901 未开始、回收率 0 全为假失败）。
# 新增全书级维度（如"多线收束""悬念总密度"）必须在此登记。
_DIM_SCOPE: dict[str, str] = {
    "mainline_progress": "book_ending",
    "ending_convergence": "book_ending",
}


def _scope_allows(name: str, in_book_ending_window: bool) -> bool:
    """维度作用域裁决：window 恒启用；book_ending 仅结局窗口内启用。"""
    return _DIM_SCOPE.get(name, "window") != "book_ending" or in_book_ending_window


class _EvaluatorDimensionsMixin:
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

        # ---- G6：B6 防注水（拍板 #5：重复度硬闸 + 信息密度软标红）----
        self._last_padding_stats = None
        if self.padding_gate:
            try:
                rep_ratio, rep_stat = self._metric_repetition()
                dens_ratio, dens_stat = self._metric_info_density()
                self._last_padding_stats = {
                    "repetition_ratio": rep_ratio,
                    "info_density_ratio": dens_ratio,
                    "repetition_stat": rep_stat,
                    "info_density_stat": dens_stat,
                }
                # 硬闸：重复句占比 ≤ 阈值 才通过（direction="<="，0-1 量纲）
                dims.append(DimensionResult(
                    "padding_repetition_abnormal", "注水·重复句占比",
                    rep_ratio, self.padding_threshold, "<=", True, "computed",
                    soft_margin=0.0,
                ))
                # 软标红：info_density_abnormal 不建 DimensionResult（不进 overall_pass），
                # 仅由 padding 子块报告标注（拍板 #5）
            except Exception as e:  # noqa: BLE001 - 确定性指标异常降级不阻断（G3）
                if self.console is not None:
                    self.console.print(f"[yellow]⚠ 防注水指标计算失败，跳过：{e}[/yellow]")  # noqa: SILENT_DEGRADE

        # ---- G5：迷爱看六维门禁（方案 A，并入 overall_pass）----
        self._last_appeal_report = None
        self._last_appeal_source = "llm"
        if self.appeal_gate and self.appeal_scorer is not None:
            try:
                _ar = gate_chapter(
                    self.appeal_scorer, self.project_dir, self.appeal_window
                )
                self._last_appeal_report = _ar
                if not _ar.llm_used:
                    # 离线降级短路（主理人拍板 #5 / 风险 1 / 修正点 A）：
                    # 六维每个 DimensionResult 的 value 必须设为 APPEAL_DIM_FLOOR(40)，
                    # 综合维 value 设 APPEAL_PASS_LINE(60)，保证全部 passed=True，禁止触发回溯。
                    self._last_appeal_source = "offline"
                    for _k in APPEAL_DIMENSIONS:
                        dims.append(DimensionResult(
                            f"{APPEAL_GATE_PREFIX}{_k}", f"迷·{APPEAL_LABELS.get(_k, _k)}",
                            float(APPEAL_DIM_FLOOR), float(APPEAL_DIM_FLOOR),
                            ">=", False, "offline", soft_margin=0.0,
                        ))
                    dims.append(DimensionResult(
                        "appeal_total", "迷·综合", float(APPEAL_PASS_LINE),
                        float(self.appeal_threshold), ">=", False, "offline", soft_margin=0.0,
                    ))
                else:
                    self._last_appeal_source = "llm"
                    for _k in APPEAL_DIMENSIONS:
                        dims.append(DimensionResult(
                            f"{APPEAL_GATE_PREFIX}{_k}", f"迷·{APPEAL_LABELS.get(_k, _k)}",
                            float(_ar.dimensions.get(_k, 0)), float(APPEAL_DIM_FLOOR),
                            ">=", False, "llm", soft_margin=0.0,
                        ))
                    dims.append(DimensionResult(
                        "appeal_total", "迷·综合", float(_ar.total_score),
                        float(self.appeal_threshold), ">=", False, "llm", soft_margin=0.0,
                    ))
            except Exception as e:  # noqa: BLE001 - 六维评分异常降级不阻断（G3）
                if self.console is not None:
                    self.console.print(f"[yellow]⚠ 迷爱看门禁评分失败，跳过：{e}[/yellow]")  # noqa: SILENT_DEGRADE

        # ---- G6：B4 黄金三章门禁（拍板 #1 方案 A，并入 overall_pass；离线短路仿 G5 修正点 A）----
        self._last_golden_report = None
        self._last_golden_source = "llm"
        if self.golden_three_gate and self.golden_scorer is not None:
            try:
                _gr = gate_first_chapters(
                    self.golden_scorer, self.project_dir, 3,
                    title="", genre="", synopsis="",
                )
                self._last_golden_report = _gr
                if not _gr.llm_used:
                    # 离线短路（拍板补充边界 2）：value 取 CLI 覆盖后的阈值，保证 passed 恒 True，
                    # 禁止误触发 escalated。注意与 G5 的差异：用 self.golden_three_* 而非硬编码常量。
                    self._last_golden_source = "offline"
                    for _k in APPEAL_DIMENSIONS:
                        dims.append(DimensionResult(
                            f"{GOLDEN_GATE_PREFIX}{_k}", f"金三·{APPEAL_LABELS.get(_k, _k)}",
                            float(self.golden_three_floor), float(self.golden_three_floor),
                            ">=", False, "offline", soft_margin=0.0,
                        ))
                    dims.append(DimensionResult(
                        "golden_total", "金三·综合", float(self.golden_three_threshold),
                        float(self.golden_three_threshold), ">=", False, "offline", soft_margin=0.0,
                    ))
                else:
                    self._last_golden_source = "llm"
                    for _k in APPEAL_DIMENSIONS:
                        dims.append(DimensionResult(
                            f"{GOLDEN_GATE_PREFIX}{_k}", f"金三·{APPEAL_LABELS.get(_k, _k)}",
                            float(_gr.dimensions.get(_k, 0)), float(self.golden_three_floor),
                            ">=", False, "llm", soft_margin=0.0,
                        ))
                    dims.append(DimensionResult(
                        "golden_total", "金三·综合", float(_gr.total_score),
                        float(self.golden_three_threshold), ">=", False, "llm", soft_margin=0.0,
                    ))
            except Exception as e:  # noqa: BLE001 - golden 评分异常降级不阻断（G3）
                if self.console is not None:
                    self.console.print(f"[yellow]⚠ 黄金三章门禁评分失败，跳过：{e}[/yellow]")  # noqa: SILENT_DEGRADE

        # ---- G8（拍板 3）：主线推进 + 结局收敛验收维度（确定性 computed，并入 overall_pass）----
        # 作用域由 _DIM_SCOPE 声明式裁决（book_ending 维仅结局窗口内启用）；
        # 中途续写窗口不具备评估前提（1200 章书写到 16 章必然假失败）。
        g8_final_phase = self._in_book_final_phase()
        if self.mainline_gate and _scope_allows("mainline_progress", g8_final_phase):
            try:
                dims.append(self._dim_mainline_progress())
            except Exception as e:  # noqa: BLE001 - 降级不阻断（G3 哲学）
                if self.console is not None:
                    self.console.print(f"[yellow]⚠ mainline_progress 计算失败，跳过：{e}[/yellow]")  # noqa: SILENT_DEGRADE
        if self.ending_gate and _scope_allows("ending_convergence", g8_final_phase):
            try:
                dims.append(self._dim_ending_convergence())
            except Exception as e:  # noqa: BLE001
                if self.console is not None:
                    self.console.print(f"[yellow]⚠ ending_convergence 计算失败，跳过：{e}[/yellow]")  # noqa: SILENT_DEGRADE

        failed = [d for d in dims if not d.passed]
        hard_failed = [d for d in failed if d.required]
        overall = len(failed) == 0

        # 综合分：各维归一化后平均
        norm = []
        for d in dims:
            # G5：迷爱看六维不计入「不崩」综合分；G6：golden_*/padding_* 同样跳过，
            #     防止新量纲漂移综合分（零回归，共享知识 #10）
            #     G8：mainline_*/ending_* 同样跳过（计数/比例量纲不可混算，仿先例）
            if d.name.startswith((APPEAL_GATE_PREFIX, GOLDEN_GATE_PREFIX, "padding_",
                                  "mainline_", "ending_")):
                continue
            if d.direction == ">=":
                norm.append(min(1.0, d.value / d.threshold) if d.threshold else 1.0)
            else:
                if d.threshold <= 0:
                    norm.append(1.0 if d.value <= 0 else 0.0)
                else:
                    norm.append(max(0.0, 1.0 - d.value / d.threshold))
        score = (sum(norm) / len(norm)) * 100 if norm else 100.0

        report = NovelHealthReport(overall_pass=overall, score=score, dimensions=dims)
        # ---- G5：填充迷爱看子块（修正点 B：六维详情从 self._last_appeal_report 取，
        #      绝不可误用 NovelHealthReport 的 report 本身不具备的字段）----
        if getattr(self, "_last_appeal_report", None) is not None:
            _ar = self._last_appeal_report
            report.appeal = {
                "source": self._last_appeal_source,          # "llm" | "offline"
                "total_score": _ar.total_score,               # 综合分（离线为 0）
                "threshold": self.appeal_threshold,           # 综合合格线
                "floor": APPEAL_DIM_FLOOR,                    # 单维触底线
                "verdict": _verdict(_ar.total_score),         # 读者感受档位
                "dimensions": {                               # 六维明细
                    k: {"score": _ar.dimensions.get(k, 0), "floor": APPEAL_DIM_FLOOR}
                    for k in APPEAL_DIMENSIONS
                },
                "passed": all(
                    d.passed for d in dims if d.name.startswith(APPEAL_GATE_PREFIX)
                ),
                "one_liner": _ar.one_liner,
                "suggestions": list(_ar.suggestions),
            }
        # ---- G6：填充黄金三章子块（修正点：从 self._last_golden_report 取，仿 G5 appeal 子块）----
        if getattr(self, "_last_golden_report", None) is not None:
            _gr = self._last_golden_report
            report.golden_three = {
                "source": self._last_golden_source,            # "llm" | "offline"
                "mode": "per_chapter_worst" if _gr.fallback else "join",  # 评分方式
                "chapters_scored": _gr.chapters_scored,
                "total_score": _gr.total_score,
                "threshold": self.golden_three_threshold,
                "floor": self.golden_three_floor,
                "verdict": _verdict(_gr.total_score),
                "dimensions": {
                    k: {"score": _gr.dimensions.get(k, 0), "floor": self.golden_three_floor}
                    for k in APPEAL_DIMENSIONS
                },
                "passed": all(
                    d.passed for d in dims if d.name.startswith(GOLDEN_GATE_PREFIX)
                ),
                "one_liner": _gr.one_liner,
                "suggestions": list(_gr.suggestions),
            }
        # ---- G6：填充防注水子块（重复度 + 信息密度软标红）----
        if getattr(self, "_last_padding_stats", None) is not None:
            ps = self._last_padding_stats
            rep_ratio = ps["repetition_ratio"]
            dens_ratio = ps["info_density_ratio"]
            report.padding = {
                "repetition": {
                    "ratio": round(rep_ratio, 4),
                    "threshold": self.padding_threshold,
                    "passed": rep_ratio <= self.padding_threshold,
                    "total_sentences": ps["repetition_stat"]["total_sentences"],
                    "repeated_sentences": ps["repetition_stat"]["repeated_sentences"],
                },
                "info_density": {   # 软标红（P1，仅报告）
                    "advancing_ratio": round(dens_ratio, 4),
                    "floor": self._INFO_DENSITY_FLOOR,
                    "flagged": dens_ratio < self._INFO_DENSITY_FLOOR,
                },
            }
        report.notes.append(
            f"伏笔：到期回收 {fstat.get('due_resolved', fstat['resolved'])}/"
            f"{fstat.get('due', fstat['resolved'] + fstat['unresolved'])}"
            f"（全书已回收 {fstat['resolved']} / 未结 {fstat['unresolved']}）；"
            f"节奏：{pstat['chapters']} 章中异常 {pstat['abnormal']} 章"
        )
        if hard_failed:
            report.notes.append(
                "硬指标不达标（不可放宽）：" + "、".join(d.label for d in hard_failed)
            )
        # ---- G7：人话总结层（拍板 2：确定性拼装；--no-human-summary 时不填充 → 展示层跳过）----
        if self.human_summary:
            report.summary = self._build_summary(report)
        return report

    # ---------------------------------------------------------------- 回溯

    def _golden_escalation_detail(self, report: NovelHealthReport) -> str:
        """前三章失败明细（人工可据此重写 1-3 章）。"""
        gr = getattr(self, "_last_golden_report", None)
        if gr is None:
            return "（golden 评分不可用）"
        lines = [f"- 综合分：{gr.total_score}/{self.golden_three_threshold}（{_verdict(gr.total_score)}）"]
        for k in APPEAL_DIMENSIONS:
            v = gr.dimensions.get(k, 0)
            mark = "✓" if v >= self.golden_three_floor else "✗"
            lines.append(f"- {APPEAL_LABELS.get(k, k)}：{v}/100（触底 {self.golden_three_floor}）{mark}")
        lines.append(f"- 评分方式：{'三章拼接一次' if not gr.fallback else '每章独立取最差'} · source={self._last_golden_source}")
        return "\n".join(lines)

    # ---------------------------------------------------------------- G8：主线推进 + 结局收敛维度
    def _in_book_final_phase(self) -> bool:
        """当前进度是否已进入全书结局窗口（G8 两维的生效前提）。

        结局窗口判定：total_written >= ending_trigger(book_total, 0.25)
        （与 pipeline ending mixin 的触发口径一致，例：1200 章书 → 第 901 章起）。
        book_total 未知（无 plan.json / 读失败）→ 保守返回 True，保持旧语义
        （无法证明是中途窗口时不禁用门禁，避免放走真正的收尾缺陷）。
        """
        try:
            from agent.core.progress import book_total as _book_total
            from agent.core.progress import ending_trigger as _ending_trigger

            total = _book_total(self.project_dir)
            if not total:
                return True
            sm = StateMachine(self.project_dir)
            sm.load()
            chapter = int((sm.progress or {}).get("total_written", 0) or 0)
            return chapter >= _ending_trigger(int(total), 0.25)
        except Exception:  # noqa: BLE001 - 判定失败保持旧语义
            return True  # noqa: SILENT_DEGRADE

    def _dim_mainline_progress(self) -> DimensionResult:
        """已访问支线数 ≥ min(3, 支线总数) 才达标（direction=">="）。

        1 条支线恒达标（threshold=min(3,1)=1），不误杀短书（PRD §7 风险 6）。
        0 条支线 threshold=0，恒达标（无支线可走，不误杀）。
        """
        visited, total = self._mainline_stats()
        threshold = float(min(3, total)) if total > 0 else 0.0
        return DimensionResult(
            "mainline_progress", "主线推进", float(len(visited)),
            threshold, ">=", False, "computed",
            soft_margin=_SOFT_MARGIN.get("mainline_progress", 0.0),
            scope=_DIM_SCOPE["mainline_progress"],
        )

    def _mainline_stats(self) -> tuple[set[str], int]:
        """双保险统计（补充边界 3）：progress.mainline_visited ∪ 章 frontmatter subline 反推。"""
        import frontmatter as _fm
        from agent.core.story.setting_manager import SettingManager

        sm = StateMachine(self.project_dir)
        try:
            sm.load()
            progress = sm.progress or {}
        except Exception:  # noqa: BLE001
            progress = {}  # noqa: SILENT_DEGRADE
        visited: set[str] = set(progress.get("mainline_visited", []) or [])
        # 双保险：章 frontmatter subline 反推
        try:
            ch_dir = self.project_dir / "chapters"
            if ch_dir.exists():
                for f in sorted(ch_dir.glob("ch*.md")):
                    post = _fm.load(f)
                    sub = post.metadata.get("subline", "")
                    if sub:
                        visited.add(str(sub))
        except Exception:  # noqa: BLE001 - 反推失败不影响主记录
            pass  # noqa: SILENT_DEGRADE
        try:
            total = len(SettingManager(self.project_dir).list_sublines())
        except Exception:  # noqa: BLE001
            total = 0  # noqa: SILENT_DEGRADE
        if total > 0:
            visited &= set(SettingManager(self.project_dir).list_sublines())  # 只统计本书支线
        return visited, total

    def _dim_ending_convergence(self) -> DimensionResult:
        """三条件：ending_mode==true 且 末章存在 且 结局段回收率 ≥0.90。

        三条件任一不满足 → value 归零（必不达标，进 overall_pass 失败 + escalated）；
        满足则 value=真实结局段回收率（报告可见真实值，明细见 _g8_structural_escalation_detail）。
        无伏笔（open_at_start==0）→ rate=1.0 恒达标（不误杀）。
        ending 为空时口径不变（本口径不含关键词匹配，退化路径不引入额外约束，拍板 3）。
        """
        rate, st = self._ending_recycle_rate()
        ok_mode = self._ending_mode_active()
        ok_last = self._last_chapter_exists()
        value = rate if (ok_mode and ok_last) else 0.0
        return DimensionResult(
            "ending_convergence", "结局收敛", value,
            0.90, ">=", False, "computed",
            soft_margin=_SOFT_MARGIN.get("ending_convergence", 0.0),
            scope=_DIM_SCOPE["ending_convergence"],
        )

    def _ending_mode_active(self) -> bool:
        sm = StateMachine(self.project_dir)
        try:
            sm.load()
            return bool((sm.progress or {}).get("ending_mode", False))
        except Exception:  # noqa: BLE001
            return False

    def _last_chapter_exists(self) -> bool:
        target = self._resolve_target()  # 与 pipeline 同源（plan.json→100）
        return (self.project_dir / "chapters" / f"ch{target:03d}.md").exists()

    def _ending_recycle_rate(self) -> tuple[float, dict[str, Any]]:
        """结局段回收率 = 结局段内回收数 / 结局段开始时未回收数。

        因 foreshadows.md 无 resolved_at 列，用 planted_at（埋设章号）+ 最终 state 近似：
          - 结局段开始 = floor(target*(1-ending_ratio)) + 1
          - 「结局段开始时未回收」≈ 结局段开始前已埋（planted_ch < ending_start）且
            最终 state ∈ {未埋, 已埋, 已回收} 的条数（含已回收——回收发生在结局段内）
          - 「结局段内回收」≈ 结局段开始前已埋 且 最终 state == 已回收 的条数
        无伏笔（denom==0）→ rate=1.0（不误杀）。与 _metric_foreshadow_recycle（行 517-537）同源解析。
        """
        target = self._resolve_target()
        ending_start = int(target * (1 - self.ending_ratio)) + 1
        f_file = self.project_dir / "foreshadows.md"
        open_at_start = 0
        resolved_in_ending = 0
        if f_file.exists():
            for line in f_file.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line.startswith("|") or line.startswith("|---") or line.startswith("| ID"):
                    continue
                cells = [c.strip() for c in line.split("|")[1:-1]]
                if len(cells) < 5:
                    continue
                planted_raw = cells[2]
                m = re.search(r"ch(\d+)", planted_raw)
                if not m or int(m.group(1)) >= ending_start:
                    continue  # 结局段开始后才埋（短线）→ 不计入分母
                state = cells[4]
                if state == "已回收":
                    resolved_in_ending += 1
                    open_at_start += 1
                elif state not in ("已废弃",):
                    open_at_start += 1
        rate = (resolved_in_ending / open_at_start) if open_at_start > 0 else 1.0
        return rate, {
            "ending_start": ending_start, "open_at_start": open_at_start,
            "resolved_in_ending": resolved_in_ending, "rate": round(rate, 4),
        }

    def _resolve_target(self) -> int:
        """与 pipeline._resolve_target（行 352-362）同源：plan.json total_chapters → 100。"""
        try:
            plan_file = self.project_dir / ".state" / "plan.json"
            if plan_file.exists():
                data = json.loads(plan_file.read_text(encoding="utf-8"))
                if data.get("total_chapters"):
                    return int(data["total_chapters"])
        except Exception:  # noqa: BLE001
            pass  # noqa: SILENT_DEGRADE
        return 100

    def _g8_structural_escalation_detail(self, report: NovelHealthReport) -> str:
        """mainline/ending 失败明细（人工可据此调整推进节奏/收束策略）。"""
        lines: list[str] = []
        mp = report.dimension("mainline_progress")
        if mp is not None and not mp.passed:
            visited, total = self._mainline_stats()
            need = min(3, total) if total > 0 else 0
            lines.append(
                f"· 主线推进：已访问 {len(visited)}/{total} 条支线"
                f"（{sorted(visited)}），需 ≥ {need} 条才达标"
            )
        ec = report.dimension("ending_convergence")
        if ec is not None and not ec.passed:
            rate, st = self._ending_recycle_rate()
            lines.append(
                f"· 结局收敛：ending_mode={self._ending_mode_active()}，"
                f"末章存在={self._last_chapter_exists()}，"
                f"结局段回收率 {st['rate']:.2f}"
                f"（回收 {st['resolved_in_ending']}/{st['open_at_start']}，"
                f"结局段自第 {st['ending_start']} 章起）"
            )
        return "\n".join(lines) if lines else "（无明细）"

    # 可选：把回溯事件写进 Memory（由 Pipeline 注入）
    memory_log: Optional[Callable[[str, str, Any], Any]] = None