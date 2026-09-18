"""设计产出供给单源（2026-09-16）——「凡设计，三端可达」。

作者命题（2026-09-16）
--------------------
> 「除了性格之外，别的都不是一成不变的，设计好的内容全部都要传递给写手
> 评委最终落盘，不要只设计不通知，这样设计没有任何意义。」

事故链条（登记单 ``20260916_角色弧光规格未建立与设计内转变被误判``）
-----------------------------------------------------------------
``plan.json.route.nodes[].main_branch.growth`` 早已含「心性：隐忍→果敢」，
``characters/<名>.md`` 的 ``## 内核`` 含「弧光：起始状态 → 终结状态」、
``## 关系`` 含「从排斥到认可」，``world.md`` 有冻结设定，``sublines/*/subline.md``
有钩子设计与情节点序列。**设计是齐的**。

缺的是**通知**：

===================  ==================================================
消费端               设计产出的到达情况
===================  ==================================================
写手 ``m5_context``   部分到达（成长预期 ✅ / 设定台账 ✅ / 弧光轨迹 ❌ / 判据 ❌）
写时门禁 ``_SCORE_CTX_FIELDS``  部分到达（``route_main_growth`` ✅，只在开头 3 章用）
**批末体检** ``reader_appeal._gather_for_eval``  **零引用**设计意图 ❌❌
落盘 ``m5_persist``   五类事实（presence/state/location/holder/count），
                      无境界/心性/关系等**设计维度的演进** ❌
===================  ==================================================

后果是确定性的，不是概率性的：写手每轮被要求写「兼济」，评委每轮被要求抓
「言行前后矛盾」且**看不到「兼济」是设计好的** ⇒ 判崩坏 ⇒ 整窗 5 章销毁重写
⇒ 写手仍拿到同一条成长预期、评委仍拿不到 ⇒ **无限回退，净增 0 章**。

本模块的定位
------------
把「设计产出」收敛为**一次装配、三端渲染**，从根上消除"设计只进不出"：

    设计真源（plan.json / world.md / characters/*.md / sublines/*/subline.md /
              .state/setting_canon.json）
        │
        ├── render_for_writer()   → 写手：本章设计意图 + 弧线轨迹 + 达标判据
        ├── render_for_judge()    → 评委：设定真源 + 角色真源 + 设计意图 + 弧线轨迹
        │                           + **设计内转变免罪**判定前提
        └── render_for_persist()  → 落盘：本章设计期望推进的状态（结转核对）

设计准则
--------
1. **只增不改**：全部为增强信息。任一真源缺失/损坏 → 该块为空串，经 ``degrade``
   显性化（不得静默），**绝不阻断**写章或评分。
2. **有界**：每块都有字符预算，避免把 prompt 挤爆导致正文被截断（这正是
   ``_PROMPT_CHARS`` 当初踩过的坑）。
3. **单一真源**：本模块只读真源、不产生真源；写手/评委/落盘三端共用同一份装配，
   禁止任一端自行实现抽取（红线 ``test_design_brief_reaches_all_consumers`` 拦漂移）。

依赖方向：本模块属 ``agent.core.story``（领域层），只依赖标准库 + 同层 ``degrade``。
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from agent.core.infra.degrade import degrade

__all__ = [
    "DESIGN_EXEMPTION",
    "DesignBrief",
    "build_design_brief",
    "render_quality_rubric",
]

_RANGE_RE = re.compile(r"(\d+)\s*[-~]\s*(\d+)")

#: 各块的字符预算（超出即截断；宁可截断也不挤掉正文——截断本身有告警）。
_BUDGET = {
    "rubric": 900,
    "route_track": 900,
    "chapter_intent": 1200,
    "setting_facts": 2800,
    "character_facts": 2000,
    "expectation": 700,
    "canon": 2400,
}

#: 「设计内转变 ≠ 崩坏」判定前提（三端共用同一段文字，禁止各写一份）。
#:
#: 2026-09-16：这条是本次事故的**直接解药**。评委此前只拿到「逐项列举崩坏处」，
#: 拿不到「本窗口的设计轨」，于是把**设计好的**性格推进数成崩坏。加此前提后，
#: 误报下降（设计内转变免罪）而**漏报不升**（设计外漂移照抓）——不是放松门槛，
#: 是把判据说完整。
DESIGN_EXEMPTION = (
    "【判定前提·设计内转变 ≠ 崩坏】以下为规划端登记的设计轨（含路线节点成长、"
    "角色弧光、关系演变、章级设计意图）。本窗口内角色性格/境界/能力/关系若沿该轨迹"
    "**有序推进**（顺序一致、有可指认的触发事件、落在登记的章区间内），属**设计内变化**，"
    "不计人设崩坏 / 设定冲突 / 逻辑漏洞；仅当变化**超出**登记轨迹"
    "（倒退、跳档、无契机、与设定台账直接冲突）才计 issue。"
    "判 issue 前先确认它**不是**设计轨里已登记的内容。"
)


def _parse_range(text: Any) -> tuple[int | None, int | None]:
    """解析 ``'1-150'`` / ``'43~82'`` 形态的章区间。"""
    m = _RANGE_RE.search(str(text or ""))
    if not m:
        return None, None
    return int(m.group(1)), int(m.group(2))


_WS_BLANK_RE = re.compile(r"\n[ \t]*\n(?:[ \t]*\n)+")


def _squeeze(text: str) -> str:
    """压掉多余空行（3+ 连续换行 → 1 个空行）与行尾空白。

    真源 md 常有成片空行，会**吃掉固定字符预算**、把真内容挤到截断线之外
    （"注入了但被截断"＝没注入）。压缩后同等预算能装下更多真内容。
    """
    if not text:
        return text
    text = _WS_BLANK_RE.sub("\n\n", text)
    return "\n".join(ln.rstrip() for ln in text.splitlines()).strip()


def _md_section(content: str, *titles: str) -> str:
    """取首个命中的 ``## <title>`` 小节正文（到下一个 ``##`` 为止）。

    返回前统一做空白压缩（见 :func:`_squeeze`）——所有取段调用方一并受益。
    """
    for title in titles:
        m = re.search(rf"^##\s*{re.escape(title)}\s*\n", content, re.M)
        if not m:
            continue
        rest = content[m.end():]
        nxt = re.search(r"^##\s", rest, re.M)
        body = (rest[: nxt.start()] if nxt else rest).strip()
        if body:
            return _squeeze(body)
    return ""


def _clip(text: str, key: str) -> str:
    limit = _BUDGET.get(key, 1000)
    if len(text) <= limit:
        return text
    return text[:limit] + "…（已截断）"


# ============================================================
# 装配
# ============================================================
@dataclass
class DesignBrief:
    """一次装配出来的设计产出摘要（三端渲染的公共输入）。

    所有字段均为**渲染好的文本块**（空串 = 该真源缺失，调用方按"无此信息"处理）。
    ``empty`` 为真时调用方应跳过注入（而不是注入一堆标题）。
    """

    chapter_num: int = 0
    window: tuple[int, int] = (0, 0)
    rubric: str = ""             # 达标判据（阈值 + 维度语义）
    route_track: str = ""        # 弧线轨迹（全书路线节点成长序列 + 当前位置）
    chapter_intent: str = ""     # 章级设计意图（钩子/情节点/支线目标/主线结果）
    setting_facts: str = ""      # 设定真源（冻结段 + 台账 + 已知冲突）
    character_facts: str = ""    # 角色真源（内核/动机/弧光/关系/语言指纹）
    design_expectation: str = ""  # 落盘期望（本章设计上应推进的状态）

    @property
    def empty(self) -> bool:
        return not any(
            (self.rubric, self.route_track, self.chapter_intent,
             self.setting_facts, self.character_facts, self.design_expectation)
        )

    # ---------------------------------------------------------- 三端渲染
    def render_for_writer(self) -> str:
        """写手端：本章设计意图 + 弧线轨迹 + 达标判据。

        「只设计不通知」的直接对症：写手必须知道**设计上本章要表现什么**、
        **弧线这一跳是设计好的**、**批末体检拿什么尺子量**。
        """
        blocks: list[str] = []
        if self.chapter_intent:
            blocks.append(
                "【本章设计意图（规划端已登记，本章须落实；不得自行改道）】\n"
                + self.chapter_intent
            )
        if self.route_track:
            blocks.append(
                "【角色弧线轨迹（**设计内转变 ≠ 人设崩坏**：按此轨迹的推进是设计要求，"
                "不是前后不一致）】\n" + self.route_track
            )
        if self.rubric:
            blocks.append(
                "【达标判据（批末体检口径；写作时按此自检，避免写完被打回）】\n"
                + self.rubric
            )
        return "\n\n".join(blocks)

    def render_for_judge(self) -> str:
        """评委端：设定真源 + 角色真源 + 设计轨 + 判定前提。

        这是本次事故唯一的真缺口（``_gather_for_eval`` 零引用设计意图）。
        评委必须与写手**拿到同一份设计轨**，否则判据互斥、回退不收敛。
        """
        blocks: list[str] = []
        if self.setting_facts:
            blocks.append(self.setting_facts)
        if self.character_facts:
            blocks.append(self.character_facts)
        design: list[str] = []
        if self.route_track:
            design.append("【角色弧线轨迹（设计轨，非漂移）】\n" + self.route_track)
        if self.chapter_intent:
            design.append("【本窗口章级设计意图】\n" + self.chapter_intent)
        if design:
            blocks.append("\n".join(design))
        if self.rubric:
            blocks.append("【本作达标判据（判定时对标，勿自设更严口径）】\n" + self.rubric)
        if blocks:
            blocks.append(DESIGN_EXEMPTION)
        return "\n\n".join(blocks)

    def render_for_persist(self) -> str:
        """落盘端：本章设计上应推进的状态（供结转核对）。

        对应作者命题的「最终落盘」——弧线推进/境界提升/关系演变必须在写完后
        **记账**，后续章节以新状态为准，而不是永远拿初始档案说话。
        """
        if not self.design_expectation:
            return ""
        return (
            "【本章设计期望推进的状态（写完后核对：正文若已推进，应落盘为新状态）】\n"
            + self.design_expectation
        )


# ---------------------------------------------------------------- 各块装配
def render_quality_rubric(targets: Any) -> str:
    """把 ``plan.json.quality_targets`` 渲染成人话判据（阈值 + 维度语义）。

    阈值是**唯一真源**（规划端产出、体检端消费）；此处只是它的**渲染器**，
    供写手/评委看到同一把尺子。维度语义取自 ``dimension_registry``（SSOT）。
    """
    if not isinstance(targets, dict) or not targets:
        return ""
    try:
        from agent.core.quality.dimension_registry import DIMENSIONS
    except Exception as e:  # noqa: BLE001 - 登记表不可用 → 判据缺失，显性降级
        degrade("design_brief.rubric", "维度登记表不可用，达标判据渲染为空", e)
        return ""

    lines: list[str] = []
    for name, thr in targets.items():
        spec = DIMENSIONS.get(name)
        if spec is None or not spec.label:
            continue
        unit = getattr(spec, "unit", None)
        unit_val = getattr(unit, "value", "")
        try:
            t = float(thr)
        except (TypeError, ValueError):  # noqa: SILENT_DEGRADE reason=expected-skip
            continue
        if unit_val == "count":
            # 计数维：阈值 0 = 零容忍；同时给出「什么才算一条」的判据文本
            line = f"- {spec.label}：{t:g} 条（{spec.prompt_label}）"
        elif unit_val == "score":
            line = f"- {spec.label}：≥ {t:g} 分（{spec.prompt_label}）"
        elif unit_val == "ratio":
            line = f"- {spec.label}：≥ {t:g}（{spec.prompt_label}）"
        else:
            line = f"- {spec.label}：阈值 {t:g}（{spec.prompt_label}）"
        if spec.required:
            line += " —— **硬指标**"
        lines.append(line)
    if not lines:
        return ""
    return (
        "合格线 = **无实质硬伤、可直接连载**（不是「惊艳」）。"
        "硬指标不达标会触发整窗回退重写；软维度不达标只告警。\n" + "\n".join(lines)
    )


def _render_route_track(nodes: list[dict], window: tuple[int, int]) -> str:
    """渲染弧线轨迹：全书节点成长序列 + 标出窗口位置。

    只给轨迹不给位置，写手/评委都不知道"现在到哪一档"；只给当前档，
    又看不到"前面是什么、后面去哪"（于是把有序推进误判为突变）。
    两者都要。
    """
    if not nodes:
        return ""
    lo, hi = window
    rows: list[str] = []
    for node in nodes:
        nlo, nhi = _parse_range(node.get("chapter_range"))
        mb = node.get("main_branch") or {}
        growth = str(mb.get("growth") or "").strip()
        title = str(mb.get("title") or "").strip()
        if not growth and not title:
            continue
        mark = ""
        if nlo is not None and nhi is not None and lo and hi and not (hi < nlo or lo > nhi):
            mark = " ← **本窗口在此档**"
        elif nlo is not None and nhi is not None and hi and lo and nhi < lo:
            mark = "（已过）"
        elif nlo is not None and nhi is not None and lo and nlo > hi:
            mark = "（后续）"
        head = f"- {node.get('id', '?')}（{node.get('chapter_range', '?')}）"
        if str(node.get("milestone") or "").strip():
            head += f" 里程碑：{str(node['milestone']).strip()}"
        rows.append(f"{head}{mark}")
        if title:
            rows.append(f"    主线：{title}")
        if growth:
            rows.append(f"    成长：{growth}")
    if not rows:
        return ""
    return (
        "弧线按下列档位**有序推进**；本窗口处于标注档。\n" + "\n".join(rows)
    )


def _render_chapter_intent(
    subline_md: str,
    *,
    chapter_num: int = 0,
    route_result: str = "",
    route_title: str = "",
) -> str:
    """章级设计意图：支线目标 + **本章**钩子设计 + **本章**情节点 + 主线结果预期。

    2026-09-18：钩子/情节点两段改走 ``chapter_contract`` 按**本章**切分（此前整段
    注入后按 400/500 字截断 ⇒ 细纲按章供给时评委只看得到前几章、看不到本章；
    细纲按阶段供给时又只看到前几个阶段）。三端共用同一实现。

    2026-09-18（M1/D2）：新增**本章强度档位**。这是"整体有起伏、单章可以放松、
    部分注水不影响质量"的落点——档位是判据的**参照系**：
    标了 ``垫片``/``日常`` 的章，评委就不该按高潮尺判它"没爆点/没钩子"。
    ⚠ 档位缺失（老数据 / 规划未标）时**不渲染该行**，判据回到通用口径
    （不放松也不收紧），保证"为兼容历史开的口子只挡历史"（纪律 #4）。
    """
    from agent.core.story.chapter_contract import (
        HOOKS_SECTION,
        PACE_TIER_BY_NAME,
        POINTS_SECTION,
        pace_tier_of,
        select_chapter_lines,
    )

    parts: list[str] = []
    goal = _md_section(subline_md, "支线目标")
    if goal:
        parts.append(f"- 支线目标：{goal[:300]}")
    if route_title or route_result:
        parts.append(f"- 主线方向：{route_title}｜结果预期：{route_result}"[:300])
    # ---- 本章强度档位（D2）：规划端登记，写手与评委共用同一把尺 ----
    tier_name = pace_tier_of(subline_md, chapter_num) if chapter_num else ""
    tier = PACE_TIER_BY_NAME.get(tier_name)
    if tier is not None:
        line = (
            f"- **本章强度档位：{tier.name}**（{tier.label}；"
            f"张力 {tier.tension_lo:g}-{tier.tension_hi:g}；"
            f"章内要求：{tier.chapter_req}）"
        )
        if tier.relaxed:
            # 放松档 = "注水章合法化"的载体：**必须先声明本档允许放松**，
            # 否则评委仍会拿高潮尺判它注水（这正是死循环的成因）。
            line += (
                "\n  ★ 本档位为规划登记的**放松章**：允许无强钩子、允许舒展的节奏，"
                "**不得**按高潮章的爆点/钩子标准判它注水或不达标；"
                "但仍须服务本章目标情绪。"
            )
        parts.append(line)
    hooks = select_chapter_lines(subline_md, HOOKS_SECTION, chapter_num=chapter_num)
    if hooks:
        label = "本章钩子设计" if chapter_num else "钩子设计（按压力阶段）"
        parts.append(f"- {label}：{hooks[:400]}")
    pts = select_chapter_lines(subline_md, POINTS_SECTION, chapter_num=chapter_num)
    if pts:
        label = "本章情节点" if chapter_num else "情节点序列"
        parts.append(f"- {label}：{pts[:500]}")
    conflicts = _md_section(subline_md, "关键冲突")
    if conflicts:
        parts.append(f"- 关键冲突：{conflicts[:240]}")
    return "\n".join(parts)


def _render_setting_facts(project_dir: Path) -> str:
    """设定真源：world.md 冻结段（境界体系/金手指）+ 设定台账 + 已知冲突。

    顺序刻意把 **台账/冲突** 放在冗长的世界观散文**之前**：本块有字符预算，
    截断总发生在尾部——把最该被评委看见的「已确立条目」放前面，
    才不至于被后面的设定散文挤掉（"注入了但被截断"等于没注入）。
    """
    parts: list[str] = []
    world = project_dir / "world.md"
    if world.exists():
        try:
            content = world.read_text(encoding="utf-8")
        except OSError as e:
            degrade("design_brief.setting", "world.md 读取失败，设定真源缺失", e)
            content = ""
        realm = _md_section(content, "修炼境界体系").strip()
        if realm:
            parts.append(f"【设定真源·境界体系（冻结）】{realm[:700]}")
    try:
        from agent.core.story.setting_canon import SettingCanon

        canon = SettingCanon.load(project_dir)
        rendered = canon.render_for_prompt(limit=30)
        conflicts = canon.render_conflicts(limit=8)
        if rendered:
            # canon 自带小标题时不再叠加（避免"台账"标题出现两次，白耗预算）
            head = "" if rendered.lstrip().startswith("【") else "【设定台账（已确立，禁止重新发明）】\n"
            parts.append(f"{head}{rendered}")
        if conflicts:
            parts.append(f"【设定已知冲突（写作时避免扩大）】\n{conflicts}")
    except Exception as e:  # noqa: BLE001 - 台账不可用 → 该块缺失，显性降级
        degrade("design_brief.setting", "设定台账不可用，设定真源不完整", e)
    if world.exists():
        try:
            content = world.read_text(encoding="utf-8")
        except OSError:
            content = ""  # noqa: SILENT_DEGRADE reason=best-effort ref=20260916_角色弧光规格未建立与设计内转变被误判.md
        golden = _md_section(content, "金手指登记").strip()
        if golden:
            parts.append(f"【设定真源·金手指登记】{golden[:600]}")
    return "\n\n".join(parts)


def _render_character_facts(project_dir: Path) -> str:
    """角色真源：内核（动机/弧光/秘密）+ 关系演变 + 语言指纹 + 权威状态。

    ★ 弧光与关系是**设计意图**，此前仅出现在角色档案里、从未到达评委
    （``reader_appeal._gather_canon`` 只抽 状态/时间线/基础）——本函数补上。
    """
    chars_dir = project_dir / "characters"
    if not chars_dir.exists():
        return ""
    rows: list[str] = []
    for path in sorted(chars_dir.glob("*.md"))[:8]:
        try:
            card = path.read_text(encoding="utf-8")
        except OSError as e:
            degrade("design_brief.characters", f"角色档案 {path.name} 读取失败", e)
            continue
        bits: list[str] = []
        basis = _md_section(card, "基础")
        if basis:
            bits.append("基础：" + "；".join(
                ln.strip("- ").strip() for ln in basis.splitlines() if ln.strip()
            )[:220])
        kernel = _md_section(card, "内核")
        if kernel:
            bits.append("内核：" + kernel[:600])
        relations = _md_section(card, "关系")
        if relations:
            bits.append("关系（演变方向）：" + relations.replace("\n", "").strip()[:300])
        fingerprint = _md_section(card, "语言指纹")
        if fingerprint:
            bits.append("语言指纹：" + "；".join(
                ln.strip("- ").strip() for ln in fingerprint.splitlines() if ln.strip()
            )[:200])
        # 权威状态（生死/时间线）：无结构化段落时按关键词兜底（与写手端同口径）
        status = _md_section(card, "状态", "当前状态", "生死", "存活状态")
        if not status:
            if re.search(r"已故|去世|死亡|牺牲|阵亡|陨落|辞世", card):
                status = "（档案正文提及已故/牺牲，按已故处理）"
            elif re.search(r"在世|存活|健在", card):
                status = "（档案正文提及在世/存活）"
        if status:
            bits.insert(0, f"权威状态：{status[:160]}")
        if bits:
            rows.append(f"- **{path.stem}**：" + "\n  ".join(bits))
    if not rows:
        return ""
    return (
        "【角色真源（含设计意图：内核/弧光/关系演变；判定人设/设定时以此为准，"
        "但**沿此处登记的弧光推进不算崩坏**）】\n" + "\n".join(rows)
    )


def _render_design_expectation(nodes: list[dict], window: tuple[int, int], chapter_num: int) -> str:
    """落盘期望：本窗口档位的成长条目 → 转成"应落盘为什么新状态"的核对清单。"""
    lo, hi = window
    target = None
    for node in nodes:
        nlo, nhi = _parse_range(node.get("chapter_range"))
        if nlo is not None and nhi is not None and (not lo or not hi or not (hi < nlo or lo > nhi)):
            target = node
            break
    if target is None and nodes:
        target = nodes[-1]
    if target is None:
        return ""
    mb = target.get("main_branch") or {}
    growth = str(mb.get("growth") or "").strip()
    lines: list[str] = []
    if growth:
        lines.append(f"- 档位成长条目：{growth}")
        # 拆出「境界：/心性：/能力：」等分项，逐项提示落盘
        for seg in re.split(r"[；;]", growth):
            seg = seg.strip()
            if "：" in seg or ":" in seg:
                key, val = re.split(r"[:：]", seg, maxsplit=1)
                val = val.strip()
                if "→" in val:
                    lines.append(
                        f"  · {key.strip()}：正文若已推进到「{val.split('→')[-1].strip()}」，"
                        f"应落盘为新状态（后续章节以此为准，不再以初始值展示）"
                    )
    lines.append(
        f"- 核对口径：第 {chapter_num} 章及本窗口正文中**实际发生**的推进才落盘；"
        f"设计期望但正文未写到的，不得提前结转。"
    )
    return "\n".join(lines)


def _guess_chapter_num(root: Path) -> int:
    """从 ``chapters/chNNN.md`` 文件名派生"当前章"。

    刻意**不读** ``.state/state.json``：那是受监管状态，业主是
    ``core/engine/state_machine.py``（红线 ``test_state_ownership`` 会拦下
    越权读取）。而这里需要的只是"文本写到第几章"，章节文件名就是最直接的
    真源——评委取样的本来也是这些文件。
    """
    best = 0
    try:
        for path in (root / "chapters").glob("ch*.md"):
            m = re.search(r"ch(\d+)", path.stem)
            if m:
                best = max(best, int(m.group(1)))
    except OSError as e:
        degrade("design_brief.chapters", "chapters 目录不可读，章号退回推断值", e)
    return best or 1


def _guess_subline_dir(root: Path) -> Path | None:
    """推断"当前支线"目录：取 ``subline.md`` 最近被写入的那条。

    同样不读 ``state.json``。写手侧（``m5_context``）会显式传入准确值，
    本函数只服务于评委/落盘这类"没有状态机上下文"的消费方；
    取最近活跃的一条是最贴近事实的近似（新支线开写必然刚被写过）。
    """
    base = root / "sublines"
    if not base.is_dir():
        return None
    cands = [p for p in base.iterdir() if (p / "subline.md").is_file()]
    if not cands:
        return None
    try:
        return max(cands, key=lambda p: (p / "subline.md").stat().st_mtime)
    except OSError as e:
        degrade("design_brief.subline", "支线目录 mtime 不可读，按名称推断", e)
        return sorted(cands)[-1]


def build_design_brief(
    project_dir: str | Path,
    chapter_num: int | None = None,
    *,
    eval_window: int | None = None,
    subline_id: str = "",
) -> DesignBrief:
    """装配设计产出摘要（只读；任一真源缺失 → 对应块为空，绝不抛异常）。

    Args:
        project_dir: 小说项目根目录。
        chapter_num: 当前章号；``None`` 时从 ``chapters/chNNN.md`` 文件名推断
            （**不读 state.json**——那是受监管状态，业主是状态机）。
        eval_window: 取样/回滚窗口章数；``None`` 时取 SSOT ``EVAL_WINDOW_CHAPTERS``。
        subline_id: 当前支线；为空时取最近活跃的支线目录。

    Returns:
        :class:`DesignBrief`。``empty`` 为真表示所有真源都不可用（调用方跳过注入）。
    """
    root = Path(project_dir)
    plan: dict[str, Any] = {}
    plan_file = root / ".state" / "plan.json"
    if plan_file.exists():
        try:
            loaded = json.loads(plan_file.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                plan = loaded
        except Exception as e:  # noqa: BLE001 - 规划缺失 → 轨迹/判据为空，显性降级
            degrade("design_brief.plan", "plan.json 解析失败，设计轨不可用", e)
    else:
        # 缺文件与解析失败同责：轨迹/判据/落盘期望都会静默变空。
        # 「只设计不通知」的反面同样成立——「没设计也不通知」必须显性化，
        # 否则调用方以为三端可达、实则评委只拿到半份真源。
        degrade(
            "design_brief.plan",
            "plan.json 不存在：弧线轨迹/达标判据/落盘期望均不可用"
            "（该项目可能早于规划轨；三端可达不完整）",
        )

    if chapter_num is None:
        chapter_num = _guess_chapter_num(root)
    try:
        chapter_num = max(1, int(chapter_num))
    except (TypeError, ValueError):  # noqa: SILENT_DEGRADE reason=expected-skip
        chapter_num = 1

    if eval_window is None:
        try:
            from agent.core.quality.dimension_registry import EVAL_WINDOW_CHAPTERS

            eval_window = EVAL_WINDOW_CHAPTERS
        except Exception:  # noqa: SILENT_DEGRADE reason=optional-feature ref=20260916_角色弧光规格未建立与设计内转变被误判.md
            eval_window = 5
    win = max(1, int(eval_window or 1))
    window = (max(1, int(chapter_num) - win + 1), int(chapter_num))

    nodes = ((plan.get("route") or {}).get("nodes")) or []
    if not isinstance(nodes, list):
        nodes = []

    # ---- 章级设计意图：取当前支线（显式优先，缺省按活跃度推断）----
    subline_md = ""
    if subline_id:
        cand = root / "sublines" / subline_id / "subline.md"
        if cand.is_file():
            try:
                subline_md = cand.read_text(encoding="utf-8")
            except OSError as e:
                degrade("design_brief.subline", f"支线 {subline_id} 读取失败", e)
    if not subline_md:
        guessed = _guess_subline_dir(root)
        if guessed is not None:
            try:
                subline_md = (guessed / "subline.md").read_text(encoding="utf-8")
            except OSError as e:
                degrade("design_brief.subline", "推断支线读取失败，章级意图缺失", e)

    # 窗口所在路线节点的主线方向（复用与写手端同一套范围匹配）
    route_title = route_result = ""
    for node in nodes:
        nlo, nhi = _parse_range(node.get("chapter_range"))
        if nlo is not None and nhi is not None and nlo <= int(chapter_num) <= nhi:
            mb = node.get("main_branch") or {}
            route_title = str(mb.get("title") or "")
            route_result = str(mb.get("result") or "")
            break

    return DesignBrief(
        chapter_num=int(chapter_num),
        window=window,
        rubric=_clip(render_quality_rubric(plan.get("quality_targets")), "rubric"),
        route_track=_clip(_render_route_track(nodes, window), "route_track"),
        chapter_intent=_clip(
            _render_chapter_intent(
                subline_md,
                chapter_num=int(chapter_num),
                route_title=route_title,
                route_result=route_result,
            ),
            "chapter_intent",
        ),
        setting_facts=_clip(_render_setting_facts(root), "setting_facts"),
        character_facts=_clip(_render_character_facts(root), "character_facts"),
        design_expectation=_clip(
            _render_design_expectation(nodes, window, int(chapter_num)), "expectation"
        ),
    )
