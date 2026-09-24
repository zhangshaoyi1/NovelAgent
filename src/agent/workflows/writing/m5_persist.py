from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

import frontmatter
from rich.panel import Panel

from agent.core.infra.degrade import degrade
from agent.core.quality.consistency import ConflictReport
from agent.core.quality.consistency.checker import derive_realm_fact
from agent.core.story.evidence_chain import EvidenceChain, EvidenceRef
from agent.workflows.writing.m5_text_hygiene import hard_replace_english  # noqa: F401 - 兼容旧导入路径

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# G15 章后归档：确定性事实抽取（纯规则，无 LLM，失败静默降级不阻断写章）
# ---------------------------------------------------------------------------
# 背景（2026-09-11 五灵破 ch181/182 章间矛盾复盘）：旧实现 must_carry/facts 全部
# 硬编码空列表（占位实现，从未接线）→ 上一章动态状态（物品/人数/动作/约定）对
# 下一章不可见 → writer 只能自创 → 触发人设/设定/连贯性硬指标失败。
#
# P0-2 重做（2026-09-12，五灵破归档 31 次回退复盘）：旧版抽取器有两个致命偏移——
#   1) **主体错位**：``subject_id`` 用章节号（``ch179``），facts 形如
#      ``world/ch179.count=一道``（证据「裂开一道缝」）、``world/ch182.count=一根``
#      （证据「像一根钉入大地的桩」）——抽出来的是量词噪音，不是任何实体的事实；
#   2) **视野过窄**：只看 ``body[-800:]``，章首/章中的状态变化（换地图、角色阵亡、
#      道具易主）整段丢失，而翻车的恰恰是这类**长程状态**。
# 新版改为「**实体锚定**」（subject_id 一律取实体名：角色名 / 道具名），并把扫描
# 窗口放宽为「头 600 + 中 1800 + 尾 1200」（≤4000 字直通全文）。
# 定义性约束（X 是 Y / X 用于 Z）交由 ``core.story.setting_canon`` 沉淀并回写
# world.md，本抽取器只负责**状态/归属/计数**这类账本事实，两条通道各司其职不重复。

# 叙事道具名词（must_carry 用：匣/锁/符/印……出现在结尾即视为要携带的口径）
_ITEM_PATTERN = re.compile(r"[匣盒锁钥匙剑刀玉符信图卷丹印珠瓶牌镜环链杖]")
_OPEN_LOOP_PATTERN = re.compile(r"未[有着能]|尚未|还没|不曾|正要|刚要|约定|决意|决定|誓要|下一步")
_SENT_SPLIT = re.compile(r"[。！？!?\n]")

# ---- 扫描窗口：有界三段取样，杜绝「只盯结尾」 ----
_SCAN_LIMIT = 4000
_SCAN_HEAD, _SCAN_MID, _SCAN_TAIL = 600, 1800, 1200

# ---- 角色状态词表：命中即沉淀 character/<角色名>.state ----
_CHAR_STATES: tuple[str, ...] = (
    # 死亡
    "死了", "身亡", "阵亡", "陨落", "殒命", "毙命", "气绝",
    # 负伤 / 失能
    "昏迷", "昏死", "晕厥", "重伤", "垂危", "中毒", "中蛊", "瘫痪", "残废", "被俘", "被抓", "囚禁",
    # 恢复
    "痊愈", "苏醒", "醒来", "脱险", "获救",
    # 位移 / 离场
    "失踪", "下落不明", "离开", "离去", "逃走", "逃离", "远走",
)
_STATE_ALT = "|".join(re.escape(s) for s in _CHAR_STATES)

# ---- 位移动词：捕获 character/<角色名>.location ----
_MOVE_VERBS: tuple[str, ...] = (
    "来到", "抵达", "赶到", "前往", "奔赴", "返回", "回到", "进入", "退往", "逃往", "潜入",
)
_MOVE_ALT = "|".join(re.escape(s) for s in _MOVE_VERBS)

# ---- 持物动词：捕获 world/<道具名>.holder（道具易主是典型的长程状态） ----
_TAKE_VERBS: tuple[str, ...] = (
    "接过", "握住", "收起", "收好", "取出", "掏出", "拿过", "拿起", "夺过", "捡起", "夺得", "偷走",
)
_TAKE_ALT = "|".join(re.escape(s) for s in _TAKE_VERBS)

# ---------------------------------------------------------------------------
# 设计维度事实（2026-09-16）——「除了性格之外，别的都不是一成不变的」
# ---------------------------------------------------------------------------
# 背景（作者 2026-09-16 命题 + 登记单 20260916_角色弧光规格未建立与设计内转变
# 被误判）：旧抽取器只有 presence/state/location/holder/count 五类 ⇒
# **境界提升、关系演变、心性转变**这些"设计上明确要求推进"的维度**从不落盘**。
# 后果是双向的：
#   1) 后续章节永远拿**初始档案**说话（"已是铸府期"仍按杂役写、"已和解"仍按
#      排斥写）⇒ 与已发生剧情矛盾 ⇒ 真·设定冲突；
#   2) 更常见的是评委据此判「设计内推进 = 前后矛盾」⇒ 整窗回退（本次事故主因）。
# 因此新增三类事实，主体一律锚定**角色名**（与 P0-2 实体锚定同口径），
# 供后续章节注入新状态、供评委在「沿设计轨推进」时免罪。
_REALM_VERBS: tuple[str, ...] = (
    "突破", "晋升", "踏入", "进阶", "跨入", "凝成", "炼成", "登临", "修成",
)
_REALM_ALT = "|".join(re.escape(s) for s in _REALM_VERBS)
#: 境界名（通用形态：X期/X境/X阶/X层，1-4 字）；不依赖某一本书的专用名词表。
_REALM_NAME_RE = re.compile(r"([\u4e00-\u9fa5]{1,4}(?:期|境|阶|层))")

