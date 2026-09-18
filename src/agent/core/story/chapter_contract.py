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
from dataclasses import dataclass

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
# 章级强度档位（D2 唯一真源）—— 2026-09-18
#
# 命题（用户 2026-09-18）：
#   「整体有起伏，单章可以放松……甚至可以出现部分注水的，但是不影响质量」
#   「比如规划描述的是连续五章都是平平淡淡的，那么校验就不会把它当作注水打回」
#
# 缺陷（登记单 ``20260918_节奏起伏与注水章合法化_可行性论证.md``）：
# 质检只有一把「高潮尺」⇒ 规划上本该放松的章被判注水 ⇒ 回退重写 ⇒
# **重写输入不变**（规划没变）⇒ 整窗销毁-重写死循环。
# 根因不是阈值宽严，是**参照系缺失**：判据不知道"本章规划上该是什么档位"。
#
# 本表即该参照系的唯一真源（档位名 / 张力带 / 章内要求 / 是否放松，四处同源）：
#   - 档位名由提示词（``prompts/m3/outline.md``）产出，红线
#     ``tests/test_pace_tier_ssot.py`` 做**成员关系**交叉核对（纪律 #19）；
#   - ``垫片``/``日常`` 是"注水章合法化"的载体：它们不是"质量差的章"，
#     而是"规划上本就该放松的章"——``relaxed`` 是对它的**正名**，不是放水。
#
# ⚠ 豁免的表达形态（设计修正，见方案文档 §11.10）
#   初稿曾写成 ``exempt_rules=("P-11",)``，但全仓搜 ``P-11`` 于 ``*.py`` 为
#   **零命中**——``P-11`` 只存在于提示词文本（``prompts/m5/generate.md`` 第 18 条）。
#   用一个**没有消费者**的字符串标记豁免 ⇒ 豁免不改变任何控制流
#   ⇒ 正是纪律 #7「凡写『供 XX 消费』的注释，必须证明该消费者存在」。
#   ⇒ 改用具**真实消费者**的行为开关 ``relaxed``：由 ``design_brief`` 渲染端消费
#   （写手看到"本章为放松章，允许无强钩子"），M3/M4 接管提示词分档与评委前提。
# ============================================================


@dataclass(frozen=True)
class PaceTier:
    """章级强度档位的唯一定义（渲染与解析共用）。"""

    name: str              # 档位名（= 提示词产出的字面量）
    tension_lo: float      # 张力带下界
    tension_hi: float      # 张力带上界
    label: str             # 渲染给人看的一句话语义
    chapter_req: str       # 章内要求（写手自检口径）
    relaxed: bool = False  # True = 本档位允许放松（无强钩子/无爆点）


#: 四档（**创作语**命名，可直接写进提示词）
PACE_TIERS: tuple[PaceTier, ...] = (
    PaceTier(
        name="高潮",
        tension_lo=9.0, tension_hi=10.0,
        label="摊牌/绝境/反转/胜负",
        chapter_req="必须有一个明确的爆点",
    ),
    PaceTier(
        name="推进",
        tension_lo=6.0, tension_hi=8.0,
        label="冲突升级、压力叠加",
        chapter_req="需有实质进展或代价",
    ),
    PaceTier(
        name="垫片",
        tension_lo=4.0, tension_hi=5.0,
        label="过渡、支线铺陈、信息释放",
        chapter_req="允许无强钩子",
        relaxed=True,
    ),
    PaceTier(
        name="日常",
        tension_lo=2.0, tension_hi=3.0,
        label="缓冲、情感、世界观浸润",
        chapter_req="允许舒展的节奏",
        relaxed=True,
    ),
)

#: 档位名元组（顺序即张力降序；供提示词交叉核对与解析校验）
PACE_TIER_NAMES: tuple[str, ...] = tuple(t.name for t in PACE_TIERS)

#: 档位名 → 定义（渲染端取语义用）
PACE_TIER_BY_NAME: dict[str, PaceTier] = {t.name: t for t in PACE_TIERS}

#: 逐章行里的档位字段名（与其余契约字段同构，见 ``CONTRACT_FIELDS``）
PACE_TIER_FIELD = "档位"

