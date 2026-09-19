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
#: 细纲中承载**章级强度档位**的独立小节（v6 新增；与模板一致）。
#: ★ 为什么独立成节：档位是评委/写手共用的**参照系**，混在长钩子行里
#:   实测会被 LLM 整段丢掉（2026-09-18：真实项目 15 条章级行、档位 0 个）。
#:   独立小节使"丢档位"与"丢钩子"成为互不掩盖的两个失败。
TIERS_SECTION = "章节强度档位"


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

#: 「最近前文」回退值的**统一标注前缀**（唯一真源，2026-09-18 N1 修复）。
#:
#: ★ 为什么要一个显式前缀而不是"各端自己判断"：
#:   本前缀同时承担三个职责，三处**必须同源**（否则又是一份手写清单，纪律 #19）：
#:     ① 写给**人/LLM 看**的语义声明（写手知道这不是本章标准，别照抄）；
#:     ② ``pace_tier_of`` 的**判据**（带此前缀 ⇒ 不得借它的档位当本章档位）；
#:     ③ 红线 ``tests/test_n1_nearest_prior.py`` 的锚点。
#:   改动本常量必须同步 ②③（语言锚与解析式是同一件事的两半，纪律 #3）。
PRIOR_CONTRACT_PREFIX = "⚠ 本章（第{n}章）尚无逐章契约；以下为**最近前文**的第{p}章契约"

#: 前缀的稳定片段（供不依赖具体章号的判据使用）
_PRIOR_CONTRACT_MARK = "尚无逐章契约"

#: 渲染端给「非本章」意图块用的**标签后缀**（唯一真源，2026-09-18 N1 修复）。
#:
#: ★ 为什么连标签文案也要同源：`design_brief._intent_label` 的标签与
#:   :data:`_PRIOR_CONTRACT_MARK` 若各写一份，就是纪律 #19 的「手写清单」——
#:   一次改名即双向破裂（红线 ``test_n1_nearest_prior`` 的
#:   ``test_consumers_do_not_hardcode_the_string`` 钉住这一点）。
NON_CURRENT_LABEL_SUFFIX = "（非本章，仅供参考——本章尚无逐章契约）"


