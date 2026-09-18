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


__all__ = [
    "HOOKS_SECTION",
    "POINTS_SECTION",
    "chapter_contract",
    "chapter_line_pattern",
    "extract_section",
    "has_chapter_level_lines",
    "select_chapter_lines",
]
