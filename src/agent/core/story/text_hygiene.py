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
from dataclasses import dataclass

from agent.core.story.chapter_contract import (
    find_contract_annotations,
    strip_contract_annotations,
)


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

# ============================================================
# 5) AI 腔 / 承接词：**唯一定义（SSOT）** + 强度与替换手段配对
# （2026-09-18：修 ch4「AI 承接词残留：说起来」——判而不可修）
#
# 事故：`说起来` 在本模块判 **blocking**，但在
# `core/quality/text_hygiene._FILLER_PHRASES` 只是 **warning**；且
# `clean_hard_pollutions` 只清指令/标题/占位符、**不清承接词**，L1 替换表
# 也不含它 ⇒ 「判得最死的一侧没有修复手段」⇒ 只能整章重写
# （每轮再付一次全章生成 + 全章审稿）。同族根因：R1「动作强度 ↔ 可修复性零对账」。
#
# 收口口径（本表即唯一真源，全部消费点由它派生，禁止再写第二份字面量）：
#   - level="blocking" **必须**要么给 replacement，要么显式 unrepairable=True
#     + reason —— 由 `audit_phrase_ledger()` 强制、红线断言恒空，
#     以此**永久禁止「判而不可修」再生**（纪律 #17）。
#   - category="bridge"（承接词）**quote_sensitive=True**：这些词在对话里是
#     合法口语（人物说「你别说」没问题），只有**叙述层**出现才是 AI 痕迹
#     ⇒ 检测与替换都在**同一叙述层投影**上进行（判据与修复同投影，
#     否则「报得出但修不掉」或「修了仍报」，两条都把人推回整章重写）。
#   - category="ai_tone"（组合式 AI 腔）任何位置出现都生硬 ⇒ 全局替换。
# ============================================================


@dataclass(frozen=True)
class PhraseSpec:
    """AI 腔 / 承接词条目：强度、替换手段、适用层的唯一定义。"""

    phrase: str
    level: str                    # "blocking"（门禁拦）| "warning"（体检提示）
    replacement: str = ""         # 确定性替换目标；空 = 无替换（须 unrepairable）
    category: str = "ai_tone"     # "ai_tone"（组合式 AI 腔）| "bridge"（承接词）
    quote_sensitive: bool = False # True = 仅叙述层命中（引号内为人物口吻，不算）
    unrepairable: bool = False    # 无 replacement 时的显式声明（元规则强制）
    reason: str = ""              # unrepairable=True 时必须给出理由


HYGIENE_PHRASES: tuple[PhraseSpec, ...] = (
    # --- 组合式 AI 腔心理描写：任何位置出现都是套路腔调（全局替换）---
    PhraseSpec("喃喃自语", "blocking", "低声说"),
    PhraseSpec("心中一动", "blocking", "忽然想到"),
    PhraseSpec("若有所思", "blocking", "沉默片刻"),
    PhraseSpec("眸光微动", "blocking", "目光一变"),
    PhraseSpec("眸子微缩", "blocking", "眯起眼"),
    PhraseSpec("嘴角微微上扬", "blocking", "笑了笑"),
    PhraseSpec("心头一颤", "blocking", "心里一沉"),
    # --- 承接词（话锋转折残留）：叙述层 AI 痕迹，对话里是合法口语 ---
    PhraseSpec("话说回来", "blocking", "不过", "bridge", quote_sensitive=True),
    PhraseSpec("你别说", "blocking", "别说", "bridge", quote_sensitive=True),
    PhraseSpec("就这么着", "blocking", "这么着", "bridge", quote_sensitive=True),
    PhraseSpec("说起来", "blocking", "这么一想", "bridge", quote_sensitive=True),
    PhraseSpec("总而言之", "blocking", "说到底", "bridge", quote_sensitive=True),
)


def _specs(
    category: str = "",
    level: str = "",
    require_replacement: bool = False,
) -> tuple[PhraseSpec, ...]:
    """按类/强度/是否带替换筛选 SSOT（派生消费点用，勿另立字面量）。"""
    out = HYGIENE_PHRASES
    if category:
        out = tuple(s for s in out if s.category == category)
    if level:
        out = tuple(s for s in out if s.level == level)
    if require_replacement:
        out = tuple(s for s in out if s.replacement)
    return out


#: 组合式 AI 腔 → 替词（L1 硬替换表；仅 ai_tone，避免误伤对话口吻）
AI_TONE_REPLACEMENTS: dict[str, str] = {
    s.phrase: s.replacement for s in _specs(category="ai_tone", require_replacement=True)
}

#: 承接词 → 弱过渡替词（仅叙述层替换；弱过渡而非删除，保留口语节奏）
BRIDGE_REPLACEMENTS: dict[str, str] = {
    s.phrase: s.replacement for s in _specs(category="bridge", require_replacement=True)
}

#: 承接词清单（叙述层命中即 blocking；doctor 侧留一份兜底清单语义）
_HARD_BRIDGE_PHRASES: tuple[str, ...] = tuple(
    s.phrase for s in _specs(category="bridge", level="blocking")
)