def _is_marked_as_non_current(text: str) -> bool:
    """该文本是否被标注为「非本章契约」（最近前文 / 阶段模板）。

    消费者据此**拒绝把弱证据当强证据**——典型误用是把最近前文的档位
    当作本章档位（参照系错位 ⇒ 评委按错误的尺判分）。
    """
    return _PRIOR_CONTRACT_MARK in text


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

    优先级：本章逐章段 > **最近前文**逐章段 > 压力阶段行 > 整段。

    ★ 2026-09-18 校正（N1，真实项目跑闸发现）：此前第 2 档是「压力阶段行」，
    但它**依赖调用方猜对阶段词**——实测四重缺陷（详见 ``_select_*`` 注释与
    方案文档 §13）：三个消费者里只有一个传 ``pressure_stage``；即使传对，
    命中的阶段行（如「铺垫阶段（第6章起）」）**没有章号、不随章推进**；
    两小节的阶段行结构不对称（钩子小节没有）⇒ 钩子仍回退整段；
    而**猜错阶段词反而更贵**（命中 0 行 ⇒ 回退整段 1663 字）。
    ⇒ 改为**结构判据**：取「章号 ≤ 请求章」里最大的那个逐章段作为
    **最近前文**，并在返回值上显式标注它不是本章（防止写手/评委误当本章标准）。

    ⚠ 兼容性（纪律 #4）：老数据（无任何逐章行）仍走「阶段行 → 整段」原路径，
    逐字不变。
    """
    section = extract_section(content, title)
    if not section:
        return ""
    lines = [ln.strip() for ln in section.splitlines() if ln.strip()]
    num = int(chapter_num or 0)
    if num > 0:
        pat = chapter_line_pattern(num)
        # ---- 档 1：本章逐章段（精确命中，唯一权威） ----
        matched = [seg for ln in lines for seg in _chapter_segments(ln) if pat.search(seg)]
        if matched:
            return "\n".join(matched)
        # ---- 档 2：最近前文逐章段（弱证据，必须显式标注） ----
        prev = _nearest_prior_chapter_line(lines, num)
        if prev is not None:
            prev_num, prev_text = prev
            return PRIOR_CONTRACT_PREFIX.format(n=num, p=prev_num) + f"：\n{prev_text}"
    # ---- 档 3/4：阶段行 → 整段（老数据与阶段级历史路径，保持原样） ----
    if pressure_stage:
        stage_matched = [
            ln for ln in lines if f"{pressure_stage}阶段" in ln or pressure_stage in ln
        ]
        if stage_matched:
            return "\n".join(stage_matched)
    return section


def _nearest_prior_chapter_line(lines: list[str], chapter_num: int) -> tuple[int, str] | None:
    """取「章号 ≤ chapter_num」里**章号最大**的那条逐章段（最近前文）。

    用途（N1 修复）：本章没有逐章契约时，给写手/评委**最近的一条**具体事件，
    而不是整段（1663 字里只有约 60 字相关）或一串不随章推进的阶段模板。

    ★ 为什么必须是**最近前文**而不是**最近后文**：
      后文事件尚未发生，注入给写手会**剧透并诱导提前写掉**（更严重的缺陷）。
      前文事件是"刚发生过、需要衔接"的信息，语义安全。

    Returns:
        ``(章号, 该段文本)``；无任何 ``第N章：`` 段时返回 ``None``。
    """
    best_num = 0
    best_text = ""
    for ln in lines:
        for seg in _chapter_segments(ln):
            m = _CHAPTER_MARKER.match(seg)
            if not m:
                continue
            num = int(re.sub(r"\D", "", m.group(0)) or 0)
            if 0 < num <= chapter_num and num > best_num:
                best_num, best_text = num, seg
    if best_num <= 0:
        return None
    return best_num, best_text


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

# ============================================================
# 压力阶段词表（PressureStage）—— 单一真源（2026-09-19，M6-B 新增）
#
# ★ 为什么要在这里定义（纪律 #19/#22）：
#   「压力阶段」此前是**没有登记表的自由文本**：`m5_context._determine_pressure_stage`
#   把 subline.md 压力曲线表的第 1 列**原样** return，下游三处按字面量比较：
#       · ``agentic_write.py:339``  ``ctx["pressure_stage"] == "高潮"`` → 加载爽点技法
#       · ``agentic_write.py:809``  ``ctx["pressure_stage"] == "高潮"`` → is_climax
#         ⇒ 直接进入评委提示词（``is_climax="是"/"否"``）与质检分支
#       · ``m5_quality_gate.py:47`` / ``m5_persist.py:821`` 拼文案
#   ⇒ 曲线表里写「舒缓/收束」，下游 ``== "高潮"`` 比较即静默为假，
#     **无报错、无日志**（纪律 #21「静默失真」的形态）。
#
# ★ 实测（M6-B 取证，70 份含压力曲线表的 subline.md）：
#     · 主词表（52 项目）：铺垫 / 冲突 / 高潮 / 舒缓
#     · **未登记变体**：``舒缓/收束``（2 项目）、``结局/收束``（1 项目）
#     · 影响章节：3 个支线（jipin-yixian S03/S04/S05）
#   ⇒ 变体全部来自 `_duplicate_backup` 备份目录；**活跃项目落盘值
#     （1694 章）全在四主词内** ⇒ 当前是**潜在缺陷**而非已发生故障。
#
# ★ 处置口径（纪律 #20：闸门强度必须与证据匹配）：
#   采用「**归一 + 告警**」，不采用「fail-fast 硬拦」——因为
#   ① 变体是**合法创作语义**（「舒缓/收束」= 舒缓收束期），硬拦会误伤；
#   ② 历史数据已含该形态，硬拦 = 挡合法历史（纪律 #18 缺豁免）。
#   ⇒ 归一到主词（保下游 ``== "高潮"`` 语义正确）+ 留痕告警（可观测）。
# ============================================================

#: 压力阶段**主词表**（顺序即张力降序，与 ``PACE_TIERS`` 同轴）。
#: ⚠ 改动本元组必须同步 ``_PRESSURE_STAGE_ALIASES`` 且跑
#:   ``tests/test_pressure_stage_ssot.py``（成员/派生关系机器核对）。
PRESSURE_STAGES: tuple[str, ...] = ("高潮", "冲突", "铺垫", "舒缓")

#: 张力序位（数字越大越紧张）——**由 PRESSURE_STAGES 派生**，勿手写。
PRESSURE_STAGE_RANK: dict[str, int] = {
    name: len(PRESSURE_STAGES) - i for i, name in enumerate(PRESSURE_STAGES)
}

#: 未登记变体 → 主词（归一表）。**键必须在真实语料中出现过**才算证据。
#: ⚠ 新增别名必须写明来源项目与章节，否则是"猜的兼容"（纪律 #4）。
PRESSURE_STAGE_ALIASES: dict[str, str] = {
    "舒缓/收束": "舒缓",   # 实测 jipin-yixian S03/S04（备份区）
    "结局/收束": "舒缓",   # 实测 jipin-yixian S05（备份区）
}

#: 归一表允许的**目标**必须是主词（防"别名指向别名"的链式漂移）。
for _alias, _canon in PRESSURE_STAGE_ALIASES.items():
    if _canon not in PRESSURE_STAGES:
        raise ValueError(
            f"PRESSURE_STAGE_ALIASES[{_alias!r}] 指向未登记主词 {_canon!r}；"
            f"合法主词 = {PRESSURE_STAGES}"
        )


def normalize_pressure_stage(raw: str) -> str:
    """把压力曲线表里的阶段词归一到 :data:`PRESSURE_STAGES` 主词。

    ★ 这是**唯一**的阶段词归一入口：下游任何按词表比较/分支的地方
      都必须先过本函数（否则就是纪律 #22「靠调用方猜对语义」）。

    归一规则（**显式且可测**）：
      1. 去空白；空串 → 空串（不臆造）
      2. 精确命中主词 → 原样返回
      3. 命中 :data:`PRESSURE_STAGE_ALIASES` → 返回对应主词
      4. **含主词作为前缀**（如「舒缓收束」「高潮段」）→ 返回该主词
      5. 其余 → 空串（**不猜**；由调用方决定回退，见 ``stage_normalization_note``）

    ⚠ 为什么规则 5 返回空串而不是原样：返回原样会让"未登记值"
      继续流到下游的字面量比较里，缺陷被掩盖。宁可显性丢失并告警。
    """
    if not raw:
        return ""
    s = str(raw).strip()
    if not s:
        return ""
    if s in PRESSURE_STAGES:
        return s
    alias = PRESSURE_STAGE_ALIASES.get(s)
    if alias:
        return alias
    # 前缀命中（长主词优先，避免「高潮」被更短的词抢先）
    for name in sorted(PRESSURE_STAGES, key=len, reverse=True):
        if s.startswith(name):
            return name
    return ""


def is_registered_pressure_stage(value: str) -> bool:
    """值是否为**已登记**主词（不含别名）。供红线与告警使用。"""
    return value in PRESSURE_STAGES


#: 从逐章行抠出档位值。**前导位置不限**，两种形态都必须命中：
#:   - v6 行首形态：``第1章：档位=日常｜章首钩子=…``（★ 现行提示词约定）
#:   - v5 行中形态：``第1章：章首钩子=…｜档位=日常``（兼容历史数据）
#:
#: ★★ 2026-09-19 真实 LLM 取证修复（纪律 #3 / #20 的教科书案例）
#:     原式写成 ``[｜|]\s*档位…`` —— **要求档位字段前必须有一个竖线**。
#:     而 v6 提示词（``prompts/m3/outline.md:33``）刻意把 `档位` 挪到**行首**
#:     （紧跟 `第N章：`，理由见该行注释：v5 放行尾会被长 `验收=` 挤掉）
#:     ⇒ 真实 LLM 输出 ``第1章：档位=日常｜章首钩子=…`` 前导是 ``：`` 不是 ``｜``
#:     ⇒ ``parse_pace_tier`` **一律返回空串**（实测 239 条逐章行，命中 **0**）
#:     ⇒ 档位供给链（M2 装配 / M3 写手分叉 / M4 评委参照系）在真实链路 **100% 空转**。
#:     ⚠ 而当时 164 条档位红线**全绿** —— 因为测试样本用的是 v5 行中形态。
#:       教训：语言锚（提示词改成行首）与解析式（正则仍要前导竖线）是同一件事的
#:       两半，改其一必须同时改另一半；且**单点单测绿 ≠ 链路口子通**。
#:
#: 前导约束的设计取舍
#: ------------------
#: ★ 用**零宽环视** ``(?<![^\s｜|【\[：:])`` 而非 ``(?:^|[｜|【\[\s])`` ——
#:   后者在实测中**反向失效**：``^`` 是**零宽断言**，正则引擎在位置 0 尝试
#:   ``^`` 分支成功匹配空串后，若整体匹配失败会**继续扫描**；但本模式在
#:   ``第1章：档位=日常`` 上，位置 0 的 ``^`` 成功、随后 ``\s*档位`` 失败，
#:   引擎前移到位置 1..3 时 ``^`` 不再成立、字符集也不含 ``：`` ⇒ **整体为 None**
#:   （实测：``第1章：档位=日常`` 命中 0，而 ``｜档位=日常`` 命中）。
#:   即"把锚点写进被消费分支"会让**锚点自身成为位置约束的载体**，
#:   与"字段名左侧要有边界"的意图相反。
#:
#:   环视的语义是精确的：**左侧必须是行首或分隔符/开括号/空白**。
#:   左侧是汉字（``我方档位``/``副档位``/``验收档位``）⇒ 拒绝；
#:   左侧是 ``：``/``:``/``｜``/``|``/``【``/``[``/空白/行首 ⇒ 接受。
#:   ⚠ **必须包含 ``：``/``:``**：v6 行首形态的前导正是 ``第N章：`` 的冒号。
#:
#: ⚠ 终止符须含中英文句读（``，,、。；;`` 等）：档位常出现在行尾，
#:   其后紧邻句号/逗号（如 ``｜档位=日常。``）——不收会把标点吞进取值。
#: ⚠ ``\s`` 与 ``】``/``]`` 也是终止符：v6 实测存在 ``档位=日常 ｜章首钩子``
#:   （档位值后有空格）与 ``【档位=日常】``（括注）两种收尾。
_PACE_TIER_RE = re.compile(
    rf"(?<![^\s｜|【\[：:])\s*{PACE_TIER_FIELD}\s*[=:：]\s*([^\s｜|，,、。；;）)：:（(】\]]+)"
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
    ★ **三级优先（v6）**——供给可以来自三处，按可靠性降序取**第一个命中**：
      1. **独立小节** ``## 章节强度档位``（v6 新增，最可靠）：每章一行
         ``第N章：<四档之一>``；档位与长钩子文本**解耦供给**，不会被挤掉。
      2. ``## 章节钩子设计`` 的本章逐章行内 ``｜档位=X``（v5 形态，兼容历史数据）。
      3. ``## 情节点序列`` 的本章逐章行内（容忍 LLM 标错小节）。
      三级皆未命中 ⇒ 返回空串（下游按"未标档位"处理）。

    ★ **写入端严格、读取端宽容**（本项目一贯口径）：规划侧三处都应给且应一致；
      读取侧容忍只有其一处——宽容是为了**兼容历史数据**，不是为了鼓励少给。

    ⚠ **必须校验"确实是本章逐章行"**：``select_chapter_lines`` 的兜底语义是
      「本章逐章行 ⇒ 最近前文逐章行 ⇒ 阶段行 ⇒ 整段」，若不做校验，回退出的
      文本里若碰巧含 ``｜档位=``（例如**最近前文**那行就带着上一章的档位，
      或规划者把格式示例写进了阶段文本），就会被误当成"本章档位"
      ⇒ **静默失真**（纪律 #21 同型）。取不到就返回空串。

      ★ 2026-09-18 N1 修复后风险上升：档位解析也必须挡住新的"最近前文"档，
        否则第 9 章会拿到第 5 章的档位（**参照系错位** ⇒ 评委按错误的尺判）。
        下方 ``_is_marked_as_non_current(line)`` 前缀判定即为此设。
    """
    try:
        num = int(chapter_num or 0)
    except (TypeError, ValueError):  # noqa: SILENT_DEGRADE reason=expected-skip
        return ""
    if num <= 0:
        return ""
    pat = chapter_line_pattern(num)
    # ① 独立档位小节（v6）：格式 `第N章：<档位>`
    tiers_body = extract_section(subline_md, TIERS_SECTION)
    if tiers_body:
        line = select_chapter_lines(subline_md, TIERS_SECTION, chapter_num=num)
        if line and not _is_marked_as_non_current(line) and pat.search(line):
            # 该小节的**行内没有 `档位=` 字段名**（整行就是档位）⇒ 直接整行取值
            tier = _parse_bare_tier(line)
            if tier:
                return tier
    # ②③ 历史形态：钩子行内 / 情节点行内
    for title in (HOOKS_SECTION, POINTS_SECTION):
        line = select_chapter_lines(subline_md, title, chapter_num=num)
        if not line or _is_marked_as_non_current(line):
            continue  # 最近前文/阶段文本 ⇒ 不是本章档位，不得借用
        if not pat.search(line):
            continue  # 兜底出的整段（无本章行）⇒ 此处没有章级档位
        tier = parse_pace_tier(line)
        if tier:
            return tier
    return ""


