"""章级契约切分 —— 写手/评委取「本章契约」的唯一实现（2026-09-18）。

背景（《灵荒薪传》09-18 实证，登记单
``项目文档/优化/20260918_章级意图缺位_细纲按阶段供给.md``）：
写手与评委的「本章意图」槽位**早已存在**（``m5_context`` 的 chapter_hooks /
plot_points、``design_brief`` 的 chapter_intent、``reader_appeal`` 的
chapter_intent），但供给端（M3 细纲提示词）要求规划者「**按压力阶段**」给
（"每阶段一行" / "每阶段 3-6 个"）⇒：

- 单条支线覆盖 180 章、只有 4 行阶段模板 ⇒ **写手拿到的"本章意图"可套用到任意
  一章**，章级内容只能自行编造 ⇒ 注水/重复/漂移 ⇒ 评委按通用叙事尺判不合格
  ⇒ 回退重写时**输入一字不变** ⇒ 整窗销毁-重写死循环（1h51m / 3.1M token / 净 0 章）。
- 且各消费者粒度不一致：有的按章匹配（hooks），有的整段注入后按 400-500 字截断
  （design_brief / reader_appeal）⇒ 阶段级文本被截断后连"阶段模板"都只剩前几行。

本模块把「按本章切分」收敛为**唯一实现**，供三端共用（禁止各端自行写正则——
粒度漂移即缺陷，参见纪律 #8「凡设计产出必须逐项核对三端可达」）：

- 有逐章行（``第N章：…``）⇒ **只返回本章行**（token 省、指向唯一、可判定）；
- 无逐章行 ⇒ 回退压力阶段行（兼容阶段级历史数据）；
- 两者皆无 ⇒ 整段返回（兼容更早的无标注数据）。

配套契约：``prompts/m3/outline.md`` 要求规划者按 ``第N章：…`` 逐章给开篇窗口，
其格式与本模块的匹配式由红线 ``tests/test_chapter_contract_granularity.py``
钉死（**提示词的语言锚与消费者的解析式是同一件事的两半**，改其一必须同时改另一半）。
"""

from __future__ import annotations

import re

#: 细纲中承载章级契约的两个小节标题（与 ``templates/subline.md.j2`` 一致）
HOOKS_SECTION = "章节钩子设计"
POINTS_SECTION = "情节点序列"


def extract_section(content: str, *titles: str) -> str:
    """取首个命中的 ``## <title>`` 小节正文（到下一个 ``##`` 或文末为止）。"""
    for title in titles:
        m = re.search(rf"^##\s*{re.escape(title)}\s*\n", content, re.M)
        if not m:
            continue
        rest = content[m.end():]
        nxt = re.search(r"^##\s", rest, re.M)
        body = (rest[: nxt.start()] if nxt else rest).strip()
        if body:
            return body
    return ""


def chapter_line_pattern(chapter_num: int) -> re.Pattern[str]:
    """逐章行匹配式（**唯一真源**）：``第7章：…`` / ``7章：…``。

    两种写法都**必须带冒号**且位于行（或分段）首——这是刻意的：
    章级契约行里常出现对别章的**交叉引用**（如"……留至第5章"），
    若允许无冒号的裸章号，第 4 章那一行会被误判成第 5 章的契约
    （2026-09-18 自查实证：第5章契约里混入了第4章尾部）。

    规划提示词（``prompts/m3/outline.md``）要求逐章行的书写格式必须能被本式命中。
    """
    n = int(chapter_num)
    return re.compile(rf"^\s*(?:第\s*{n}\s*章|{n}\s*章)\s*[：:]")


#: 章标记（带冒号）——既是切分边界，也是「这一行是不是章级契约」的判据
_CHAPTER_MARKER = re.compile(r"第\s*\d+\s*章\s*[：:]")


def _chapter_segments(line: str) -> list[str]:
    """把一行切成逐章段（LLM 有时用 ``；`` 而非换行分隔多章）。

    只有**带冒号的章标记**才算边界；无标记的行原样返回（由匹配式决定取舍）。
    """
    marks = list(_CHAPTER_MARKER.finditer(line))
    if len(marks) <= 1:
        return [line]
    segs: list[str] = []
    for i, m in enumerate(marks):
        end = marks[i + 1].start() if i + 1 < len(marks) else len(line)
        segs.append(line[m.start():end].strip(" ；;，,。"))
    return segs


def select_chapter_lines(
    content: str,
    title: str,
    *,
    chapter_num: int = 0,
    pressure_stage: str = "",
) -> str:
    """从细纲小节中切出「本章」契约行。

    优先级：逐章段 > 压力阶段行 > 整段。
    """
    section = extract_section(content, title)
    if not section:
        return ""
    lines = [ln.strip() for ln in section.splitlines() if ln.strip()]
    if int(chapter_num or 0) > 0:
        pat = chapter_line_pattern(chapter_num)
        matched = [
            seg
            for ln in lines
            for seg in _chapter_segments(ln)
            if pat.search(seg)
        ]
        if matched:
            return "\n".join(matched)
    if pressure_stage:
        matched = [
            ln for ln in lines if f"{pressure_stage}阶段" in ln or pressure_stage in ln
        ]
        if matched:
            return "\n".join(matched)
    return section


def chapter_contract(subline_md: str, *, chapter_num: int, pressure_stage: str = "") -> tuple[str, str]:
    """返回 (本章钩子设计, 本章情节点序列)——三端共用的同一份契约。"""
    return (
        select_chapter_lines(
            subline_md, HOOKS_SECTION,
            chapter_num=chapter_num, pressure_stage=pressure_stage,
        ),
        select_chapter_lines(
            subline_md, POINTS_SECTION,
            chapter_num=chapter_num, pressure_stage=pressure_stage,
        ),
    )


