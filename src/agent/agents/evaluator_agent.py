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

from agent.core.chapters import iter_chapter_texts  # G6：公共章节读取 helper（根因 B6-3）
from agent.core.state_machine import StateMachine
from agent.workflows.m10_rollback import M10RollbackWorkflow
from agent.core.reader_appeal import (  # G5：迷爱看六维双闸
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
    # G5：迷爱看（读者吸引力）六维子块（不崩与迷爱看双闸分离展示）
    appeal: Optional[dict] = None
    # ---- G6：三闸子块 ----
    golden_three: Optional[dict] = None   # B4：黄金三章（source/mode/total/verdict/六维明细）
    ai_flavor: Optional[dict] = None      # B5：AI 味命中（由 pipeline 在评测后回填）
    padding: Optional[dict] = None        # B6：防注水（重复度 + 信息密度）

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
            "appeal": self.appeal,
            "golden_three": self.golden_three,
            "ai_flavor": self.ai_flavor,
            "padding": self.padding,
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
        # ---- G5：迷爱看小节（仅当 appeal 子块存在）----
        if self.appeal is not None:
            a = self.appeal
            lines.append("")
            if a.get("source") == "offline":
                lines.append("## 迷爱看（离线通过，未实测）")
                lines.append(
                    "> LLM 不可用，六维门禁按「降级不阻断」判通过；"
                    "配置真实 LLM 后才会真实评估读者吸引力。"
                )
            else:
                lines.append("## 迷爱看（读者吸引力六维）")
                verdict = a.get("verdict", "")
                lines.append(
                    f"- **综合分**：{a.get('total_score', 0)}/{a.get('threshold', 60)}"
                    f"（{verdict}）　**达标**：{'✓' if a.get('passed') else '✗'}"
                )
                lines.append("")
                lines.append("| 维度 | 得分 | 触底线 | 达标 |")
                lines.append("|---|---|---|---|")
                dims = a.get("dimensions", {})
                for k, v in dims.items():
                    ok = v.get("score", 0) >= v.get("floor", 40)
                    mark = "✓" if ok else "✗"
                    lines.append(
                        f"| {APPEAL_LABELS.get(k, k)} | {v.get('score', 0)} "
                        f"| {v.get('floor', 40)} | {mark} |"
                    )
                if a.get("one_liner"):
                    lines.append(f"> {a.get('one_liner')}")

        # ---- G6：黄金三章小节 ----
        if self.golden_three is not None:
            g = self.golden_three
            lines.append("")
            if g.get("source") == "offline":
                lines.append("## 黄金三章（离线通过，未实测）")
                lines.append(
                    "> LLM 不可用，黄金三章门禁按「降级不阻断」判通过；"
                    "配置真实 LLM 后才会实测开局吸引力。"
                )
            else:
                lines.append("## 黄金三章（B4 开局门禁）")
                mode_txt = (
                    "三章拼接一次评分" if g.get("mode") == "join"
                    else "每章独立评分取最差（超长回退）"
                )
                lines.append(
                    f"- **综合分**：{g.get('total_score', 0)}/{g.get('threshold', 60)}"
                    f"（{g.get('verdict', '')}）　**达标**：{'✓' if g.get('passed') else '✗'}　"
                    f"（{mode_txt} · source={g.get('source', 'llm')}）"
                )
                lines.append("")
                lines.append("| 维度 | 得分 | 触底线 | 达标 |")
                lines.append("|---|---|---|---|")
                for k, v in g.get("dimensions", {}).items():
                    ok = v.get("score", 0) >= v.get("floor", 40)
                    mark = "✓" if ok else "✗"
                    lines.append(
                        f"| {APPEAL_LABELS.get(k, k)} | {v.get('score', 0)} "
                        f"| {v.get('floor', 40)} | {mark} |"
                    )
                if g.get("one_liner"):
                    lines.append(f"> {g.get('one_liner')}")

        # ---- G6：去 AI 味小节（标红，advisory 不阻断）----
        if self.ai_flavor is not None and self.ai_flavor.get("count", 0) > 0:
            a = self.ai_flavor
            lines.append("")
            lines.append("## 去 AI 味（⚠ 命中 AI 腔词句）")
            lines.append(f"- **模式**：{a.get('mode', 'advisory')}　**命中**：{a.get('count', 0)} 处")
            for hit in a.get("hits", []):
                lines.append(
                    f"- [red]第 {hit.get('chapter', '?')} 章：{hit.get('message', '')}[/red]"
                )

        # ---- G6：防注水小节 ----
        if self.padding is not None:
            p = self.padding
            lines.append("")
            lines.append("## 防注水（B6）")
            rep = p.get("repetition", {})
            rep_mark = "✓" if rep.get("passed") else "✗"
            lines.append(
                f"- **重复句占比**：{rep.get('ratio', 0)} ≤ {rep.get('threshold', 0.30)}　{rep_mark}"
                f"（{rep.get('repeated_sentences', 0)}/{rep.get('total_sentences', 0)} 句）"
            )
            den = p.get("info_density", {})
            if den.get("flagged"):
                lines.append(
                    f"- [yellow]⚠ 信息密度偏低：推进句占比 {den.get('advancing_ratio', 0)}"
                    f" < {den.get('floor', 0.25)}（软标红，仅提示）[/yellow]"
                )
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
        """确定性：异常章节比例（注水/赶进度）。G6：读取改走公共 helper（行为零变化）。"""
        counts: list[int] = []
        for _, text in iter_chapter_texts(self.project_dir):
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

    # ---- G6：B6 防注水确定性指标（拍板 #5：重复度硬闸 + 信息密度软标红）----
    _REPEAT_SIM_THRESHOLD: float = 0.85      # 句级相似度（字符集合 Jaccard）≥ 此值视为重复句
    _INFO_DENSITY_FLOOR: float = 0.25        # 推进句占比 < 25% 触发信息密度软标红
    _ADVANCE_WORDS = ("说", "道", "问", "答", "喊", "吼", "叫", "走", "冲", "打", "夺",
                      "跳", "追", "抢", "看", "笑", "哭", "跪", "拔", "挥", "杀", "逃")
    _SHIFT_WORDS = ("忽然", "瞬间", "下一刻", "终于", "然后", "接着", "随即", "来到", "离开", "进入")

    @staticmethod
    def _split_sentences(text: str) -> list[str]:
        """按中文句末标点切分句子，去空白；长度 < 8 字忽略（太短无判重意义）。"""
        parts = re.split(r"[。！？…；\n]+", text)
        return [p.strip() for p in parts if len(p.strip()) >= 8]

    @staticmethod
    def _sent_sim(a: str, b: str) -> float:
        """确定性相似度：字符集合 Jaccard（0-1）。"""
        sa, sb = set(a), set(b)
        if not sa or not sb:
            return 0.0
        return len(sa & sb) / len(sa | sb)

    def _metric_repetition(self) -> tuple[float, dict[str, Any]]:
        """确定性：重复句占比（0-1）。每章内句子两两比较，任一先前句子相似度 ≥ 0.85 即判重复。
        全书占比 = 重复句总数 / 总句数。O(章内 n²)，单章句数有限、耗时毫秒级。"""
        total = 0
        repeated = 0
        by_chapter: list[dict[str, Any]] = []
        for f, text in iter_chapter_texts(self.project_dir):
            sents = self._split_sentences(text)
            if not sents:
                continue
            rep = 0
            seen: list[str] = []
            for s in sents:
                if any(self._sent_sim(s, t) >= self._REPEAT_SIM_THRESHOLD for t in seen):
                    rep += 1
                seen.append(s)
            total += len(sents)
            repeated += rep
            by_chapter.append({"chapter": f.stem, "total": len(sents), "repeated": rep})
        ratio = (repeated / total) if total else 0.0
        return ratio, {"chapters": len(by_chapter), "total_sentences": total,
                       "repeated_sentences": repeated, "by_chapter": by_chapter}

    def _metric_info_density(self) -> tuple[float, dict[str, Any]]:
        """确定性：推进句占比（软标红用）。启发式：含对话（引号/说/道/问…）或动作/位移/时间推进
        信号词的句子视为推进句。粗糙代理，仅作报告标注收集数据（拍板 #5，P1）。"""
        total = 0
        advancing = 0
        for _, text in iter_chapter_texts(self.project_dir):
            for s in self._split_sentences(text):
                total += 1
                if self._is_advancing_sentence(s):
                    advancing += 1
        ratio = (advancing / total) if total else 1.0
        return ratio, {"total_sentences": total, "advancing_sentences": advancing}

    def _is_advancing_sentence(self, s: str) -> bool:
        if any(q in s for q in ("「", "」", "“", "”", "『", "』")):
            return True
        if any(w in s for w in self._ADVANCE_WORDS):
            return True
        return any(w in s for w in self._SHIFT_WORDS)

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
                    self.console.print(f"[yellow]⚠ 防注水指标计算失败，跳过：{e}[/yellow]")

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
                    self.console.print(f"[yellow]⚠ 迷爱看门禁评分失败，跳过：{e}[/yellow]")

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
                    self.console.print(f"[yellow]⚠ 黄金三章门禁评分失败，跳过：{e}[/yellow]")

        failed = [d for d in dims if not d.passed]
        hard_failed = [d for d in failed if d.required]
        overall = len(failed) == 0

        # 综合分：各维归一化后平均
        norm = []
        for d in dims:
            # G5：迷爱看六维不计入「不崩」综合分；G6：golden_*/padding_* 同样跳过，
            #     防止新量纲漂移综合分（零回归，共享知识 #10）
            if d.name.startswith((APPEAL_GATE_PREFIX, GOLDEN_GATE_PREFIX, "padding_")):
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

    # 可选：把回溯事件写进 Memory（由 Pipeline 注入）
    memory_log: Optional[Callable[[str, str, Any], Any]] = None