#: 关系演变动词（结盟/反目/和解…）——对应角色档案「关系」段登记的演变方向。
_RELATION_VERBS: tuple[str, ...] = (
    "结盟", "结拜", "拜师", "反目", "决裂", "和解", "认可", "投靠", "效忠",
    "背叛", "和解", "化敌为友", "并肩",
)
_RELATION_ALT = "|".join(re.escape(s) for s in _RELATION_VERBS)

#: 心性/立场转变（对应路线节点「心性：隐忍→果敢」）——作者命题里的"性格"那一项。
#: 只收**正向、已发生**的转变表述；「不再/没有」这类否定式由 _negated 守卫挡住，
#: 故此处刻意**不收录**否定前缀写法。
_DISPOSITION_VERBS: tuple[str, ...] = (
    "明白", "懂得", "醒悟", "释然", "放下", "决心", "立志", "坦然", "坚定",
)
_DISPOSITION_ALT = "|".join(re.escape(s) for s in _DISPOSITION_VERBS)

# ---- 计数：必须「数量 + 2~4 字实体性名词」，否则判为量词噪音 ----
# 旧版实况：``一道缝``（「裂开一道缝」）、``一根``（「像一根钉入大地的桩」）被当成
# 计数事实、主体还写成章号。现要求名词以实体性后缀收尾（人/符/剑/丹……），
# 于是「三枚镇魔符」「三个黑衣人」抽得到，而「钉入大地的桩」这类描写被挡下。
_NUM_ALT = r"[0-9一二两三四五六七八九十百千几半]+"
_UNIT_ALT = r"[人多双名只条枚颗道把柄根张块片具件样重层个]"
# 名词里出现这些字即判为切错的片段——三类：
#   1) 虚词 / 副词：「接着一人」「正在灵」；
#   2) 比况词 / 能愿动词 / 动词：「四个字像烙印」（比况）、「能翻盘」「身穿灰袍」
#      「伸手探向」「一个数字钉死」「二层已解锁」；
#   3) 数量修饰：「半个月有人」「一个半人高」「一条线五个人」。
# ★ 2026-09-25 扩充（灵荒工坊 ch47 判定事故）：旧表只有虚词，于是「『属性对冲』四个字
#   像烙印一样刻在他记忆里」被抽成 ``world/字像烙印.count=四个``，评委据此把 ch51 的
#   「符号出现在三个地方」判成**数量矛盾**（``logic_holes`` 硬指标不达标，整轮 escalated）。
#   宁可少抽（漏一条 count 事实无害）也不可抽错（脏实体进账本会持续污染评委判定）。
#   ★ 尺度：只收「不可能出现在实体名里」的字。实测反例——「劣」曾被误收，
#     于是「三枚劣品灵石」（正当实体）被一并挡掉；凡有反例的字一律不进本表。
_NOUN_BAD = re.compile(
    r"[的了着过在正接来去又并将被把对向从和与或是那此"  # 虚词 / 副词
    r"像似能翻伸呈都已穿嵌解有"                          # 比况 / 能愿 / 动词
    r"数个半]"                                          # 数量修饰
)
#: 只在「傀儡」里成词、从不单独作名词收尾的汉字——候选以此收尾即说明名词被截断
#: （「十二具标准化傀儡」曾抽成 ``标准化傀``、「一具粗劣傀儡」曾抽成 ``粗劣傀``）。
_INCOMPLETE_CHARS = frozenset("傀")
# 实体性收尾（人物 / 法器 / 器物）
_ENTITY_SUFFIX = re.compile(
    r"(人|者|众|手|弟子|门人|守卫|侍卫|兵|卒|符|剑|刀|丹|印|令|旗|阵|盘|卷|"
    r"珠|牌|匣|盒|袋|环|石|兽|妖|傀|尸|影|玉|铃|镜|锁|钥|碗|杯|盏|壶|杖|棍|"
    r"弓|箭|甲|袍|书|册|笔|绳|链|钉|针|瓶|罐|箱|图|符)$"
)
_COUNT_RE = re.compile(rf"({_NUM_ALT}{_UNIT_ALT})([\u4e00-\u9fa5]{{2,6}})")
# 持物动词后紧跟的名词可长一些（「拿起布局图边的一块碎石」→ 剥修饰语后取「碎石」）
_TAKE_NOUN_RE = re.compile(r"[\u4e00-\u9fa5]{2,12}")


def _pick_noun(run_text: str, entities: set[str], max_len: int = 4) -> str:
    """从量词后的汉字串里挑出实体性名词：由长到短取首个命中（镇灵符 > 镇灵）。"""
    upper = min(len(run_text), max_len)
    for length in range(upper, 1, -1):
        cand = run_text[:length]
        if _NOUN_BAD.search(cand):
            continue
        # 截断守卫：末字是「只作构词成分」的汉字（傀，须与「儡」连用）说明名词被切短，丢弃
        if cand[-1] in _INCOMPLETE_CHARS:
            continue
        if cand in entities or _ENTITY_SUFFIX.search(cand):
            return cand
    return ""


