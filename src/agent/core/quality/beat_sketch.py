"""章节桥段骨架提取（防重复桥段注入用，2026-09-10 回滚率削减·P0）

背景
----
五灵破归档 ch151-155 连续重写 3 遍后，Writer 用最安全的"教学→失败几次→
成功→感动"模板连灌 3 章，批末体检 coherence 85→30、readability 65→35。
根因之一是重写时 Writer 看不到"上一版/相邻章已经写过什么桥段"。

设计
----
确定性启发式提取，零 LLM 成本：逐段取段首短句，构成"已用桥段序列"。
注入重写 prompt 的【已用桥段禁用清单】，让 Writer 具体知道不能重复什么，
而不是只知道"要更好"这种抽象要求。

依赖方向：仅标准库（core 层红线）。
"""

from __future__ import annotations

import re

_CUT_RE = re.compile(r"[。！？；…]")
# 可跳过的非叙事行（frontmatter 之外仍可能混入的标题/分隔）
_SKIP_RE = re.compile(r"^\s*(#|---|===|\*\*|【第|第\s*\d+\s*章)")


def _strip_frontmatter(text: str) -> str:
    if text.startswith("---"):
        end = text.find("\n---", 3)
        if end != -1:
            return text[end + 4:]
    return text


def _first_fragment(paragraph: str, max_chars: int) -> str:
    """取段落首个句子的句内片段（到第一个句末标点，再截 max_chars）。"""
    paragraph = paragraph.strip()
    m = _CUT_RE.search(paragraph)
    frag = paragraph[: m.end()] if m else paragraph
    frag = frag.strip()
    return frag[:max_chars]


def extract_beat_sketch(
    text: str, max_beats: int = 14, max_chars: int = 36
) -> list[str]:
    """从章节正文提取桥段骨架（每段一个段首短句，按出现顺序）。

    - 自动剥离 YAML frontmatter 与标题行；
    - 空行分段；若无空行（单段长文）退化为按句切分；
    - 结果按原文顺序、最多 max_beats 条。
    """
    if not text:
        return []
    body = _strip_frontmatter(text)
    paragraphs: list[str] = [p.strip() for p in re.split(r"\n\s*\n", body) if p.strip()]
    if len(paragraphs) <= 1:
        # 单段长文：按句切分为伪段落
        sentences = _CUT_RE.split(body)
        paragraphs = [s.strip() for s in sentences if s.strip()]
    beats: list[str] = []
    for p in paragraphs:
        if len(beats) >= max_beats:
            break
        first_line = p.split("\n", 1)[0].strip()
        if not first_line or _SKIP_RE.match(first_line):
            continue
        frag = _first_fragment(first_line, max_chars)
        if len(frag) < 6:  # 过短片段（标点残句）无信息量
            continue
        beats.append(frag)
    return beats


def render_beats(beats: list[str], indent: str = "  ") -> str:
    """把骨架列表渲染为清单文本（空列表返回空串）。"""
    return "\n".join(f"{indent}- {b}" for b in beats)
