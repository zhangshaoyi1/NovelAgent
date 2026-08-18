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

from rich.console import Console

from agent.core.llm_client import LLMClient
from agent.utils import parse_llm_json


# ============================================================
# 提示词
# ============================================================
# Evaluator 维度评分（单维，要求 LLM 给一个数值）
_EVAL_DIM_LABELS = {
    "character_stability_high": "人设稳定性（角色言行/动机是否前后矛盾，统计明显崩坏处数量）",
    "setting_consistency_high": "设定一致性（境界/金手指/世界观规则是否被打破，统计明显冲突数量）",
    "logic_holes": "逻辑漏洞（情节硬伤/因果不成立，统计明显漏洞数量）",
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

_EVAL_SYSTEM_PROMPT = """你是一位苛刻的网文总编，负责用真实标准给小说维度打分。
只输出 JSON，不要任何解释文字。格式：
{"value": <数字>, "rationale": "<一句话理由>"}
- 计数类维度（人设稳定/设定一致/逻辑漏洞）：value 是该维度在给定文本中检测到的「明显问题数量」（整数，0 表示无）。
- 评分类维度（连贯性/追读力）：value 是 0-100 的整数评分。
严格客观，不给水分为满分，确有问题时给低分。"""

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
    # 维度中文标签（展示用）
    labels: dict[str, str] = field(default_factory=lambda: {
        "hook_strength": "钩子强度",
        "payoff_density": "爽点密度",
        "immersion": "代入感",
        "character_arc": "人物弧光",
        "world_novelty": "世界观新颖度",
        "emotion_curve": "情绪曲线",
    })

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
        }

    def to_markdown(self) -> str:
        if not self.llm_used:
            return (
                "# 迷爱看评分（不可用）\n\n"
                f"> LLM 不可用，无法评分：{self.error or '未知错误'}\n\n"
                "配置真实 LLM（把 .env_ai 复制为 .env）后即可获得真实读者吸引力评分。"
            )
        lines = ["# 迷爱看评分报告", ""]
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
        llm_client: LLMClient | None = None,
        console: Console | None = None,
    ) -> None:
        self._llm = llm_client
        self.console = console or Console()

    @property
    def llm(self) -> LLMClient:
        if self._llm is None:
            self._llm = LLMClient()
        return self._llm

    # ---------------------------------------------------------- 路径 1：Evaluator 维度
    def score(self, dimension: str, project_dir: str | Path) -> float:
        """兼容 ``EvaluatorAgent.score_fn``：对单维做真 LLM 评分。

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
            resp = self.llm.chat_utility(
                messages=[
                    {"role": "system", "content": _EVAL_SYSTEM_PROMPT},
                    {"role": "user", "content": prompt},
                ],
                temperature=0.2,
                max_tokens=400,
                enable_thinking=False,
            )
            data = parse_llm_json(resp.text)
            val = float(data.get("value", 0))
        except Exception as e:  # noqa: BLE001 - LLM 不可达/解析失败：降级默认
            if self.console is not None:
                self.console.print(f"[yellow]⚠ 维度 {dimension} 评分降级为默认：{e}[/yellow]")
            return self._default_for(dimension)
        return self._clamp(dimension, val)

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
        return max(0.0, val)  # 计数类维：非负

    def _gather_for_eval(self, dimension: str, project_dir: str) -> str:
        """收集评分所需文本（最新 1-3 章正文 + 世界观简介）。"""
        d = Path(project_dir)
        chapters_dir = d / "chapters"
        parts: list[str] = []
        # 世界观简介
        world = d / "world.md"
        if world.exists():
            content = world.read_text(encoding="utf-8")
            idx = content.find("## 故事简介")
            if idx >= 0:
                parts.append("【世界观简介】" + content[idx: idx + 400])
        # 最新章节（最多 3 章）
        if chapters_dir.exists():
            files = sorted(chapters_dir.glob("ch*.md"))[-3:]
            for f in files:
                text = f.read_text(encoding="utf-8")
                if text.startswith("---"):
                    text = text.split("---", 2)[-1]
                parts.append(f"【{f.stem}】\n{text.strip()[:2500]}")
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
            resp = self.llm.chat_utility(
                messages=[
                    {"role": "system", "content": _APPEAL_SYSTEM_PROMPT},
                    {"role": "user", "content": user_prompt},
                ],
                temperature=0.3,
                max_tokens=1200,
                enable_thinking=False,
            )
            return self._parse_appeal(resp.text)
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