def has_chapter_level_lines(subline_md: str) -> bool:
    """细纲是否含**逐章**契约行（供体检/审计区分「章级供给」与「阶段模板」）。"""
    for title in (HOOKS_SECTION, POINTS_SECTION):
        section = extract_section(subline_md, title)
        if section and re.search(r"第\s*\d+\s*章", section):
            return True
    return False


# ============================================================
# 契约字段名（唯一真源）—— 2026-09-18
#
# 事故：prompt v4 把契约字段名定为「章首钩子/章尾钩子/爽点/目标情绪/在场/禁/
# 验收」，而 ``guardrails._META_LEAK_RE`` 恰把「章末悬念」列为 **error 级**禁词
# ⇒ **同一批词，供给侧当"字段名"用、检测侧当"污染词"拦**，两边各写一份字面量、
# 无人核对。实测泄漏（``chapters/ch003.md:254``）：
#
#     - *钩子：章末悬念从笼统的「外域词汇」收敛到更具体的……*
#
# ⇒ 被既有「写作元指令泄漏」判 blocking ⇒ ch3 未过门禁（重写后仍失败）。
# 这是「提示词语言锚 ↔ 消费者解析式是同一件事的两半」的**反向破裂**：
# 上次修的是"解析式追不上语言锚"，这次是"检测词表没跟上语言锚的改名"。
#
# 因此字段名清单落在**契约唯一真源**本模块：检测侧（guardrails）与清理侧
# （core/story/text_hygiene）都从这里派生；提示词里的字段名由红线与
# ``CONTRACT_FIELDS`` 做机器交叉核对 —— 禁止再出现第二份字面量清单。
# ============================================================

#: 契约字段名（写手收到的指令字段；**也是写手不得抄进正文的标签集合**）
CONTRACT_FIELDS: tuple[str, ...] = (
    "章首钩子",
    "章尾钩子",
    "爽点",
    "目标情绪",
    "在场",
    "禁",
    "验收",
)

#: 正文中不得出现的契约/批注标签（**只收正文里绝不自然出现的元词**）。
#: ⚠ 短词/常用词（爽点、在场、禁、验收）**有意不收**——正文里「验收了药材」
#:   「他站在场边」是正常用语，收进词表会大量误杀；它们只由
#:   ``CONTRACT_ANNOTATION_RE`` 的**类级指纹**（行首列表标记 + 冒号）兜住。
CONTRACT_LEAK_LABELS: tuple[str, ...] = (
    "章首钩子",
    "章尾钩子",
    "章末悬念",
    "章节钩子设计",
    "情节点序列",
    "目标情绪",
    "章首钩",
    "章尾钩",
)

#: 类级指纹（与措辞无关）：行首列表标记 + 契约语义标签 + 冒号/等号。
#: 词表只覆盖已知措辞；写手把字段名改写成「钩子：…」「情绪：…」时靠它兜住。
#: ⚠ 只认**行首列表标记**：小说正文用 ``-``/``*`` 起行的场景只有列表，
#:   而规划批注恰恰都是列表形态（实测泄漏行即 ``- *钩子：…*``）。
CONTRACT_ANNOTATION_RE = re.compile(
    r"^[ \t]*[-*+•]\s*[*_]{0,2}\s*[^\n：:=]{0,10}?"
    r"(?:钩子|爽点|情绪|在场|禁|验收|伏笔任务|情节点)"
    r"[^\n：:=]{0,8}[：:=]",
    re.M,
)


def find_contract_annotations(text: str) -> list[str]:
    """返回正文中的契约批注行（人类可读；空列表 = 干净）。"""
    if not text:
        return []
    return [m.group(0).strip() for m in CONTRACT_ANNOTATION_RE.finditer(text)]


def is_contract_annotation_line(line: str) -> bool:
    """整行是否为「规划批注行」：行首列表标记 + （契约标签词 或 批注指纹）。"""
    stripped = line.strip()
    if not stripped or not re.match(r"^[-*+•]\s", stripped):
        return False
    if CONTRACT_ANNOTATION_RE.match(line):
        return True
    return any(label in stripped for label in CONTRACT_LEAK_LABELS)


def strip_contract_annotations(text: str) -> tuple[str, list[str]]:
    """删除契约批注行（整行删）——把「判 blocking 但只能整章重写」变成可确定性修复。

    Returns:
        (清理后文本, 轨迹列表)。只删**整行**（列表标记起行），不碰叙事正文；
        与 ``find_contract_annotations``/``is_contract_annotation_line`` 同源
        （判据与修复同投影，否则又成「报得出、修不掉」⇒ 整章重写）。
    跳过 YAML front-matter 区：其 ``- field: …`` 列表是证据链元数据，不是规划批注。
    """
    if not text:
        return text, []
    lines = text.split("\n")
    fm_end = 0
    if lines and lines[0].strip() == "---":
        for i in range(1, len(lines)):
            if lines[i].strip() == "---":
                fm_end = i + 1
                break
    kept: list[str] = []
    traced: list[str] = []
    for idx, line in enumerate(lines):
        if idx < fm_end:
            kept.append(line)
            continue
        if is_contract_annotation_line(line):
            traced.append(f"删契约批注：{line.strip()[:40]}")
            continue
        kept.append(line)
    return "\n".join(kept), traced


__all__ = [
    "CONTRACT_ANNOTATION_RE",
    "CONTRACT_FIELDS",
    "CONTRACT_LEAK_LABELS",
    "HOOKS_SECTION",
    "POINTS_SECTION",
    "chapter_contract",
    "chapter_line_pattern",
    "extract_section",
    "find_contract_annotations",
    "has_chapter_level_lines",
    "is_contract_annotation_line",
    "select_chapter_lines",
    "strip_contract_annotations",
]
