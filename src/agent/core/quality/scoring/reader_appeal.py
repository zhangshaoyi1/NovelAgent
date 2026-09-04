"""B1 真 LLM 追读力 / 读者吸引力评分器（迷爱看核心）

把 Evaluator 现有 5 个「pass 默认」维度升级为**真实 LLM 评分**，并新增作者侧可直接使用的
「迷爱看」6 维评分（钩子强度/爽点密度/代入感/人物弧光/世界观新颖度/情绪曲线）。

两条使用路径：
1. ``score(dimension, project_dir) -> float``：签名兼容 ``EvaluatorAgent.score_fn``，
   让全书「不崩」终审的 人设稳定/设定一致/连贯/追读/逻辑 维度由真 LLM 判定
   （替代离线时的满分安全默认）。供 ``evaluate --real-score`` 启用。
2. ``score_chapter(chapter_text, ...) -> ReaderAppealReport``：作者侧独立评分，
   直接回答「读者会不会爱看」，给出 6 维分数 + 一句话感受 + 改进建议。供 ``/appeal`` 命令。

降级不阻断（项目哲学）：
- LLM 不可达 / 调用异常 / 返回无法解析 → ``score`` 回退 Evaluator 安全默认；
  ``score_chapter`` 返回 ``llm_used=False`` 的占位报告，绝不抛异常中断用户流程。
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from llmagent.gateway import Gateway
from rich.console import Console

from agent.core.story.chapters import (  # G6：公共章节读取 helper（消除根因 B6-3 重复实现）
    iter_chapter_texts,
    list_chapter_files,
    read_chapters_text,
    strip_frontmatter,
    take_chapter_files,
)
from agent.utils import parse_llm_json
from agent.client.gateway_adapter import chat_utility, create_gateway
from agent.core.infra.prompt_manager import pm


# ============================================================
# 提示词
# ============================================================
# Evaluator 维度评分（单维，要求 LLM 给一个数值）
_EVAL_DIM_LABELS = {
    "character_stability_high": "人设稳定性（角色言行/动机是否前后矛盾，逐项列举崩坏处数量）",
    "setting_consistency_high": "设定一致性（境界/金手指/世界观规则是否被打破，逐项列举冲突数量）",
    "logic_holes": "逻辑漏洞（情节硬伤/因果不成立，逐项列举漏洞数量）",
    "coherence": "连贯性（章节衔接/叙事流畅度，0-100 评分）",
    "readability": "追读力/可读性（让人想继续读的欲望，0-100 评分）",
}

# 迷爱看 6 维（作者侧独立评分）
APPEAL_DIMENSIONS = {
    "hook_strength": "章末钩子强度（让读者想翻下一章的抓力）",
    "payoff_density": "爽点密度（反转/打脸/成长/揭密的爽感浓度）",
    "immersion": "代入感（视角稳定、细节可信、情绪可被带入）",
    "character_arc": "人物弧光（角色有成长/转变，不是工具人）",
    "world_novelty": "世界观新颖度（设定有新意、有记忆点）",
    "emotion_curve": "情绪曲线（节奏起伏有呼吸感，不Flat不注水）",
}

APPEAL_WEIGHTS = {
    "hook_strength": 0.20,
    "payoff_density": 0.20,
    "immersion": 0.20,
    "character_arc": 0.15,
    "world_novelty": 0.10,
    "emotion_curve": 0.15,
}

# G5 门禁合格线（主理人拍板 #3：综合线 + 单维触底兜底）
APPEAL_PASS_LINE: int = 60        # 综合分合格线（可被 --appeal-threshold 覆盖）
APPEAL_DIM_FLOOR: int = 40        # 单维触底兜底线
APPEAL_GATE_PREFIX: str = "appeal_"   # 六维 DimensionResult 名前缀
APPEAL_LABELS: dict[str, str] = {     # 短中文标签（展示 + is_pass 失败维命名）
    "hook_strength": "钩子强度",
    "payoff_density": "爽点密度",
    "immersion": "代入感",
    "character_arc": "人物弧光",
    "world_novelty": "世界观新颖度",
    "emotion_curve": "情绪曲线",
}

# ---- G6：黄金三章门禁常量（主理人拍板 #3：复用 G5 阈值 60/40，可被 CLI 覆盖）----
GOLDEN_PASS_LINE: int = 60          # 三章拼接综合分合格线（--golden-three-threshold 覆盖）
GOLDEN_DIM_FLOOR: int = 40          # 单维触底线（--golden-three-floor 覆盖）
GOLDEN_GATE_PREFIX: str = "golden_" # golden_* DimensionResult 名前缀
GOLDEN_JOIN_CHAR_LIMIT: int = 10000 # 与 score_chapter 截断（行 315）对齐；超长 fallback 每章独立评分

# G2 计数类维度集合（以 issues 重算 value）；评分类维度用自报 value。
COUNT_DIMS = {"character_stability_high", "setting_consistency_high", "logic_holes"}
# 计入硬门禁的 severity 集合（high 必计、mid 计入以收紧；low 仅上报，不计入门禁）。
SEVERITY_GATE = {"high", "mid"}

_EVAL_SYSTEM_PROMPT = """你是一位苛刻的网文总编，负责用真实标准给小说维度打分。
只输出 JSON，不要任何解释文字。格式：
{"value": <数字>, "rationale": "<一句话理由>", "issues": [{"type": "人设|设定|逻辑", "severity": "high|mid|low", "desc": "<逐条描述>"}]}
- 计数类维度（人设稳定/设定一致/逻辑漏洞）：对文本中每一个独立的硬伤/漏洞分别列举一条 issue，逐项列举、不得合并多条为一条；不得因"情节需要/伏笔/铺垫/人设成长"等理由豁免；凡确凿的设定/人设/因果冲突均计入（移除"明显"限定）。value 必须等于 issues 中计入门禁的条数（severity 为 high 或 mid 计入，low 仅上报）。
- 评分类维度（连贯性/追读力）：value 是 0-100 的整数评分；仅在确凿流畅、有追更欲时给 80+，衔接生硬/平铺直叙不得给高分；给出分数须有依据，不给水分为满分。
严格客观，确有问题时给低分。"""

_APPEAL_SYSTEM_PROMPT = """你是一位资深网文编辑兼重度读者，评估这一章「读者会不会爱看」。
只输出 JSON，不要任何解释文字。格式：
{
  "dimensions": {
    "hook_strength": <0-100>,
    "payoff_density": <0-100>,
    "immersion": <0-100>,
    "character_arc": <0-100>,
    "world_novelty": <0-100>,
    "emotion_curve": <0-100>
  },
  "one_liner": "<一句话读者感受，≤30字>",
  "suggestions": ["<改进建议1>", "<改进建议2>"]
}
每个维度独立、客观打分，不给水分为满分；确有短板给低分并给可操作建议。"""


# ============================================================
# 报告
# ============================================================
@dataclass
class ReaderAppealReport:
    """迷爱看 6 维评分报告。"""

    dimensions: dict[str, int]
    total_score: int
    one_liner: str
    suggestions: list[str]
    llm_used: bool = True
    error: str = ""
    # G5：评分来源标记（"llm" 真评测 / "offline" 离线降级占位）
    source: str = "llm"
    # G6：本次评分实际评的章节数（拼接=1，fallback=3）
    chapters_scored: int = 1
    # G6：True=超长回退为每章独立评分取最差
    fallback: bool = False
    # 维度中文标签（展示用）
    labels: dict[str, str] = field(default_factory=lambda: {
        "hook_strength": "钩子强度",
        "payoff_density": "爽点密度",
        "immersion": "代入感",
        "character_arc": "人物弧光",
        "world_novelty": "世界观新颖度",
        "emotion_curve": "情绪曲线",
    })
    # ---- G7：人话总结行（主理人拍板 2：表格前插入总结段；离线分支原样已有人话）----
    summary_lines: list[str] = field(default_factory=list)

    @staticmethod
    def _compute_total(dims: dict[str, int]) -> int:
        total = 0.0
        for k, w in APPEAL_WEIGHTS.items():
            total += dims.get(k, 0) * w
        return int(round(total))

    def to_dict(self) -> dict[str, Any]:
        return {
            "dimensions": dict(self.dimensions),
            "total_score": self.total_score,
            "one_liner": self.one_liner,
            "suggestions": list(self.suggestions),
            "llm_used": self.llm_used,
            "error": self.error,
            "source": self.source,
            # ---- G7（只增不删）：人话总结行 ----
            "summary_lines": list(self.summary_lines),
        }

    def to_markdown(self) -> str:
        if not self.llm_used:
            return (
                "# 迷爱看评分（不可用）\n\n"
                f"> LLM 不可用，无法评分：{self.error or '未知错误'}\n\n"
                "配置真实 LLM（把 .env_ai 复制为 .env）后即可获得真实读者吸引力评分。"
            )
        lines = ["# 迷爱看评分报告", ""]
        # ---- G7：人话总结段（表格前插入；summary_lines 为空则跳过，离线分支原样）----
        if self.summary_lines:
            lines.append("## 一句话总结")
            for ln in self.summary_lines:
                lines.append(f"- {ln}")
            lines.append("")
        verdict = _verdict(self.total_score)
        lines.append(f"**总评分**：{self.total_score}/100 （{verdict}）")
        lines.append(f"> {self.one_liner}")
        lines.append("")
        lines.append("| 维度 | 得分 |")
        lines.append("|---|---|")
        for k in APPEAL_DIMENSIONS:
            lines.append(f"| {self.labels.get(k, k)} | {self.dimensions.get(k, 0)}/100 |")
        if self.suggestions:
            lines.append("")
            lines.append("## 改进建议")
            for i, s in enumerate(self.suggestions, 1):
                lines.append(f"{i}. {s}")
        return "\n".join(lines)


def _verdict(score: int) -> str:
    if score >= 85:
        return "会追更、会安利"
    if score >= 75:
        return "会追更"
    if score >= 60:
        return "可看可不看"
    if score >= 45:
        return "勉强能看"
    return "容易弃书"


# ============================================================
# G7 人话总结行（主理人拍板 1/2：确定性拼装、零 LLM；表格前插总结段）
# ============================================================
def build_appeal_summary_lines(report: "ReaderAppealReport") -> list[str]:
    """确定性拼装迷爱看人话总结行（零 LLM，素材全取自 ReaderAppealReport）。

    失败维判定与 is_pass（行 381-397）同源：单维 < APPEAL_DIM_FLOOR(40) 或
    综合 total_score < APPEAL_PASS_LINE(60)。建议来源：LLM suggestions 优先 + 维度模板兜底。
    离线（llm_used=False）返回 []（to_markdown 离线分支已有人话，不重复）。
    """
    if not report.llm_used:
        return []
    lines: list[str] = []
    total = report.total_score
    if total < APPEAL_PASS_LINE:
        lines.append(
            f"综合分 {total}/100 未达合格线 {APPEAL_PASS_LINE}"
            f"（{_verdict(total)}）——读者吸引力整体偏弱，建议按下方建议提升。"
        )
    for k, v in report.dimensions.items():
        if v < APPEAL_DIM_FLOOR:
            label = APPEAL_LABELS.get(k, k)
            gap = APPEAL_DIM_FLOOR - v
            lines.append(
                f"{label}：实测 {v} ＜ 触底线 {APPEAL_DIM_FLOOR}（差 {gap}）"
            )
    # 下一步建议：LLM suggestions 优先（注明来源），无则维度级模板兜底
    if report.suggestions:
        lines.append("下一步建议（来自 LLM）：")
        for i, s in enumerate(report.suggestions[:5], 1):
            lines.append(f"  {i}. {s}")
    else:
        lines.append("下一步建议（模板）：请针对上述未达触底线的维度逐项优化。")
    return lines


# ============================================================
# 评分器
# ============================================================
class ReaderAppealScorer:
    """真 LLM 评分器（迷爱看），离线优雅降级。

    Args:
        llm_client: LLM 客户端；None 则内部惰性构造。
        console: rich 控制台。
    """

    def __init__(
        self,
        llm_client: Gateway | None = None,
        console: Console | None = None,
    ) -> None:
        self._llm = llm_client
        self.console = console or Console()
        # G2：保存各维度最近一次结构化评分结果（含 issues/rationale），供报告展示。
        self._last_eval: dict[str, dict] = {}

    @property
    def llm(self) -> Gateway:
        if self._llm is None:
            self._llm = create_gateway()
        return self._llm

    # ---------------------------------------------------------- 路径 1：Evaluator 维度
    def score(self, dimension: str, project_dir: str | Path) -> float:
        """兼容 ``EvaluatorAgent.score_fn``：对单维做真 LLM 评分。

        G2：计数类维度以 LLM 列举的 ``issues`` 为准重算 value（忽略自报，
        封堵"报 0 实则列举 N 条"的漏判）；评分维无 issues 时回退自报 value。
        结果（value/rationale/issues）存入 ``self._last_eval[dimension]``。
        LLM 不可用时回退 Evaluator 安全默认（硬计数维 0、评分维满分）。
        """
        try:
            text = self._gather_for_eval(dimension, str(project_dir))
            if not text:
                return self._default_for(dimension)
            prompt = (
                f"请评估以下小说片段在「{_EVAL_DIM_LABELS.get(dimension, dimension)}」"
                f"维度上的表现。\n\n{text[:8000]}"
            )
            # 思考型模型（如 dots3-note-prev）即便 enable_thinking=False 也会产思考，
            # 预算过小会被思考占满 → content 为空或 JSON 被截断、解析失败。
            # 真实小说实测：评分/计数维均须 ≥8192 才稳定出完整 JSON。
            resp = chat_utility(self.llm,
                messages=[
                    {"role": "system", "content": pm.get("quality.reader_appeal_eval").system},
                    {"role": "user", "content": prompt},
                ],
                temperature=0.2,
                max_tokens=8192,
                enable_thinking=False,
            )
            data = parse_llm_json(resp)
            issues = data.get("issues") or []
            if dimension in COUNT_DIMS and issues:
                # 以 issues 为准重算：仅计入 severity ∈ SEVERITY_GATE 的条数，忽略 LLM 自报 value。
                val = float(sum(
                    1 for it in issues
                    if str(it.get("severity", "")).lower() in SEVERITY_GATE
                ))
            else:
                # 评分维或无 issues：回退 LLM 自报 value（向后兼容、行为不变）。
                val = float(data.get("value", 0))
        except Exception as e:  # noqa: BLE001 - LLM 不可达/解析失败：降级默认
            if self.console is not None:
                self.console.print(f"[yellow]⚠ 维度 {dimension} 评分降级为默认：{e}[/yellow]")
            return self._default_for(dimension)
        value = self._clamp(dimension, val)
        # G2：结构化结果落 _last_eval（issues/rationale），不扩展 score_fn 返回协议。
        self._last_eval[dimension] = {
            "value": value,
            "rationale": data.get("rationale", ""),
            "issues": issues,
        }
        return value

    @staticmethod
    def _default_for(dimension: str) -> float:
        # 与 EvaluatorAgent._score 安全默认保持一致
        if dimension in ("coherence", "readability"):
            return 100.0
        return 0.0

    @staticmethod
    def _clamp(dimension: str, val: float) -> float:
        if dimension in ("coherence", "readability"):
            return max(0.0, min(100.0, val))
        # G2：计数类维非负整数化（float(int(max(0, val)))），吸收 LLM 噪声与小数。
        return float(int(max(0, val)))

    def _gather_for_eval(self, dimension: str, project_dir: str) -> str:
        """收集评分所需文本（最新 1-3 章正文 + 世界观简介）。"""
        d = Path(project_dir)
        parts: list[str] = []
        # 世界观简介
        world = d / "world.md"
        if world.exists():
            content = world.read_text(encoding="utf-8")
            idx = content.find("## 故事简介")
            if idx >= 0:
                parts.append("【世界观简介】" + content[idx: idx + 400])
        # 最新章节（最多 3 章）——复用公共 helper（G6，消除根因 B6-3 重复实现）
        for f in take_chapter_files(list_chapter_files(project_dir), side="last", n=3):
            try:
                text = strip_frontmatter(f.read_text(encoding="utf-8")).strip()
            except OSError:
                continue
            parts.append(f"【{f.stem}】\n{text[:2500]}")
        return "\n\n".join(parts)

    # ---------------------------------------------------------- 路径 2：迷爱看 6 维
    def score_chapter(
        self,
        chapter_text: str,
        *,
        title: str = "",
        genre: str = "",
        synopsis: str = "",
    ) -> ReaderAppealReport:
        """作者侧独立评分：迷爱看 6 维。LLM 不可用返回占位报告。"""
        context = ""
        if title:
            context += f"【章节标题】{title}\n"
        if genre:
            context += f"【题材】{genre}\n"
        if synopsis:
            context += f"【世界观简介】{synopsis[:300]}\n"
        user_prompt = (
            f"{context}\n【本章正文】\n{chapter_text[:10000]}"
        )
        try:
            resp = chat_utility(self.llm,
                messages=[
                    {"role": "system", "content": pm.get("quality.reader_appeal").system},
                    {"role": "user", "content": user_prompt},
                ],
                temperature=0.3,
                # 思考型模型需留足预算才能产出完整 JSON（实测 ≥8192 稳）。
                max_tokens=8192,
                enable_thinking=False,
            )
            return self._parse_appeal(resp)
        except Exception as e:  # noqa: BLE001 - LLM 不可达：降级占位报告
            if self.console is not None:
                self.console.print(f"[yellow]⚠ 迷爱看评分降级（LLM 不可用）：{e}[/yellow]")
            return ReaderAppealReport(
                dimensions={k: 0 for k in APPEAL_DIMENSIONS},
                total_score=0,
                one_liner="LLM 不可用，无法评分",
                suggestions=[],
                llm_used=False,
                error=str(e),
                source="offline",
            )

    def _parse_appeal(self, raw: str) -> ReaderAppealReport:
        data = parse_llm_json(raw)
        dims_raw = data.get("dimensions", {}) or {}
        dims: dict[str, int] = {}
        for k in APPEAL_DIMENSIONS:
            try:
                v = int(dims_raw.get(k, 0))
            except (TypeError, ValueError):
                v = 0
            dims[k] = max(0, min(100, v))
        total = ReaderAppealReport._compute_total(dims)
        suggestions = [str(s) for s in (data.get("suggestions", []) or [])][:5]
        one_liner = str(data.get("one_liner", ""))[:60]
        return ReaderAppealReport(
            dimensions=dims,
            total_score=total,
            one_liner=one_liner,
            suggestions=suggestions,
            llm_used=True,
        )


# ============================================================
# G5 门禁判定 + 按章节取末章评分助手
# ============================================================
def is_pass(
    report: "ReaderAppealReport",
    threshold: int = APPEAL_PASS_LINE,
    floor: int = APPEAL_DIM_FLOOR,
) -> tuple[bool, list[str]]:
    """综合分 >= threshold 且 每个单维 >= floor 才通过。

    Returns:
        (passed, failed_dims)：passed=True 当且仅当
        综合 total_score >= threshold 且 每个维度 >= floor；
        failed_dims 为不达标维度（单维触底）的中文标签列表。
    """
    failed_dims = [
        APPEAL_LABELS.get(k, k) for k, v in report.dimensions.items() if v < floor
    ]
    passed = (report.total_score >= threshold) and (len(failed_dims) == 0)
    return passed, failed_dims


def gate_chapter(
    scorer: "ReaderAppealScorer",
    project_dir: str | Path,
    window: int = 1,
    *,
    title: str = "",
    genre: str = "",
    synopsis: str = "",
) -> "ReaderAppealReport":
    """读末 window 章正文拼接，调 scorer.score_chapter 得迷爱看报告。

    无 chapters 目录或 LLM 不可达时返回 llm_used=False 占位报告（不抛异常）；
    synopsis 为空时尝试从 project_dir/world.md 的『## 故事简介』段提取
    （复用 _gather_for_eval 风格）。章节文件匹配 ch*.md，去 frontmatter
    （以 '---' 开头则切掉首段）。G6：读取改走公共 helper（行为零变化）。
    """
    d = Path(project_dir)
    if not list_chapter_files(project_dir):
        # 无章节可评：返回离线占位（不抛异常），由调用方短路为通过。
        return ReaderAppealReport(
            dimensions={k: 0 for k in APPEAL_DIMENSIONS},
            total_score=0,
            one_liner="无章节可评",
            suggestions=[],
            llm_used=False,
            error="no chapters dir",
            source="offline",
        )
    texts = read_chapters_text(project_dir, side="last", n=window)
    chapter_text = "\n\n".join(texts)

    # synopsis 为空时尝试从 world.md 的『## 故事简介』段提取（复用 _gather_for_eval 风格）
    if not synopsis:
        world = d / "world.md"
        if world.exists():
            try:
                content = world.read_text(encoding="utf-8")
                idx = content.find("## 故事简介")
                if idx >= 0:
                    synopsis = content[idx: idx + 300]
            except OSError:
                pass

    return scorer.score_chapter(
        chapter_text, title=title, genre=genre, synopsis=synopsis
    )


def gate_first_chapters(
    scorer: "ReaderAppealScorer",
    project_dir: str | Path,
    n: int = 3,
    *,
    title: str = "",
    genre: str = "",
    synopsis: str = "",
) -> "ReaderAppealReport":
    """B4 黄金三章门禁评分：读前 n 章（默认 3）正文。

    评测方式（拍板 #3，C 拼接一次评分默认）：
    - 三章正文拼接为一段 → scorer.score_chapter 一次评分（成本 = 终审多 1 次 LLM 调用）；
    - 拼接长度超 GOLDEN_JOIN_CHAR_LIMIT(10000)（score_chapter 内部会截断，等价于只评了开头）
      → **fallback 每章独立评分取最差**：对每章分别 score_chapter，逐维取 min、
      total_score 取 min，llm_used = 任一在线（all 在线才 True），并置 fallback=True。
    离线（LLM 不可用）时各次 score_chapter 返回 llm_used=False 占位，由 Evaluator 短路为通过。
    无章节可评返回 llm_used=False 占位（不抛异常，仿 gate_chapter 行 403-413）。
    """
    files = take_chapter_files(list_chapter_files(project_dir), side="first", n=n)
    if not files:
        return ReaderAppealReport(
            dimensions={k: 0 for k in APPEAL_DIMENSIONS},
            total_score=0, one_liner="无章节可评", suggestions=[],
            llm_used=False, error="no chapters dir", source="offline",
        )
    texts = read_chapters_text(project_dir, side="first", n=n)
    joined = "\n\n".join(texts)

    if len(joined) <= GOLDEN_JOIN_CHAR_LIMIT:
        report = scorer.score_chapter(joined, title=title, genre=genre, synopsis=synopsis)
        report.chapters_scored = 1
        return report

    # fallback：超长 → 每章独立评分取最差（拍板 #3）
    worst: dict[str, int] = {k: 100 for k in APPEAL_DIMENSIONS}
    worst_total = 100
    any_online = False
    for t in texts:
        r = scorer.score_chapter(t, title=title, genre=genre, synopsis=synopsis)
        if r.llm_used:
            any_online = True
        for k in APPEAL_DIMENSIONS:
            worst[k] = min(worst.get(k, 100), r.dimensions.get(k, 0))
        worst_total = min(worst_total, r.total_score)
    return ReaderAppealReport(
        dimensions=worst,
        total_score=worst_total,
        one_liner="三章拼接超长，已按每章独立评分取最差",
        suggestions=[],
        llm_used=any_online,
        source="llm" if any_online else "offline",
        chapters_scored=n,
        fallback=True,
    )