def _parse_bare_tier(chapter_line: str) -> str:
    """从**独立档位小节**的行里取值：整行可能只写档位名，也可能带 ``档位=``。

    容忍三种写法（LLM 对格式并不稳定，收紧只会漏读）：
      - ``第3章：推进``（v6 标准形态）
      - ``第3章：档位=推进``（行内字段形态）
      - ``第3章：推进（张力 6-8）``（带注形态）

    仍要求取值 ∈ :data:`PACE_TIER_NAMES`，否则返回空串。
    """
    if not chapter_line:
        return ""
    # 先试行内字段形态
    tier = parse_pace_tier(chapter_line)
    if tier:
        return tier
    # 再去掉「第N章：」前缀与括注后，在剩余文本里找**登记过的档位名**
    rest = re.sub(r"^\s*(?:第\s*\d+\s*章|\d+\s*章)\s*[:：]?\s*", "", chapter_line)
    rest = re.sub(r"[（(][^）)]*[）)]", "", rest)
    for name in PACE_TIER_NAMES:
        if name in rest:
            return name
    return ""


def pace_tiers_of_window(
    subline_md: str, window: tuple[int, int]
) -> list[tuple[int, PaceTier]]:
    """取**窗口内逐章**的强度档位（评委端参照系；未标档位的章不返回）。

    ★ 为什么必须逐章（M4，2026-09-18）：
      评委一次判 ``eval_window``（默认 5）章，而 :func:`pace_tier_of` 只给
      **本章**一档 ⇒ 供给粒度（1 章）≠ 消费粒度（N 章）⇒ 评委拿**最后一章**
      的尺量整窗 —— 正是纪律 #15 的「章级槽位由阶段级文本供给＝没供给」同型。
      用户命题「规划描述的是连续五章平平淡淡，校验就不该把它当注水打回」，
      若只给第 5 章的档位，其余四章**仍然**被高潮尺判注水 ⇒ 死循环原样复现。

    ⚠ 兼容性（纪律 #4）：老数据无逐章档位 ⇒ 返回空列表 ⇒ 评委端不渲染该块，
      判据回到通用口径（不放松也不收紧）。

    Returns:
        ``[(章号, PaceTier), ...]``，按章号升序；无任何登记档位时为空列表。
    """
    # ★ 刻意**不加** ``try/except + # noqa: SILENT_DEGRADE`` 防御：
    #   ``window`` 由 ``design_brief.build_design_brief`` 用 ``(max(1, ch-win+1), ch)``
    #   构造，形状确定；为不可能的输入开一条降级豁免，等于给棘轮（豁免只减不增）
    #   加计数、且让"真异常"也被静默吞掉（纪律 #1）。
    lo, hi = int(window[0]), int(window[1])
    out: list[tuple[int, PaceTier]] = []
    for n in range(max(1, lo), max(0, hi) + 1):
        tier = PACE_TIER_BY_NAME.get(pace_tier_of(subline_md, n))
        if tier is not None:
            out.append((n, tier))
    return out