def _pick_object_noun(run_text: str, entities: set[str]) -> str:
    """持物句里挑道具名：先剥掉修饰语（「的」前）与前置量词，再取实体性收尾的名词。"""
    tail = run_text.rsplit("的", 1)[-1]
    stripped = re.sub(rf"^({_NUM_ALT}{_UNIT_ALT})", "", tail, count=1)
    return _pick_noun(stripped or tail, entities, max_len=4)


# 否定词：命中则说明该状态/动作**没有发生**（「林凡的手没有离开炉壁」≠ 离场）
_NEG_BEFORE = re.compile(r"[不没未别莫勿]")


def _negated(body: str, pos: int, span: int = 4) -> bool:
    return bool(_NEG_BEFORE.search(body[max(0, pos - span) : pos]))


def _chapter_body_text(chapter_num: int, chapter_text: str | None, chapters_dir: Path) -> str:
    """取本章正文体：优先调用方传入，缺省回读刚落盘的章节文件（去 frontmatter）。"""
    text = chapter_text
    if not text:
        f = chapters_dir / f"ch{chapter_num:03d}.md"
        if not f.exists():
            return ""
        text = f.read_text(encoding="utf-8")
    if text.startswith("---"):
        parts = text.split("---", 2)
        if len(parts) >= 3:
            text = parts[2]
    return re.sub(r"\s*\n\s*", "\n", text.strip())


def _tail_sentences(tail: str) -> list[str]:
    """结尾段切句，去空去重，保持原文顺序。"""
    out: list[str] = []
    for s in _SENT_SPLIT.split(tail):
        s = s.strip()
        if len(s) >= 6 and s not in out:
            out.append(s)
    return out


