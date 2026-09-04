"""M23 短篇扫榜 + 拆文工作流（外部市场/作品分析）

移植自 oh-story-claudecod 的 story-short-scan 与 story-short-analyze skill，
落地为 NovelAgent 两个工作流：

- ``M23ShortScanWorkflow``（id ``m23_short_scan``）：短篇网文扫榜。
  基于榜单样本（--input 文件）或内置市场知识（skills/short-story/real-market-data.md），
  用 LLM 输出情绪方向、题材候选、风险阈值与验证动作。
- ``M23ShortAnalyzeWorkflow``（id ``m23_short_analyze``）：短篇拆文。
  深度拆解爆款短篇的故事核、结构、情感线、反转设计、写作手法、共鸣层次。

边界声明：本功能是**外部短篇市场/作品分析**，产物默认仅输出报告，可选保存到
项目 ``.state/analyze/``；不写学习库（learnings.json，m17_learn 负责写后沉淀）。
LLM 调用统一走 ``chat_utility`` / ``chat_creative``；LLM 不可用 / 解析失败 → 降级不阻断。

用法：
    wf = M23ShortScanWorkflow()
    report = wf.run(market_data="...榜单文本...", platform="知乎盐言")
    print(report.to_json())
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from rich.console import Console

from agent.client.gateway_adapter import create_gateway, chat_utility
from llmagent.gateway import Gateway
from agent.core.engine.workflow_registry import workflow
from agent.core.infra.prompt_manager import pm
from agent.utils import parse_llm_json

# 知识包目录：src/agent/skills/short-story/
SKILL_DIR = Path(__file__).resolve().parent.parent / "skills" / "short-story"

# 文本截断上限（控制 tokens 成本）
MAX_MARKET_CHARS = 20000  # 榜单数据上限
MAX_INPUT_CHARS = 20000  # 拆文正文上限

# 未提供榜单样本时的占位说明（提示 LLM 标注为候选假设）
NO_MARKET_DATA = "（未提供实时榜单样本，本次分析基于内置历史市场知识，仅作为候选假设，需复扫校验）"


def _read_knowledge(name: str) -> str:
    """读取知识包内指定参考文件（缺失 / 读取失败降级为空）。"""
    f = SKILL_DIR / name
    if not f.is_file():
        return ""
    try:
        return f.read_text(encoding="utf-8")
    except OSError:
        return ""


def _join_knowledge(files: tuple[str, ...]) -> str:
    """把多个参考文件拼接为一段提示词注入文本。"""
    parts: list[str] = []
    for name in files:
        text = _read_knowledge(name).strip()
        if text:
            parts.append(f"## {name}\n{text}")
    return "\n\n".join(parts)


class ShortStoryKnowledge:
    """短篇扫榜/拆文知识包加载器（skills/short-story/）。

    扫榜只需跨平台市场数据；拆文需要输出模板、质量清单、盐言风格与拆文案例。
    """

    SCAN_FILES: tuple[str, ...] = ("real-market-data.md",)
    ANALYZE_FILES: tuple[str, ...] = (
        "output-templates.md",
        "quality-checklist.md",
        "zhihu-style.md",
        "deconstruction-examples.md",
    )

    def __init__(self, skill_dir: Path = SKILL_DIR) -> None:
        self.skill_dir = Path(skill_dir)

    def _read(self, name: str) -> str:
        f = self.skill_dir / name
        if not f.is_file():
            return ""
        try:
            return f.read_text(encoding="utf-8")
        except OSError:
            return ""

    def scan_knowledge(self) -> str:
        """扫榜参考（跨平台市场数据）。"""
        return _join_knowledge(self.SCAN_FILES)

    def analyze_knowledge(self) -> str:
        """拆文参考（输出模板 + 质量清单 + 盐言风格 + 拆文案例）。"""
        return _join_knowledge(self.ANALYZE_FILES)


# ============================================================
# 数据契约：扫榜报告
# ============================================================
@dataclass
class ScanReport:
    """短篇扫榜报告"""

    platform: str = ""
    sample_date: str = ""
    signal_strength: str = ""
    next_rescan: str = ""
    data_source: str = ""
    market_overview: str = ""
    emotion_rank: list[dict[str, Any]] = field(default_factory=list)
    topic_hotspots: list[dict[str, Any]] = field(default_factory=list)
    insights: dict[str, Any] = field(default_factory=dict)
    trend_alerts: list[dict[str, Any]] = field(default_factory=list)
    directions: list[dict[str, Any]] = field(default_factory=list)
    one_liner: str = ""

    # ------ JSON 形态 ------
    def to_dict(self) -> dict[str, Any]:
        return {
            "platform": self.platform,
            "sample_date": self.sample_date,
            "signal_strength": self.signal_strength,
            "next_rescan": self.next_rescan,
            "data_source": self.data_source,
            "market_overview": self.market_overview,
            "emotion_rank": self.emotion_rank,
            "topic_hotspots": self.topic_hotspots,
            "insights": self.insights,
            "trend_alerts": self.trend_alerts,
            "directions": self.directions,
            "one_liner": self.one_liner,
        }

    def to_json(self, indent: int = 2) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=indent)

    # ------ Markdown 形态 ------
    def to_markdown(self) -> str:
        lines: list[str] = []
        lines.append(f"# 短篇网文扫榜报告：{self.platform or '未指定平台'}")
        lines.append("")
        lines.append("## 市场概况")
        lines.append("")
        lines.append(f"- 扫榜时间：{self.sample_date or '-'}")
        lines.append(f"- 信号强度：{self.signal_strength or '-'}")
        lines.append(f"- 复扫节点：{self.next_rescan or '-'}")
        lines.append(f"- 数据来源：{self.data_source or '-'}")
        if self.market_overview:
            lines.append(f"- 核心发现：{self.market_overview}")
        lines.append("")
        # 情绪热度排行
        if self.emotion_rank:
            lines.append("## 情绪热度排行")
            lines.append("")
            lines.append("| 排名 | 情绪类型 | 榜上数量 | 趋势 | 代表作 |")
            lines.append("|------|----------|----------|------|--------|")
            for r in self.emotion_rank:
                lines.append(
                    f"| {r.get('rank', '-')} | {r.get('emotion_type', '-')} "
                    f"| {r.get('count', '-')} | {r.get('trend', '-')} "
                    f"| {r.get('example', '-')} |"
                )
            lines.append("")
        # 题材热点
        if self.topic_hotspots:
            lines.append("## 题材热点")
            lines.append("")
            lines.append("| 题材 | 热度 | 竞争程度 | 门槛 | 代表作 |")
            lines.append("|------|------|----------|------|--------|")
            for t in self.topic_hotspots:
                lines.append(
                    f"| {t.get('topic', '-')} | {t.get('heat', '-')} "
                    f"| {t.get('competition', '-')} | {t.get('barrier', '-')} "
                    f"| {t.get('example', '-')} |"
                )
            lines.append("")
        # 关键数据洞察
        if self.insights:
            lines.append("## 关键数据洞察")
            lines.append("")
            ins = self.insights
            lines.append(f"- 篇幅区间：{ins.get('word_count_range', '-')}")
            lines.append(f"- 开头模式：{ins.get('opening_pattern', '-')}")
            lines.append(f"- 结尾偏好：{ins.get('ending_pref', '-')}")
            lines.append(f"- 标题特征：{ins.get('title_pattern', '-')}")
            lines.append(f"- 人设热词：{ins.get('character_hotwords', '-')}")
            lines.append("")
        # 风口预警
        if self.trend_alerts:
            lines.append("## 风口预警")
            lines.append("")
            icons = {"正在爆发": "🔥", "即将起风": "⚡", "即将饱和": "⚠️"}
            for a in self.trend_alerts:
                icon = icons.get(a.get("type", ""), "·")
                lines.append(f"- {icon} {a.get('type', '')}：{a.get('topic', '-')} — {a.get('basis', '')}")
            lines.append("")
        # 值得写的方向
        if self.directions:
            lines.append("## 值得写的方向")
            lines.append("")
            for i, d in enumerate(self.directions, 1):
                lines.append(
                    f"{i}. {d.get('direction', '-')} + {d.get('emotion_hook', '-')} "
                    f"（{d.get('feasibility', '')}）"
                )
            lines.append("")
        if self.one_liner:
            lines.append("## 一句话")
            lines.append("")
            lines.append(self.one_liner)
            lines.append("")
        return "\n".join(lines)


# ============================================================
# 数据契约：拆文报告
# ============================================================
@dataclass
class AnalyzeReport:
    """短篇拆文报告"""

    title: str = ""
    platform: str = ""
    story_core: dict[str, Any] = field(default_factory=dict)
    summary: str = ""
    pov: str = ""
    timeline: str = ""
    structure: list[dict[str, Any]] = field(default_factory=list)
    emotion_curve: list[dict[str, Any]] = field(default_factory=list)
    explosion: dict[str, Any] = field(default_factory=dict)
    reversal: dict[str, Any] = field(default_factory=dict)
    techniques: list[dict[str, Any]] = field(default_factory=list)
    characters: list[dict[str, Any]] = field(default_factory=list)
    opening: dict[str, Any] = field(default_factory=dict)
    ending: dict[str, Any] = field(default_factory=dict)
    five_dim_score: dict[str, Any] = field(default_factory=dict)
    explosion_power: str = ""
    topicality: str = ""
    resonance: list[dict[str, Any]] = field(default_factory=list)
    reusable_structures: list[dict[str, Any]] = field(default_factory=list)
    writing_actions: str = ""
    rhythm_quick: dict[str, Any] = field(default_factory=dict)
    one_liner_eval: str = ""

    # ------ JSON 形态 ------
    def to_dict(self) -> dict[str, Any]:
        return {
            "title": self.title,
            "platform": self.platform,
            "story_core": self.story_core,
            "summary": self.summary,
            "pov": self.pov,
            "timeline": self.timeline,
            "structure": self.structure,
            "emotion_curve": self.emotion_curve,
            "explosion": self.explosion,
            "reversal": self.reversal,
            "techniques": self.techniques,
            "characters": self.characters,
            "opening": self.opening,
            "ending": self.ending,
            "five_dim_score": self.five_dim_score,
            "explosion_power": self.explosion_power,
            "topicality": self.topicality,
            "resonance": self.resonance,
            "reusable_structures": self.reusable_structures,
            "writing_actions": self.writing_actions,
            "rhythm_quick": self.rhythm_quick,
            "one_liner_eval": self.one_liner_eval,
        }

    def to_json(self, indent: int = 2) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=indent)

    # ------ Markdown 形态 ------
    def to_markdown(self) -> str:
        lines: list[str] = []
        lines.append(f"# 短篇拆文报告：{self.title or '未命名'}")
        lines.append("")
        if self.platform:
            lines.append(f"- **来源平台**：{self.platform}")
        if self.pov:
            lines.append(f"- **POV**：{self.pov}")
        if self.timeline:
            lines.append(f"- **叙事时间线**：{self.timeline}")
        lines.append("")
        # 故事核
        if self.story_core:
            lines.append("## 故事核")
            lines.append("")
            lines.append(f"**设定**：{self.story_core.get('setting', '')}")
            lines.append(f"**主题**：{self.story_core.get('theme', '')}")
            lines.append(f"**核心行动**：{self.story_core.get('core_action', '')}")
            if self.story_core.get("one_liner"):
                lines.append(f"**一句话**：{self.story_core['one_liner']}")
            lines.append("")
        if self.summary:
            lines.append("## 故事梗概")
            lines.append("")
            lines.append(self.summary)
            lines.append("")
        # 结构
        if self.structure:
            lines.append("## 结构划分")
            lines.append("")
            lines.append("| 段落 | 字数范围 | 占比 | 功能 | 对应节 |")
            lines.append("|------|----------|------|------|--------|")
            for s in self.structure:
                lines.append(
                    f"| {s.get('segment', '-')} | {s.get('word_range', '-')} "
                    f"| {s.get('ratio', '-')} | {s.get('function', '-')} "
                    f"| {s.get('sections', '-')} |"
                )
            lines.append("")
        # 情感曲线
        if self.emotion_curve:
            lines.append("## 情感曲线")
            lines.append("")
            lines.append("| 位置 | 字数 | 节点 | 情绪 | 强度 | 触发事件 | 钩子类型 |")
            lines.append("|------|------|------|------|------|----------|----------|")
            for e in self.emotion_curve:
                lines.append(
                    f"| {e.get('position', '-')} | {e.get('word_count', '-')} "
                    f"| {e.get('node', '-')} | {e.get('emotion', '-')} "
                    f"| {e.get('intensity', '-')} | {e.get('trigger', '-')} "
                    f"| {e.get('hook_type', '-')} |"
                )
            lines.append("")
        # 爆点
        if self.explosion:
            lines.append("## 爆点分析")
            lines.append("")
            dims = (
                ("prelude", "铺垫"),
                ("accumulation", "积累"),
                ("delay", "延迟"),
                ("burst", "爆发点"),
                ("aftermath", "余波"),
                ("impression", "印象"),
            )
            for key, label in dims:
                val = self.explosion.get(key, "")
                if val:
                    lines.append(f"- **{label}**：{val}")
            lines.append("")
        # 反转
        if self.reversal:
            lines.append("## 反转设计")
            lines.append("")
            lines.append(f"- **类型**：{self.reversal.get('type', '-')}")
            fh = self.reversal.get("foreshadowing") or []
            lines.append(f"- **铺垫线索**：{'；'.join(str(x) for x in fh) or '-'}")
            lines.append(f"- **误导方向**：{self.reversal.get('mislead', '-')}")
            lines.append(f"- **真相揭示**：{self.reversal.get('reveal', '-')}")
            lines.append(f"- **时机**：{self.reversal.get('timing', '-')}")
            lines.append(
                f"- **效果**：惊喜 {self.reversal.get('surprise', '-')} · "
                f"合理 {self.reversal.get('plausibility', '-')} · "
                f"冲击 {self.reversal.get('impact', '-')}"
            )
            lines.append("")
        # 写作手法
        if self.techniques:
            lines.append("## 写作手法")
            lines.append("")
            for i, t in enumerate(self.techniques, 1):
                lines.append(
                    f"{i}. **{t.get('name', '-')}**（{t.get('position', '-')}）："
                    f"{t.get('effect', '')} —— 可复用度 {t.get('reusability', '-')}"
                )
            lines.append("")
        # 人物
        if self.characters:
            lines.append("## 人物")
            lines.append("")
            lines.append("| 人物 | 叙事角色 | 行动角色 | 功能标签 | 关键台词 |")
            lines.append("|------|----------|----------|----------|----------|")
            for c in self.characters:
                lines.append(
                    f"| {c.get('name', '-')} | {c.get('narrative_role', '-')} "
                    f"| {c.get('action_role', '-')} | {c.get('function', '-')} "
                    f"| {c.get('key_line', '-')} |"
                )
            lines.append("")
        # 开头 / 结尾
        if self.opening:
            lines.append("## 开头分析")
            lines.append("")
            lines.append(f"- **前 3 句**：{self.opening.get('first_3_sentences', '-')}")
            lines.append(f"- **钩子类型**：{self.opening.get('hook_type', '-')}")
            lines.append(
                f"- **前50字冲突**：{self.opening.get('conflict_in_50', '-')} · "
                f"**前100字知核心矛盾**：{self.opening.get('core_conflict_in_100', '-')}"
            )
            lines.append(
                f"- **信息密度**：{self.opening.get('info_density', '-')} · "
                f"**代入感**：{self.opening.get('empathy', '-')} · "
                f"**情绪强度**：{self.opening.get('intensity', '-')}/10"
            )
            lines.append("")
        if self.ending:
            lines.append("## 结尾分析")
            lines.append("")
            lines.append(f"- **类型**：{self.ending.get('type', '-')}")
            lines.append(f"- **情绪落点**：{self.ending.get('emotional_landing', '-')}")
            lines.append(f"- **余韵**：{self.ending.get('afterglow', '-')}")
            lines.append(f"- **传播欲**：{self.ending.get('share_power', '-')}")
            lines.append(f"- **收束完整性**：{self.ending.get('closure', '-')}")
            lines.append(f"- **情绪强度**：{self.ending.get('intensity', '-')}/10")
            lines.append("")
        # 五维评分
        if self.five_dim_score:
            labels = (
                ("opening_attraction", "开头吸引力"),
                ("emotion_pull", "情感拉扯力"),
                ("reversal_design", "反转设计"),
                ("pacing_control", "节奏控制"),
                ("ending_afterglow", "结尾余韵"),
            )
            lines.append("## 五维评分")
            lines.append("")
            lines.append("| 维度 | 评分 | 说明 |")
            lines.append("|------|------|------|")
            for key, label in labels:
                item = self.five_dim_score.get(key, {}) or {}
                lines.append(f"| {label} | {item.get('score', '-')}/5 | {item.get('note', '')} |")
            lines.append("")
        # 爆点性 / 话题性
        if self.explosion_power:
            lines.append("## 爆点性")
            lines.append("")
            lines.append(self.explosion_power)
            lines.append("")
        if self.topicality:
            lines.append("## 话题性")
            lines.append("")
            lines.append(self.topicality)
            lines.append("")
        # 共鸣
        if self.resonance:
            lines.append("## 共鸣分析")
            lines.append("")
            for r in self.resonance:
                lines.append(
                    f"- **{r.get('layer', '-')}**（{r.get('strength', '-')}）："
                    f"{r.get('trigger', '')}"
                )
            lines.append("")
        # 可复用结构
        if self.reusable_structures:
            lines.append("## 可复用结构")
            lines.append("")
            for i, s in enumerate(self.reusable_structures, 1):
                lines.append(
                    f"{i}. **{s.get('name', '-')}**：{s.get('usage', '')} — "
                    f"适用场景：{s.get('scenario', '')}"
                )
            lines.append("")
        # 写作动作 / 节奏速报
        if self.writing_actions:
            lines.append("## 同类型写作动作")
            lines.append("")
            lines.append(self.writing_actions)
            lines.append("")
        if self.rhythm_quick:
            lines.append("## 节奏速报")
            lines.append("")
            lines.append(
                f"- 事件密度：{self.rhythm_quick.get('event_density', '-')} · "
                f"对话密度：{self.rhythm_quick.get('dialogue_density', '-')} · "
                f"冲突密度：{self.rhythm_quick.get('conflict_density', '-')}"
            )
            lines.append("")
        if self.one_liner_eval:
            lines.append("## 一句话评价")
            lines.append("")
            lines.append(self.one_liner_eval)
            lines.append("")
        return "\n".join(lines)


# ============================================================
# 工作流：短篇扫榜
# ============================================================
@workflow("m23_short_scan")
class M23ShortScanWorkflow:
    """短篇网文扫榜工作流：基于榜单样本/内置知识输出市场分析报告"""

    def __init__(
        self,
        llm_client: Gateway | None = None,
        console: Console | None = None,
        skill_dir: Path = SKILL_DIR,
    ) -> None:
        self.llm = llm_client or create_gateway()
        self.console = console or Console()
        self.knowledge = ShortStoryKnowledge(skill_dir)

    def run(
        self,
        market_data: str = "",
        platform: str = "综合",
        sample_date: str = "",
    ) -> ScanReport:
        """执行短篇扫榜。

        Args:
            market_data: 榜单样本原文（可来自 --input 文件）。为空则基于内置
                市场知识分析（结果标注为候选假设，需复扫校验）。
            platform: 目标平台（知乎盐言/七猫/黑岩/点众/综合）。
            sample_date: 样本日期（默认今天）。

        Returns:
            ScanReport
        """
        market_data = (market_data or "").strip()[:MAX_MARKET_CHARS]
        if not market_data:
            market_data = NO_MARKET_DATA
        sample_date = sample_date or datetime.now().strftime("%Y-%m-%d")
        knowledge = self.knowledge.scan_knowledge()

        prompt = pm.get("m23.short_scan")
        messages = [
            {"role": "system", "content": prompt.system},
            {
                "role": "user",
                "content": prompt.render_user(
                    platform=platform,
                    market_data=market_data,
                    knowledge=knowledge,
                ),
            },
        ]
        self.console.print(
            f"[cyan]短篇扫榜中[/cyan] · 平台 [bold]{platform}[/bold] · "
            f"数据来源 {'榜单样本' if market_data != NO_MARKET_DATA else '内置知识'}"
        )
        try:
            resp = chat_utility(
                self.llm,
                messages=messages, temperature=0.4, enable_thinking=False
            )
            data = parse_llm_json(resp)
            return self._parse(data, platform=platform, sample_date=sample_date)
        except (ValueError, Exception):  # noqa: BLE001 - 解析失败降级为空报告
            self.console.print(
                "[yellow]⚠ 扫榜 JSON 解析失败，返回空报告。[/yellow]"
            )
            return ScanReport(platform=platform, sample_date=sample_date)

    def _parse(self, data: dict[str, Any], platform: str, sample_date: str) -> ScanReport:
        """把 LLM JSON 解析为 ScanReport（字段缺失降级为空）。"""
        return ScanReport(
            platform=str(data.get("platform") or platform),
            sample_date=str(data.get("sample_date") or sample_date),
            signal_strength=str(data.get("signal_strength", "")),
            next_rescan=str(data.get("next_rescan", "")),
            data_source=str(data.get("data_source", "")),
            market_overview=str(data.get("market_overview", "")),
            emotion_rank=[dict(x) for x in (data.get("emotion_rank") or []) if isinstance(x, dict)],
            topic_hotspots=[dict(x) for x in (data.get("topic_hotspots") or []) if isinstance(x, dict)],
            insights=dict(data.get("insights") or {}),
            trend_alerts=[dict(x) for x in (data.get("trend_alerts") or []) if isinstance(x, dict)],
            directions=[dict(x) for x in (data.get("directions") or []) if isinstance(x, dict)],
            one_liner=str(data.get("one_liner", "")),
        )


# ============================================================
# 工作流：短篇拆文
# ============================================================
@workflow("m23_short_analyze")
class M23ShortAnalyzeWorkflow:
    """短篇拆文工作流：深度拆解爆款短篇的故事核/结构/情感线/反转/手法/共鸣"""

    def __init__(
        self,
        llm_client: Gateway | None = None,
        console: Console | None = None,
        skill_dir: Path = SKILL_DIR,
    ) -> None:
        self.llm = llm_client or create_gateway()
        self.console = console or Console()
        self.knowledge = ShortStoryKnowledge(skill_dir)

    def run(
        self,
        input_text: str,
        title: str = "",
        platform: str = "",
        genre: str = "",
        save: bool = False,
        output_dir: Path | None = None,
    ) -> AnalyzeReport:
        """执行短篇拆文。

        Args:
            input_text: 待拆短篇正文。
            title: 作品标题。
            platform: 来源平台（如 知乎盐言/七猫），可选。
            genre: 题材类型（追妻/重生/虐文...），可选。
            save: 是否把报告写入 ``output_dir``（默认 ``<project>/.state/analyze/``）。
            output_dir: 报告保存目录（``save=True`` 时使用）。

        Returns:
            AnalyzeReport
        """
        input_text = (input_text or "").strip()[:MAX_INPUT_CHARS]
        if not input_text:
            raise ValueError("拆文需要提供短篇正文（input_text 为空）")

        knowledge = self.knowledge.analyze_knowledge()
        prompt = pm.get("m23.short_analyze")
        messages = [
            {"role": "system", "content": prompt.system},
            {
                "role": "user",
                "content": prompt.render_user(
                    title=title or "未命名",
                    platform=platform or "未指定",
                    genre=genre or "未指定",
                    knowledge=knowledge,
                    input_text=input_text,
                ),
            },
        ]
        self.console.print(
            f"[cyan]短篇拆文中[/cyan] · 作品 [bold]{title or '未命名'}[/bold] · "
            f"平台 {platform or '未指定'}"
        )
        try:
            resp = chat_utility(
                self.llm,
                messages=messages, temperature=0.3, enable_thinking=False
            )
            data = parse_llm_json(resp)
            report = self._parse(data, title=title, platform=platform)
        except (ValueError, Exception):  # noqa: BLE001 - 解析失败降级为空报告
            self.console.print(
                "[yellow]⚠ 拆文 JSON 解析失败，返回空报告。[/yellow]"
            )
            report = AnalyzeReport(title=title, platform=platform)

        if save:
            self._save(report, output_dir)
        return report

    def _parse(self, data: dict[str, Any], title: str, platform: str) -> AnalyzeReport:
        """把 LLM JSON 解析为 AnalyzeReport（字段缺失降级为空）。"""
        return AnalyzeReport(
            title=str(data.get("title") or title),
            platform=str(data.get("platform") or platform),
            story_core=dict(data.get("story_core") or {}),
            summary=str(data.get("summary", "")),
            pov=str(data.get("pov", "")),
            timeline=str(data.get("timeline", "")),
            structure=[dict(x) for x in (data.get("structure") or []) if isinstance(x, dict)],
            emotion_curve=[dict(x) for x in (data.get("emotion_curve") or []) if isinstance(x, dict)],
            explosion=dict(data.get("explosion") or {}),
            reversal=dict(data.get("reversal") or {}),
            techniques=[dict(x) for x in (data.get("techniques") or []) if isinstance(x, dict)],
            characters=[dict(x) for x in (data.get("characters") or []) if isinstance(x, dict)],
            opening=dict(data.get("opening") or {}),
            ending=dict(data.get("ending") or {}),
            five_dim_score=dict(data.get("five_dim_score") or {}),
            explosion_power=str(data.get("explosion_power", "")),
            topicality=str(data.get("topicality", "")),
            resonance=[dict(x) for x in (data.get("resonance") or []) if isinstance(x, dict)],
            reusable_structures=[
                dict(x) for x in (data.get("reusable_structures") or []) if isinstance(x, dict)
            ],
            writing_actions=str(data.get("writing_actions", "")),
            rhythm_quick=dict(data.get("rhythm_quick") or {}),
            one_liner_eval=str(data.get("one_liner_eval", "")),
        )

    def _save(self, report: AnalyzeReport, output_dir: Path | None) -> Path:
        """报告写入 output_dir（默认 ``.state/analyze/``）。"""
        if output_dir is None:
            output_dir = Path("projects/my-novel") / ".state" / "analyze"
        out_dir = Path(output_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        ts = datetime.now().strftime("%Y%m%d-%H%M%S")
        base = report.title or "untitled"
        path = out_dir / f"analyze-{base}-{ts}.md"
        path.write_text(report.to_markdown(), encoding="utf-8")
        return path