def audit_phrase_ledger() -> list[str]:
    """元规则自检：blocking 条目「有替换」或「显式 unrepairable」，二者必居其一。

    Returns:
        违规说明清单（空 = 合规）。红线测试断言恒空。
    存在意义：把「判据强度必须与处置手段配对」从**口头纪律**变成**可执行判据**
    ——  纪律 #17 的可验证形态。
    """
    bad: list[str] = []
    for s in HYGIENE_PHRASES:
        if s.level != "blocking" or s.replacement:
            continue
        if not s.unrepairable or not s.reason.strip():
            bad.append(
                f"{s.phrase}：blocking 级但既无 replacement 也未标 unrepairable+reason"
                "（判而不可修 ⇒ 只能整章重写）"
            )
    return bad


# ---- 叙述层投影（引号感知：判据侧与修复侧共用同一投影）----
# ⚠ 两侧必须同投影：判据在叙述层、修复若全局做，会误改人物口语（把台词里的
#   「你别说」也替换掉）；判据全局、修复只在叙述层做，则永远修不干净 ⇒ 死循环。
_QUOTE_OPEN = {"“": "”", "「": "」", "『": "』", "‘": "’"}
_QUOTE_CLOSE = {v: k for k, v in _QUOTE_OPEN.items()}

#: 投影占位字符（与原文字符**等长**替换 ⇒ 偏移可直接映射回原文）
_QUOTE_FILL = "\x00"


def narrative_projection(text: str) -> str:
    """返回与 ``text`` **等长**的「叙述层投影」：成对引号内的字符变为 ``\\x00``。

    支持 ``“”``/``「」``/``『』``/``‘’`` 四种配对，栈式扫描，**换行符原样保留**
    （等长 ⇒ 偏移可直接映射回原文）。
    取向（保守）：未闭合的开引号**在行尾复位**（只屏蔽本行剩余部分）。
    中文小说对话以段为单位、不会跨段不闭合；若因笔误漏掉一个 ``”`` 就让后半章
    全部退出检测，检测就形同虚设（假阴性比误替换更糟：AI 腔会原样落盘）。
    """
    if not text:
        return text
    chars = list(text)
    stack: list[str] = []
    for i, ch in enumerate(chars):
        if ch == "\n":
            stack.clear()  # 行尾复位：未闭合引号只影响本行
            continue
        if ch in _QUOTE_OPEN:
            stack.append(_QUOTE_OPEN[ch])
            chars[i] = _QUOTE_FILL
        elif ch in _QUOTE_CLOSE and stack and stack[-1] == ch:
            stack.pop()
            chars[i] = _QUOTE_FILL
        elif stack:
            chars[i] = _QUOTE_FILL
    return "".join(chars)


def replace_bridge_words(text: str) -> tuple[str, list[str]]:
    """叙述层承接词确定性替换（引号内人物口吻不动）。

    Returns:
        (替换后文本, 轨迹列表，如 ``["说起来×2→这么一想"]``；未命中为空)。
    与 ``_HARD_BRIDGE_PHRASES`` 同源同投影 —— 保证「扫得到的就修得掉」。
    """
    out = text
    traced: list[str] = []
    for phrase, rep in sorted(BRIDGE_REPLACEMENTS.items(), key=lambda kv: -len(kv[0])):
        proj = narrative_projection(out)  # 每轮重算：替换会改变长度
        idx = [m.start() for m in re.finditer(re.escape(phrase), proj)]
        if not idx:
            continue
        for i in reversed(idx):
            out = out[:i] + rep + out[i + len(phrase):]
        traced.append(f"{phrase}×{len(idx)}→{rep}")
    return out, traced


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

    annotations = find_contract_annotations(body)
    if annotations:
        hits.append(
            f"契约批注泄漏：{len(annotations)} 行（如「{annotations[0][:30]}」）"
        )

    bridges = [p for p in _HARD_BRIDGE_PHRASES if p in narrative_projection(body)]
    if bridges:
        hits.append("AI 承接词残留：" + "、".join(bridges))

    return hits


def clean_hard_pollutions(text: str) -> tuple[str, list[str]]:
    """落盘前确定性清理：删【…】指令（独立行/行内）、去重标题行、清占位符、
    替换**叙述层**承接词。

    Returns:
        (清理后文本, 清理轨迹列表；无命中轨迹为空)。
    只做高置信删除/去重 + 叙述层承接词替换（弱过渡替词，不改情节/人物/设定）；
    与 scan_hard_pollutions 同源同投影（「扫得到的就修得掉」）。
    """
    out = text
    traced: list[str] = []

    # 2026-09-18：先删**契约批注行**（细纲字段名被写手照抄成批注块，
    # ch003.md:254 实测）——与 guardrails 的检测同源，判得出就删得掉。
    out, _anno_traced = strip_contract_annotations(out)
    traced.extend(_anno_traced)

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

    # 2026-09-18：承接词由「只报不修（blocking）」改为「确定性替换」——
    # 此前 ch4 因此整章重写仍未过。只在叙述层动，引号内人物口吻保留。
    out, _bridge_traced = replace_bridge_words(out)
    traced.extend(_bridge_traced)

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