def _scan_chunks(body: str) -> list[str]:
    """有界三段取样：头 / 中 / 尾。

    旧实现只看 ``body[-800:]``，章首立旗、章中换地图、章尾留钩子这类**长程状态**
    全被丢弃。≤4000 字直接全文；超长时取「头 600 + 中 1800 + 尾 1200」共 3600 字，
    兼顾章首状态与章末交接，且注入口径固定、不随章长膨胀。
    """
    if len(body) <= _SCAN_LIMIT:
        return [body]
    mid_start = max(_SCAN_HEAD, (len(body) - _SCAN_MID) // 2)
    return [body[:_SCAN_HEAD], body[mid_start : mid_start + _SCAN_MID], body[-_SCAN_TAIL:]]


def _scan_sentences(body: str) -> list[str]:
    """按窗口切句，跨窗口去重并保序（窗口边界可能截出半句，故按窗口独立切）。"""
    out: list[str] = []
    for chunk in _scan_chunks(body):
        for s in _tail_sentences(chunk):
            if s not in out:
                out.append(s)
    return out


def _entity_names(ctx: dict[str, Any], body: str) -> list[str]:
    """已知实体名：characters_info 里的角色名 + 正文引号术语（本章涌现的道具/设定名）。

    引号术语必须过 ``is_term_like``：正文对白用 ASCII 引号，正则配对会把两段对白
    之间的文字整段当成引号内容（实况：``能恢复。`` 被当成实体名，进而让
    「一条河道来」被抽成 ``world/河道.count``）。后者虽无害，但会污染实体表、
    放大后续误匹配，必须挡在入口。
    """
    names = _present_characters(body, ctx)
    from agent.core.story.setting_canon import is_term_like

    for term in re.findall(r"[「『“\"]([^」』”\"\n]{2,12})[」』”\"]", body):
        t = term.strip()
        if t and t not in names and is_term_like(t):
            names.append(t)
    return names


def _present_characters(body: str, ctx: dict[str, Any]) -> list[str]:
    """从 characters_info（**name** 标记行）解析角色名，返回在本章正文出场者。"""
    names: list[str] = []
    for line in (ctx.get("characters_info") or "").splitlines():
        m = re.search(r"\*\*(.+?)\*\*", line)
        if m:
            name = m.group(1).strip()
            if name and name in body and name not in names:
                names.append(name)
    return names


def _extract_chapter_facts(
    chapter_num: int, body: str, ctx: dict[str, Any], project_dir: Path | None = None
) -> tuple[list[tuple[str, str, str, str, str]], list[str], list[str]]:
    """确定性抽取最小事实集（P0-2 实体锚定版）。

    Args:
        project_dir: 项目目录；给出时才启用「主角境界推进·跨句兜底」（见步骤 6b，
            需要读 world.md 境界体系 / plan.json 主角 / 账本承接值）。

    Returns:
        (facts_raw, must_carry, next_chapter_constraints)
        facts_raw: [(domain, subject_id, field, value, evidence)] → ContinuityFact
        must_carry / next_chapter_constraints: list[str] → ContinuityHandoff

    不变式：``subject_id`` **永远是实体名**（角色名 / 道具名），绝不是章节号。
    账本按 ``(domain, subject_id, field)`` 覆盖更新，用章节号当主体等于每章新建一行
    噪音——投影里全是「ch179.count=一道」这类写手无法消费的信息（旧版实况）。
    """
    facts_raw: list[tuple[str, str, str, str, str]] = []
    must_carry: list[str] = []
    constraints: list[str] = []
    if not body:
        return facts_raw, must_carry, constraints
    # 防御：调用方可能传原始文件内容（含 frontmatter），先剥掉再抽
    if body.startswith("---"):
        parts = body.split("---", 2)
        if len(parts) >= 3:
            body = parts[2]

    sentences = _scan_sentences(body)  # 有界三段窗口：must_carry / 约束取此
    body_sentences = _tail_sentences(body)  # 全文切句：仅用于取证据（准确优先）
    commit_id = f"ch{chapter_num:03d}"
    seen: set[tuple[str, str, str]] = set()

    def _emit(domain: str, subject: str, field_name: str, value: str, evidence: str) -> None:
        key = (domain, subject, field_name)
        if not subject or not value or key in seen:
            return
        seen.add(key)
        facts_raw.append((domain, subject, field_name, value, evidence[:100]))

    def _count_field(field_name: str) -> int:
        return sum(1 for f in facts_raw if f[2] == field_name)

    def _evidence(*needles: str) -> str:
        return next((s for s in body_sentences if all(n in s for n in needles)), "") or commit_id

    entities = set(_entity_names(ctx, body))
    characters = _present_characters(body, ctx)

    # 1) 出场角色（≤3）：presence —— 主体 = 角色名
    for name in characters[:3]:
        _emit("character", name, "presence", f"第{chapter_num}章在场", _evidence(name))

    # 2) 角色状态变化（≤6）：死亡/负伤/失能/恢复/离场——典型长程状态，下章不可逆
    for name in characters:
        if _count_field("state") >= 6:
            break
        m = re.search(rf"{re.escape(name)}[^，。；！？\n]{{0,8}}?({_STATE_ALT})", body)
        if not m or _negated(body, m.start(1)):
            continue
        _emit("character", name, "state", m.group(1), _evidence(name, m.group(1)))

    # 3) 位置变化（≤4）：抵达/返回/进入某地 —— 主体 = 角色名
    for name in characters:
        if _count_field("location") >= 4:
            break
        m = re.search(
            rf"{re.escape(name)}[^，。；！？\n]{{0,6}}?({_MOVE_ALT})([^，。；！？\n]{{2,12}})", body
        )
        if not m or _negated(body, m.start(1)):
            continue
        _emit("character", name, "location", f"{m.group(1)}{m.group(2)}", m.group(0))

    # 4) 道具易主（≤4）：world/<道具名>.holder = 持有者 —— 主体 = 道具名
    for name in characters:
        if _count_field("holder") >= 4:
            break
        m = re.search(
            rf"{re.escape(name)}[^，。；！？\n]{{0,4}}?({_TAKE_ALT})({_TAKE_NOUN_RE.pattern})", body
        )
        if not m or _negated(body, m.start(1)):
            continue
        item = _pick_object_noun(m.group(2), entities)
        if item:
            _emit("world", item, "holder", name, m.group(0))

    # 5) 计数口径（≤3）：必须「数量 + 实体性名词」，如「三枚镇魔符」「三个黑衣人」。
    #    量词后跟一串汉字时由长到短试，取首个实体性名词；全都不是实体
    #    （旧版实况：一道缝 / 一根钉入大地的桩）则整条丢弃。
    for m in _COUNT_RE.finditer(body):
        if _count_field("count") >= 3:
            break
        noun = _pick_noun(m.group(2), entities)
        if noun:
            _emit("world", noun, "count", m.group(1), m.group(0))

    # 6) 设计维度·境界推进（≤3）：character/<角色名>.realm
    #    设计上「境界：杂役→归一期」是明确要求的推进；不落盘则后续章节永远按
    #    初始境界写，评委据此判「设定被打破」（设计内推进被当成冲突）。
    for name in characters:
        if _count_field("realm") >= 3:
            break
        # 组 1 = 动词（用于否定守卫定位），组 2 = 境界名。
        # 中间允许逗号（「林砚闭关三月，终于突破到栖气期」），但不跨句读。
        m = re.search(
            rf"{re.escape(name)}[^。；！？\n]{{0,12}}?({_REALM_ALT})(?:到|至|为|了)?"
            rf"[^。；！？\n]{{0,6}}?({_REALM_NAME_RE.pattern})",
            body,
        )
        if not m or _negated(body, m.start(1)):
            continue
        _emit("character", name, "realm", m.group(2), m.group(0))

    # 6b) 主角境界推进·跨句兜底（2026-09-24 灵荒工坊 ch043 实证）
    #     步骤 6 要求「角色名与境界名**同句**」；正文把突破写成对白里的独立短句
    #     （`"突破了。栖气期初期。"`）时两者不在同一句 ⇒ 漏抽，且 LLM 结算也没记
    #     ⇒ 承接锚点在账本里永不前进 ⇒ 后续章节按旧境界写 ⇒ 与已发生正文自相矛盾。
    #     只追认主角「承接 + 恰好 1 档」的推进（越级留给 realm_span 规则 BLOCK）。
    if project_dir is not None:
        derived = derive_realm_fact(project_dir, body)
        if derived:
            _emit(
                "character",
                derived["subject_id"],
                derived["field"],
                derived["value"],
                derived["evidence"],
            )

    # 7) 设计维度·关系演变（≤4）：character/<角色名>.relation
    #    角色档案「关系」段登记的是**演变方向**（"从排斥到认可"）；不落盘则
    #    后续仍按旧关系写，与已发生剧情矛盾（真冲突），且评委无从判断这是设计。
    for name in characters:
        if _count_field("relation") >= 4:
            break
        m = re.search(
            rf"{re.escape(name)}[^。；！？\n]{{0,16}}?({_RELATION_ALT})",
            body,
        )
        if not m or _negated(body, m.start(1)):
            continue
        _emit("character", name, "relation", m.group(1), m.group(0))

    # 8) 设计维度·心性转变（≤3）：character/<角色名>.disposition
    #    对应路线节点「心性：隐忍→果敢」。这是**被误判成"人设崩坏"最多**的一类
    #    ——必须在写完后落盘为新状态，否则弧线每推进一次就被判一次崩坏。
    for name in characters:
        if _count_field("disposition") >= 3:
            break
        m = re.search(
            rf"{re.escape(name)}[^。；！？\n]{{0,16}}?({_DISPOSITION_ALT})",
            body,
        )
        if not m or _negated(body, m.start(1)):
            continue
        _emit("character", name, "disposition", m.group(0)[:40], m.group(0))

    # 9) must_carry：结尾段含叙事道具的句子（匣/锁/钥匙/符……），保权威口径
    item_hits = [s for s in sentences if _ITEM_PATTERN.search(s)]
    for s in item_hits[-2:]:
        must_carry.append(s[:120])

    # 10) 未闭环动作（≤3）：下一章约束（待续口径）
    for s in sentences:
        if _OPEN_LOOP_PATTERN.search(s):
            entry = "待续：" + s[:110]
            if entry not in constraints:
                constraints.append(entry)
        if len(constraints) >= 3:
            break

    # 11) must_carry 兜底：仍为空时取结尾最后一句（结尾状态永远必须携带）
    if not must_carry and sentences:
        must_carry.append(sentences[-1][:120])

    return facts_raw, must_carry[:5], constraints


@dataclass
class PreValidationResult:
    """E3 前置冲突检测结论"""

    decision: str  # "continue" | "interrupt"
    report: ConflictReport
    auto_resolved: list[str] = field(default_factory=list)


class M5PersistMixin:
    """依据链 / 持久化 / 归档 / 进度 / 呈现 / E3 前置门禁（由 m5_write_chapter 拆出，仅由 M5WriteChapterWorkflow 组合使用）"""

    # ============================================================
    # 5. 依据链
    # ============================================================
    def _build_evidence_chain(self, ctx: dict[str, Any]) -> EvidenceChain:
        """构建本章引用的设定条目（E4 结构化分类引用）

        分类：
            - settings：世界观 / 境界 / 金手指 / 支线 / 路线 / 关系网
            - characters：本章涉及角色档案
            - foreshadows：伏笔登记表 + 本章伏笔任务涉及的 F-ID
        """
        wi = ctx["world_info"]
        settings: list[EvidenceRef] = [
            EvidenceRef(name=wi.get("title", ""), field="世界观/故事简介", source="world.md"),
        ]
        if wi.get("realm_system"):
            settings.append(
                EvidenceRef(name="境界体系", field="境界体系（冻结）", source="world.md")
            )
        if wi.get("golden_finger_info"):
            settings.append(EvidenceRef(name="金手指", field="金手指登记", source="world.md"))
        settings.append(
            EvidenceRef(
                name=ctx["subline_name"],
                field="支线目标",
                source=f"sublines/{ctx['subline_id']}/subline.md",
            )
        )
        settings.append(
            EvidenceRef(
                name=ctx["route_node_id"],
                field="主角路线节点",
                source="protagonist_route.md",
            )
        )
        settings.append(
            EvidenceRef(name="关系网", field="关系当前状态", source="relations/graph.md")
        )

        characters: list[EvidenceRef] = []
        for line in ctx["characters_info"].splitlines():
            m = re.search(r"\*\*(.+?)\*\*", line)
            if m:
                name = m.group(1).strip()
                # 名单里的名字可能与档案文件不一致（规划占位名/模糊匹配命中）：
                # 只登记真实存在的档案，否则每章刷「引用源不存在」告警污染溯源元数据
                source = f"characters/{name}.md"
                if not (self.project_dir / source).exists():
                    resolved = self._resolve_character_source(name)
                    if not resolved:
                        continue
                    source = resolved
                characters.append(
                    EvidenceRef(name=name, field="身份/动机", source=source)
                )

        foreshadows: list[EvidenceRef] = [
            EvidenceRef(name="伏笔登记表", field="全局伏笔", source="foreshadows.md"),
        ]
        for fid in re.findall(r"F-\d+", ctx.get("foreshadow_task", "")):
            foreshadows.append(
                EvidenceRef(ref_id=fid, field="本章伏笔任务", source="foreshadows.md")
            )

        return EvidenceChain(characters=characters, foreshadows=foreshadows, settings=settings)
    # ============================================================
    # 6. 持久化
    # ============================================================
    def _save_chapter(
        self,
        ctx: dict[str, Any],
        text: str,
        title: str,
        word_count: int,
        quality_passed: bool,
        revision_attempts: int,
        evidence_chain: EvidenceChain,
    ) -> Path:
        """保存章节文件（frontmatter 含 E4 结构化证据链）"""
        self.chapters_dir.mkdir(parents=True, exist_ok=True)
        file = self.chapters_dir / f"ch{ctx['chapter_num']:03d}.md"

        metadata = {
            "chapter": ctx["chapter_num"],
            "subline": ctx["subline_id"],
            "route_node": ctx["route_node_id"],
            "pressure_stage": ctx["pressure_stage"],
            "title": title,
            "created_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "word_count": word_count,
            "quality_passed": quality_passed,
            "revision_attempts": revision_attempts,
            "evidence_chain": evidence_chain.to_dict(),
        }
        # ---- G-EN：落盘前绝对零英文关卡（单一写盘点，任何写章路径都过此门）----
        # 缺口 B（2026-09-06）：统一改走 _finalize_chapter_text（canonical body 链，
        # 与门禁消费口径同源）。链内各步幂等，上游已 finalize 过时此为确定性兜底。
        text = self._finalize_chapter_text(text)
        word_count = len(text.replace("\n", "").replace(" ", ""))
        metadata["word_count"] = word_count
        # 缺口 B：成文（标题 + 正文）由 compose_chapter_markdown 唯一合成，
        # 落盘形态 == 门禁形态，guardrails 标题检查/查重自此生效。
        body = self.compose_chapter_markdown(ctx["chapter_num"], title, text)
        post = frontmatter.Post(body, **metadata)
        # P0-3（原子落盘）：temp + replace，杜绝进程中断留下截断的半成品章节文件。
        # 与 state_machine.save()（同样 temp+replace）配合，写序为「先正文后状态指针」，
        # 即使中途崩溃也只会出现"章节已存在、进度落后"的可恢复态，不会出现反向损坏。
        from agent.core.infra.atomic import atomic_write_text

        atomic_write_text(file, frontmatter.dumps(post))
        return file

    def _record_book_ledger(
        self,
        ctx: dict[str, Any],
        chapter_title: str,
        chapter_text: str | None = None,
        quality_passed: bool = True,
        revision_attempts: int = 0,
    ) -> None:
        """书级台账 hook（2026-09-12）：登场登记 + 质量基线记录。

        章后持久化书级信号——登场连续性登记表（"初次登场用再次口吻"检测的
        数据源）与滑窗质量基线（全书质量漂移告警的数据源）。任何异常显性
        降级不阻断写章（与 _archive_chapter 同语义）。
        """
        try:
            from agent.core.quality import book_ledger

            chapter_num = int(ctx["chapter_num"])
            body = _chapter_body_text(
                chapter_num, chapter_text, self.chapters_dir
            )
            book_ledger.record_debuts(self.project_dir, body, chapter_num)
            book_ledger.record_quality(
                self.project_dir, chapter_num, quality_passed, revision_attempts
            )
        except Exception as e:  # noqa: BLE001 - 台账失败不阻断写章
            degrade(
                "m5.record_book_ledger",
                "书级台账记录失败（登场登记/质量基线），跳过不阻断",
                e,
            )

    def _record_tension(self, ctx: dict[str, Any], chapter_text: str | None = None) -> None:
        """M6-B1 章后 hook：落盘本章**实测张力**（纯观测面，不是供给面）。

        背景：``tension_curve`` 此前**零生产调用点**（M6-A 取证），
        本 hook 是它第一条生产链路。

        ★ 定位：这是「**事实**」（正文统计）通道，与强度档位「**意图**」
        （规划标注）分属两条独立线，**绝不用张力反推/覆盖档位**——
        那是系统替作者定意图，违反 ``chapter_contract.py:447`` 既有红线。

        ⚠ 消费者说明（纪律 #7，2026-09-19 复核修正）：
        本 hook **只写台账**（``TENSION_LEDGER`` = ``.state/memory/tension_readings.json``）。
        ``TensionCurveManager.check_rhythm`` 读的是**内存 state** ``self._scores``，
        而 ``_scores`` 只由 ``evaluate_chapter()`` 填充 —— 该入口**当前仍是零生产调用点**。
        ⇒ **本 hook 与 ``check_rhythm`` 不相通**，它不是 ``check_rhythm`` 的输入源。
        台账的读取方是 ``read_tension()``（见 C 项待收口）。

        失败降级不阻断写章（观测面失败 ≠ 主结论失败，纪律 #1/#2）。
        """
        try:
            from agent.core.story.tension_curve import record_tension

            body = _chapter_body_text(
                int(ctx["chapter_num"]), chapter_text, self.chapters_dir
            )
            record_tension(self.project_dir, int(ctx["chapter_num"]), body)
        except Exception as e:  # noqa: BLE001 - 观测面失败不阻断写章
            degrade(
                "m5.record_tension",
                "实测张力落盘失败（观测面缺失，不影响本章产出）",
                e,
            )

    def _archive_chapter(self,
        ctx: dict[str, Any],
        chapter_title: str,
        chapter_text: str | None = None,
    ) -> None:
        """G15 章后归档 hook：本章最小交接归档进连续性账本 + 伏笔 beats 标记落地。

        - 向 `ContinuityLedgerStore.commit` 写入本章交接（source_commit_id=本章 ID），
          `latest_handoff()` 即成为下一章投影的「上一章交接」来源。
        - facts / must_carry / next_chapter_constraints 由确定性抽取器从本章正文
          （优先 ``chapter_text``，缺省回读落盘文件）产出，非空（能力对账红线
          ``test_capability_parity`` 强制：禁止字面量空列表回潮）。
        - 把规划锚指向本章（``anchor_chapter == 本章``）的伏笔 beat 标记为 committed，
          由纯函数 `derive_status` 自动推进线程状态。
        - 缺账本 / 任何异常 → 静默降级，绝不阻断写章（与「降级不阻断」一致）。
        """
        try:
            from agent.core.continuity import ContinuityFact, ContinuityHandoff, ContinuityLedgerStore
            from agent.core.story.foresight import ForesightBeat, ForesightStore, mark_committed

            chapter_num = ctx["chapter_num"]
            commit_id = f"ch{chapter_num:03d}"

            body = _chapter_body_text(chapter_num, chapter_text, self.chapters_dir)
            facts_raw, must_carry, constraints = _extract_chapter_facts(
                chapter_num, body, ctx, self.project_dir
            )
            facts = [
                ContinuityFact(
                    domain=domain,
                    subject_id=subject_id,
                    field=field_name,
                    value=value,
                    source_commit_id=commit_id,
                    evidence=evidence,
                )
                for domain, subject_id, field_name, value, evidence in facts_raw
            ]
            tail = body[-260:] if body else ""
            summary = (
                f"第{chapter_num}章《{chapter_title}》结尾状态：{tail}" if tail
                else f"第{chapter_num}章《{chapter_title}》"
            )

            ledger = ContinuityLedgerStore(self.project_dir)
            ledger.load()
            ledger.commit(
                chapter=chapter_num,
                facts=facts,
                knowledge=[],
                open_loops=[],
                handoff=ContinuityHandoff(
                    chapter=chapter_num,
                    summary=summary,
                    must_carry=must_carry,
                    next_chapter_constraints=constraints,
                    source_commit_id=commit_id,
                ),
            )

            from agent.workflows.evaluation.m13_foreshadow import seed_foresight_threads

            seed_foresight_threads(self.project_dir)  # P1-4：先播种，mark 才有 beat 可标
            store = ForesightStore(self.project_dir)
            threads = store.load()
            changed = False
            for t in threads:
                for b in t.beats:
                    if b.anchor_chapter == chapter_num and b.exec_status != "committed":
                        mark_committed(t, ForesightBeat.model_validate(b), commit_id)
                        changed = True
            if changed:
                store.save(threads)

            self._sync_setting_canon(chapter_num, body, ctx)
        except Exception as e:  # noqa: BLE001 - 归档失败降级不阻断
            degrade(
                "m5_persist.archive_chapter",
                "章后归档失败（连续性账本/伏笔标记），不影响本章产出",
                e,
            )
            logger.debug("[continuity] 章后归档失败，已降级", exc_info=True)

        # P1-5 收尾：LLM delta 结算——补充确定性抽取抓不到的信息差变化与开环
        # 推进/闭环（独立 try，失败 degrade 不阻断写章，账本保持确定性提交状态）。
        # 无 llm 的实例（测试桩/独立混用）无结算能力，静默跳过——确定性抽取
        # 通道已提交本章事实，缺 LLM 结算不构成失败、不应触发 degrade 告警。
        if getattr(self, "llm", None) is not None:
            try:
                from agent.workflows.writing.ledger_delta_producer import produce_and_apply_delta

                produce_and_apply_delta(
                    str(self.project_dir),
                    self.llm,
                    chapter_num=int(ctx["chapter_num"]),
                    chapter_title=chapter_title,
                    # 独立重算正文（不依赖上方归档 try 的局部变量；缺省回读落盘文件）
                    chapter_text=_chapter_body_text(
                        int(ctx["chapter_num"]), chapter_text, self.chapters_dir
                    ),
                )
            except Exception as e:  # noqa: BLE001 - 结算失败降级不阻断
                degrade("m5_persist.ledger_delta", "章后 LLM 账本结算异常", e)

    def _sync_setting_canon(self, chapter_num: int, body: str, ctx: dict[str, Any]) -> None:
        """P0-1（2026-09-12）：设定回写通道——把本章涌现的定义性约束沉淀进台账并回写 world.md。

        背景：``world.md`` 自开书当天起从未被回写（五灵破归档写满 185 章、零回写），
        写作过程中涌现的设定（阵盘 / 镇灵符 / 导引纹 / 五行属性……）只存在于正文章节里，
        Writer 隔几章就把同一设定重新发明一遍 → 章间矛盾 → 人设/设定/连贯性硬指标长期不达标。
        本方法是缺失的「设定回流」那一环：抽定义 → 入台账 → 检测冲突 → 回写 world.md。

        任何异常一律静默降级，绝不阻断写章。
        """
        try:
            from agent.core.story.setting_canon import SettingCanon, extract_definitions

            entities: list[str] = []
            for line in (ctx.get("characters_info") or "").splitlines():
                m = re.search(r"\*\*(.+?)\*\*", line)
                if m and m.group(1).strip():
                    entities.append(m.group(1).strip())

            entries = extract_definitions(chapter_num, body, known_entities=entities)
            if not entries:
                return
            canon = SettingCanon.load(self.project_dir)
            canon.merge(entries)
            canon.save()
            canon.sync_to_world_md()
        except Exception as e:  # noqa: BLE001 - 设定台账失败绝不阻断写章
            degrade("m5_persist.setting_canon", "设定回写失败，不影响本章产出", e)
            logger.debug("[setting-canon] 设定回写失败，已降级", exc_info=True)

    def _pre_validation(self, ctx: dict[str, Any]) -> PreValidationResult:
        """E3 前置冲突检测门禁

        Returns:
            PreValidationResult：
                - 无冲突 → continue
                - 高严重度冲突 → interrupt（需用户仲裁）
                - 低/中冲突 → 自动仲裁（写入 world.md 修订日志）后 continue
        """
        planned = self._build_planned_setting(ctx)
        report = self.conflict_arbiter.check_new_setting(  # type: ignore[union-attr]
            planned, subline_id=ctx["subline_id"]
        )

        if not report.has_conflict:
            return PreValidationResult("continue", report)

        if report.needs_arbitration:
            # 高严重度：记录到 world.md 修订日志并中断生成
            high_fields = ", ".join(
                c.field for c in report.conflicts if c.severity == "high"
            )
            self.sm.append_revision_log(
                f"[仲裁-高] 前置冲突检测拦截生成：{report.summary}"
                f"（高严重度字段：{high_fields}）"
            )
            return PreValidationResult("interrupt", report)

        # 低/中严重度：自动采用新设定，记录仲裁结果
        for c in report.conflicts:
            self.sm.append_revision_log(
                f"[仲裁-自动] 字段 {c.field}（{c.severity}）："
                f"{c.suggestion or '自动采用新设定，继续生成'}"
            )
        return PreValidationResult(
            "continue", report, auto_resolved=[c.field for c in report.conflicts]
        )
    # ============================================================
    # E4 证据链校验
    # ============================================================
    def _resolve_character_source(self, name: str) -> str | None:
        """模糊匹配角色档案：名单名与档案文件名互含即视为同一角色（与 m5_context 口径一致）。"""
        chars_dir = self.project_dir / "characters"
        if not chars_dir.exists():
            return None
        for p in chars_dir.glob("*.md"):
            if name in p.stem or p.stem in name:
                return f"characters/{p.stem}.md"
        return None

    def _validate_evidence(self, chain: EvidenceChain) -> EvidenceChain:
        """F-E4.3 落盘前校验所有引用源文件是否存在

        缺失的源仅记录告警，不阻断落盘（引用源本就来自已加载文件）。
        """
        missing: list[str] = []
        for r in chain.all_refs():
            if r.source and not (self.project_dir / r.source).exists():
                missing.append(r.source)
        chain.missing_sources = missing
        if missing:
            self.console.print(
                f"[yellow]⚠ 证据链中有 {len(missing)} 个引用源不存在："
                f"{', '.join(missing)}[/yellow]"
            )
        return chain
    # ============================================================
    # 7. 更新进度
    # ============================================================
    def _update_progress(self, ctx: dict[str, Any]) -> None:
        """更新 state.json 的 progress 字段。

        G8（拍板 4/补充边界 1）关键兼容点：**合并写入**（progress.update），
        保留 mainline_visited / ending_mode / mainline_* / ending_* 等既有键；
        禁止全新 dict 覆盖（否则 G8 状态每次写章被抹掉）。
        """
        self.state_machine.load()
        progress = dict(self.state_machine.progress or {})
        # F-7：total_written 只增不回流（max）——并发双进程写同一项目时，
        # 先写完的进程可能被后写的进程用较小 chapter_num 覆盖回退；
        # max(当前, chapter_num) 保证进度单调推进（用户手动归零除外，写章从 1 起）。
        progress.update({
            "current_subline": ctx["subline_id"],
            "current_chapter": ctx["chapter_num"],
            "total_written": max(int(progress.get("total_written", 0) or 0), int(ctx["chapter_num"])),
            "last_written_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        })
        # G8：mainline_visited 双保险初始化（未记录时以当前支线打底；已存在则保留）
        visited = progress.get("mainline_visited")
        if not isinstance(visited, list):
            visited = []
        if ctx["subline_id"] and ctx["subline_id"] not in visited:
            visited.append(ctx["subline_id"])
        progress["mainline_visited"] = visited
        self.state_machine.progress = progress
        self.state_machine.save()
    # ============================================================
    # 8. 呈现
    # ============================================================
    def _present(
        self,
        chapter_file: Path,
        ctx: dict[str, Any],
        word_count: int,
        quality_passed: bool,
        revision_attempts: int,
    ) -> None:
        """展示章节摘要"""
        status = "[green]✓ 通过[/green]" if quality_passed else "[yellow]△ 未完全通过[/yellow]"
        self.console.print(
            Panel(
                f"第 {ctx['chapter_num']} 章 · {ctx['pressure_stage']}阶段\n"
                f"字数：{word_count} | 质量：{status} | 修订：{revision_attempts} 次\n"
                f"文件：{chapter_file.relative_to(self.project_dir)}",
                title=f"ch{ctx['chapter_num']:03d}.md",
                border_style="green" if quality_passed else "yellow",
                expand=False,
            )
        )
