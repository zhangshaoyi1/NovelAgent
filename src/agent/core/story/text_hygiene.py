"""core 层正文净化纯函数（2026-09-14 框架化：一条定义、全路径消费）

背景（灵荒薪传 ch001 双标题 + 「【下一章预告：…】」泄漏复盘）：
- 写章路径（agentic/m5）经 _finalize_chapter_text 走 L2 硬污染清理；
- 但 rewrite / paragraph_rewriter 走各自私有落盘，绕过净化；
- guardrails（core 层）因 R6 分层红线不能 import workflows 的 m5_text_hygiene，
  无法注册 L2 规则——「发现一个问题、解决一类问题」的正确姿势是：
  **把纯文本净化/扫描逻辑下沉 core 层单一定义**，guardrails 与所有写改路径统一消费。

本模块只含纯函数（无 agent 包内依赖、无 LLM、无 IO 副作用），
workflows 的 m5_text_hygiene 从其 re-export 保持兼容；guardrails 直接 import。
"""

from __future__ import annotations

import re


def strip_frontmatter(text: str) -> str:
    """去掉可能存在的 YAML frontmatter（与 m5_text_hygiene._strip_frontmatter 同实现）"""
    if text.startswith("---"):
        end = text.find("\n---", 3)
        if end != -1:
            rest = text[end + 4:]
            return rest.lstrip("\n")
    return text


# ============================================================
# L2 生成残留硬污染：确定性扫描 + 落盘前兜底清理
# （2026-09-13 定义于 workflows/m5_text_hygiene，2026-09-14 下沉 core 供全路径消费）
# 灵荒薪传 ch001 实证：标题重复两遍 + 正文混入「【下一章预告：…】」
# 指令文本——LLM 质检（钩子/爽点等情感类规则）对这类低级硬伤无感，
# 只能靠确定性规则拦截。两类用途：
#   scan_hard_pollutions  —— 门禁/护栏硬关卡（命中即打回或拒绝落盘）
#   clean_hard_pollutions —— 落盘前高置信清理（删指令行/去重标题行/清占位符）
# ============================================================

# 1) 标题重复：# 第 N 章 · xxx 出现 ≥2 次（行首锚定，匹配整行——去重时整行删除，
#    不残留标题名；容忍空格/分隔符差异）
_HARD_TITLE_RE = re.compile(
    r"^[ \t]*#{1,6}\s*第\s*[0-9一二三四五六七八九十百千]+\s*章\s*[·:：,，、\-–—\s][^\n]*$",
    re.M,
)

# 2) AI 指令泄漏（独立行）：【下一章预告：…】等【】包裹的导演/预告指令
_HARD_DIRECTIVE_RE = re.compile(
    r"^\s*【(?:下一章|本章|本章节|写作|作者|系统|大纲|细纲|预告|伏笔|爽点|节奏|要求|批注)[^】]{0,80}】",
    re.M,
)

# 3) AI 指令泄漏（行内/句末混入）：如句尾贴着【下一章预告…】
_HARD_DIRECTIVE_INLINE_RE = re.compile(
    r"【(?:下一章预告|本章预告|本章重点|写作要求|作者按|系统提示)[^】]{0,60}】"
)

# 4) 占位符：裸问号占位、{placeholder}、TODO/FIXME、待补充等
_HARD_PLACEHOLDER_RE = re.compile(
    r"\s\?\s|\{[a-z_]{2,}\}|TODO|FIXME|待补充|此处补|此处插入",
    re.I,
)

# 5) AI 承接词（话锋转折残留，生成模型自述痕迹）
_HARD_BRIDGE_PHRASES = ("话说回来", "你别说", "就这么着", "说起来", "总而言之")


def scan_hard_pollutions(text: str) -> list[str]:
    """确定性扫描生成残留硬污染，返回人类可读命中清单（空列表 = 干净）。

    供 guardrails 硬关卡 / 写章门禁 / rewrite 校验统一消费（不依赖 LLM 自觉）。
    """
    if not text:
        return []
    body = strip_frontmatter(text)
    hits: list[str] = []

    titles = _HARD_TITLE_RE.findall(body)
    if len(titles) >= 2:
        hits.append(f"标题重复：检测到 {len(titles)} 个章节标题行（正文应只保留落盘统一标题）")

    directives = _HARD_DIRECTIVE_RE.findall(body)
    if directives:
        hits.append(
            f"AI 指令泄漏：{len(directives)} 处独立行【…】指令（如「{directives[0].strip()[:30]}」）"
        )
    inline = _HARD_DIRECTIVE_INLINE_RE.findall(body)
    if inline:
        hits.append(f"AI 指令泄漏（行内）：{len(inline)} 处（如「{inline[0][:30]}」）")

    ph = _HARD_PLACEHOLDER_RE.findall(body)
    if ph:
        hits.append(f"占位符残留：{len(ph)} 处（裸问号/TODO/待补充等）")

    bridges = [p for p in _HARD_BRIDGE_PHRASES if p in body]
    if bridges:
        hits.append("AI 承接词残留：" + "、".join(bridges))

    return hits


def clean_hard_pollutions(text: str) -> tuple[str, list[str]]:
    """落盘前高置信清理：删【…】指令（独立行/行内）、去重标题行、清占位符。

    Returns:
        (清理后文本, 清理轨迹列表；无命中轨迹为空)。
    只做高置信删除/去重，绝不改动叙事正文；命中项与 scan_hard_pollutions 同源。
    """
    out = text
    traced: list[str] = []

    def _drop(m: re.Match) -> str:
        traced.append(f"删指令：{m.group(0).strip()[:30]}")
        return ""

    out = _HARD_DIRECTIVE_RE.sub(_drop, out)
    out = _HARD_DIRECTIVE_INLINE_RE.sub(_drop, out)

    seen_title = False

    def _dedup_title(m: re.Match) -> str:
        nonlocal seen_title
        if not seen_title:
            seen_title = True
            return m.group(0)
        traced.append(f"删重复标题：{m.group(0).strip()[:30]}")
        return ""

    out = _HARD_TITLE_RE.sub(_dedup_title, out)
    out = _HARD_PLACEHOLDER_RE.sub("", out)
    return out, traced


def strip_leading_headings(text: str) -> str:
    """剥掉正文开头的 markdown 标题行（LLM 自报标题）——防与落盘统一标题重复。

    rewrite/_save 拼「# 第 N 章 · 标题」前必须调用（灵荒薪传 ch001 双标题根因：
    旧逻辑把带自报标题的 new_text 直接拼进 body，标题重复两遍）。
    仅处理连续的前导标题行 + 空行，正文不动。
    """
    lines = text.split("\n")
    while lines and (not lines[0].strip() or lines[0].lstrip().startswith("#")):
        lines.pop(0)
    return "\n".join(lines).strip()