def pace_tier_coverage(subline_md: str, window: tuple[int, int]) -> tuple[int, int, list[int]]:
    """窗口内**档位供给完整度**：返回 ``(已标章数, 窗口章数, 缺档章号列表)``。

    ★ 为什么要它（M5 前置，纪律 #20「闸门强度必须与证据匹配」）：
      档位供给是"能拿到才作数"的 —— 一旦缺章，**该章静默退回通用判据**
      （不报错、不记日志）＝ 纪律 #21 的**静默失真**同型。所以要有一个
      **逐章可检测**的完整度读数，供采样闸/巡检**度量供给质量**，
      而不是只能看到"整体有没有档位"。

    ⚠ **本函数只报数、不判罚、不补值**：
      - 不判罚：缺档不等于违规（历史数据本就无档位，且 LLM 产出总有波动）；
      - 不补值：绝不按 ``pressure_curve`` 反推档位——那是**系统替作者定意图**
        （纪律 #15 消费者编造的老路），比缺档更糟。
      调用方（M5 采样闸）自行决定"告警 / 计数 / 是否 fail-fast"。

    Args:
        subline_md: 细纲全文。
        window: ``(lo, hi)`` 闭区间章号。

    Returns:
        ``(已标章数, 窗口内章数, [缺档章号...])``；窗口无章时 ``(0, 0, [])``。
    """
    lo, hi = max(1, int(window[0])), max(0, int(window[1]))
    nums = list(range(lo, hi + 1))
    if not nums:
        return 0, 0, []
    missing = [n for n in nums if not pace_tier_of(subline_md, n)]
    return len(nums) - len(missing), len(nums), missing


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
    "NON_CURRENT_LABEL_SUFFIX",
    "PACE_TIER_BY_NAME",
    "PACE_TIER_FIELD",
    "PACE_TIER_NAMES",
    "PACE_TIERS",
    "POINTS_SECTION",
    "PRESSURE_STAGES",
    "PRESSURE_STAGE_ALIASES",
    "PRESSURE_STAGE_RANK",
    "PRIOR_CONTRACT_PREFIX",
    "TIERS_SECTION",
    "PaceTier",
    "chapter_contract",
    "chapter_line_pattern",
    "extract_section",
    "find_contract_annotations",
    "has_chapter_level_lines",
    "is_contract_annotation_line",
    "is_registered_pressure_stage",
    "normalize_pressure_stage",
    "pace_tier_coverage",
    "pace_tier_of",
    "pace_tiers_of_window",
    "parse_pace_tier",
    "select_chapter_lines",
    "strip_contract_annotations",
]