#: 从逐章行抠出档位值：``…｜档位=推进｜…``（容忍三种分隔符与两种竖线）
#: ⚠ 终止符须含中英文句读（``，,、。；;`` 等）：档位常出现在行尾，
#:   其后紧邻句号/逗号（如 ``｜档位=日常。``）——不收会把标点吞进取值。
_PACE_TIER_RE = re.compile(
    rf"[｜|]\s*{PACE_TIER_FIELD}\s*[=:：]\s*([^\s｜|，,、。；;）)：:（(]+)"
)


def parse_pace_tier(chapter_line: str) -> str:
    """从**逐章契约行**里解析强度档位；无标注/未登记值返回空串。

    刻意容忍 ``=``/``:``/``：`` 与全角 ``｜``/半角 ``|``：LLM 对分隔符并不稳定，
    放宽解析不会引入歧义（档位值本身不含标点）。

    Returns:
        登记在 :data:`PACE_TIER_NAMES` 的档位名；否则空串
        （下游按"未标档位"处理 = **沿用现有通用判据**，不放松也不收紧）。
    """
    if not chapter_line:
        return ""
    m = _PACE_TIER_RE.search(chapter_line)
    if not m:
        return ""
    tier = m.group(1).strip()
    return tier if tier in PACE_TIER_BY_NAME else ""


def pace_tier_of(subline_md: str, chapter_num: int) -> str:
    """取**本章**的强度档位（三端共用入口，粒度与 ``select_chapter_lines`` 一致）。

    判据与取舍
    ----------
    ★ **写入端严格、读取端宽容**（本项目一贯口径）：
      - 规划侧只应在「章节钩子设计」的逐章行标档位（该行字段最全，是章级契约主行）；
      - 读取侧先查钩子小节，未命中再查情节点小节（容忍 LLM 标错小节）。

    ⚠ **必须校验"确实是本章逐章行"**：``select_chapter_lines`` 的兜底语义是
      「无逐章行 ⇒ 回退阶段行 ⇒ 再回退整段」，若不做校验，回退出的整段里若
      碰巧含 ``｜档位=``（例如规划者把格式示例写进了阶段文本），就会被误当成
      "本章档位" ⇒ **静默失真**（纪律 #21 同型）。取不到就返回空串。
    """
    try:
        num = int(chapter_num or 0)
    except (TypeError, ValueError):  # noqa: SILENT_DEGRADE reason=expected-skip
        return ""
    if num <= 0:
        return ""
    pat = chapter_line_pattern(num)
    for title in (HOOKS_SECTION, POINTS_SECTION):
        line = select_chapter_lines(subline_md, title, chapter_num=num)
        if not line or not pat.search(line):
            continue  # 非本章逐章行（阶段行/整段兜底）⇒ 此处没有章级档位
        tier = parse_pace_tier(line)
        if tier:
            return tier
    return ""


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
    #: 2026-09-18 M1 新增（D2）：章级强度档位。红
    #: ``tests/test_contract_labels_not_leaked.py::test_prompt_field_names_match_ssot``
    #: 会自动核对"提示词字段名 ⊆ 本元组"，故登记于此即受跨模块核对保护（纪律 #19）。
    PACE_TIER_FIELD,
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
#:
#: ⚠ 关键词集**与 ``CONTRACT_FIELDS`` 的成员关系**由红线
#: ``tests/test_pace_tier_ssot.py::test_fingerprint_keywords_derivable_from_ssot``
#: 机器核对（纪律 #19）。此前它是手写字面量、无人核对 ⇒ 一次改名即双向破裂；
#: 2026-09-18 新增 ``档位`` 时即依赖该核对发现"新字段成了泄漏新入口"。
#:   词根来源：CONTRACT_FIELDS 成员（钩子/爽点/情绪/在场/禁/验收/档位）
#:           + 小节名词根（伏笔任务/情节点）。
CONTRACT_ANNOTATION_RE = re.compile(
    r"^[ \t]*[-*+•]\s*[*_]{0,2}\s*[^\n：:=]{0,10}?"
    r"(?:钩子|爽点|情绪|在场|禁|验收|伏笔任务|情节点|档位)"
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
    "PACE_TIER_BY_NAME",
    "PACE_TIER_FIELD",
    "PACE_TIER_NAMES",
    "PACE_TIERS",
    "POINTS_SECTION",
    "PaceTier",
    "chapter_contract",
    "chapter_line_pattern",
    "extract_section",
    "find_contract_annotations",
    "has_chapter_level_lines",
    "is_contract_annotation_line",
    "pace_tier_of",
    "parse_pace_tier",
    "select_chapter_lines",
    "strip_contract_annotations",
]
