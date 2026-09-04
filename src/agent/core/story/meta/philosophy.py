"""「世界模拟」设计哲学文案

对标笔枢 Novelbuilt 的「世界模拟」叙事，但落地为 NovelAgent 自己的声音：
我们不给你一个写手，而给你一台让故事自洽涌现的引擎——
世界先于文字 · 专家而非通才 · 主动权在你。

对外可经 CLI ``philosophy`` 命令、Web 首页引导、以及产品文档复用。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import List


TAGLINE = "对抗设定崩塌与记忆流失"

OPENING = (
    "长篇创作的敌人不是灵感枯竭，而是设定的崩塌与记忆的流失。\n"
    "NovelAgent 的全部工程，都为对抗这两件事而生——\n"
    "它做的不是「AI 写手」，而是「让故事自洽涌现的世界引擎」。"
)

POSITIONING = (
    "同类产品给你一个 AI 写手，NovelAgent 给你一支编制完整的创作团队 + 一台世界状态机。\n"
    "人物处境、势力消长、物品去向、伏笔的埋设与回收，被结构化地记录与推演——\n"
    "下一章的创作，永远基于一个自洽的当下。"
)


@dataclass
class Pillar:
    title: str
    body: str


PILLARS: List[Pillar] = [
    Pillar(
        "世界先于文字",
        "先建立一个可计算、可追溯的世界，再让文字从中生长。设定不是文档，而是一台运转的状态机——"
        "每写完一章，世界就「前进一拍」。",
    ),
    Pillar(
        "专家而非通才",
        "没有万能的「写作 AI」。我们用一支编制完整的专家 Agent 分工协作（世界观架构师、伏笔管家、"
        "连贯性审校、不崩终审……），像一支真正的创作团队。运行 ``roster`` 查看全编制。",
    ),
    Pillar(
        "主动权在你",
        "全自动碰撞（Auto Driver），还是逐章接管（Co-pilot）？无论哪种，方向与拍板始终握在作者手里——"
        "审校与改稿同样有 Agent 辅助，不必逐字盯稿。",
    ),
]

CLOSING = (
    "我们的护城河是三位一体：自动纠偏（七维硬门禁 + 自动回溯）+ 自动完结（主线推进与结局收敛）"
    "+ 成本透明（写前预估、写中 ETA、超预算自动降档）。\n"
    "别的工具给你流程，NovelAgent 给你结果——一本自动不崩、自动收尾、成本透明的完结书。\n"
    "别再追着 AI 的废话改了，去造你的世界。"
)


def render_text() -> str:
    """纯文本版哲学文案（CLI 用）"""
    lines = [TAGLINE, "", OPENING, "", "——", ""]
    lines.append(POSITIONING)
    lines.append("")
    for i, p in enumerate(PILLARS, 1):
        lines.append(f"{i}. {p.title} —— {p.body}")
    lines.append("")
    lines.append(CLOSING)
    return "\n".join(lines)


def render_markdown() -> str:
    """Markdown 版哲学文案（Web / 文档用）"""
    lines = [f"# {TAGLINE}", "", f"> {OPENING}", "", "## 我们做什么", "", POSITIONING, ""]
    lines.append("## 三条设计哲学")
    for i, p in enumerate(PILLARS, 1):
        lines.append(f"{i}. **{p.title}** —— {p.body}")
    lines.append("")
    lines.append("## 护城河")
    lines.append("")
    lines.append(CLOSING)
    return "\n".join(lines)


def get_philosophy() -> dict:
    """供 Web / API 消费的结构化哲学"""
    return {
        "tagline": TAGLINE,
        "opening": OPENING,
        "positioning": POSITIONING,
        "pillars": [{"title": p.title, "body": p.body} for p in PILLARS],
        "closing": CLOSING,
    }
