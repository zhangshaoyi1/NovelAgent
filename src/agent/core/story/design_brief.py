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
    "DESIGN_EXEMPTION_PACE",
    "CARRIED_TO_JUDGE",
    "CARRIED_TO_PERSIST",
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
    #: M4：窗口逐章档位块。行数 = 窗口章数（行由代码生成、单行有界），
    #: 预算按 eval_window=20 的极端值给足，正常（5 章）永不触发截断。
    "window_pace_tiers": 1200,
    "setting_facts": 2800,
    "character_facts": 2000,
    "expectation": 700,
    "canon": 2400,
    #: 承接=（章首状态结转）：事实/信息差/未闭环/上一章交接的有界投影。
    #: 预算控制注入体量；为空（无账本）时块整体为空。
    "carry": 1200,
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

#: 强度维判定前提（M4，2026-09-18）——**仅在窗口内确有登记档位时**才追加。
#:
#: ★ 为什么单独一条、且**条件追加**而不是并进 :data:`DESIGN_EXEMPTION`：
#:   - 「设计内转变免罪」讲的是**内容变化**（性格/境界/能力/关系沿轨推进）；
#:     「规划内放松 ≠ 注水」讲的是**节奏强度**（本章规划上就该平淡）。
#:     两者判据不同，混在一条里会让评委把"放松"误读成"内容变了也免罪"。
#:   - 老数据**无**档位 ⇒ 本条**不出现** ⇒ 评委端文本逐字等同改造前
#:     （纪律 #4：新机制只对新数据生效，历史路径零改动）。
#:
#: ★ 本条措辞刻意**不点名**任何具体档位：哪些档算"放松"由
#:   :data:`DESIGN_EXEMPTION_PACE` 引用的【本窗口各章强度档位】块给出，
#:   该块由 ``chapter_contract.PaceTier.relaxed`` 派生——提示词里若抄一份
#:   档位名单，一次改名即双向破裂（纪律 #19）。
DESIGN_EXEMPTION_PACE = (
    "【判定前提·规划内的放松章 ≠ 注水】上面【本窗口各章强度档位】是规划端登记的"
    "**节奏安排**：登记为**放松章**的那些章，节奏舒展、无强钩子、无爆点"
    "属**规划内的放松**，不得按高强度章的爆点/钩子标准判它们注水或追读力不达标；"
    "仍须服务该章登记的章内要求。仅当**偏离**登记档位才计 issue"
    "（登记放松却通篇无信息增量、或登记高强度却写得平淡）。"
    "未给出强度档位的章，按原标尺判，不放松也不收紧。"
)

#: 承接=（章首状态结转）——评委端对齐判定的**权威起点**（2026-09-21，三端供给收口）。
#:
#: ★ 为什么需要这条：评委此前对齐「设定一致/人设稳定」只能靠被污染的正典
#:   （``setting_canon.render_for_prompt(limit=30)`` 取最早最脏条目）与路线级弧线
#:   （60–120 章一个节点，太粗）。而**上一章结算结转的状态**（事实/信息差/未闭环/
#:   章末交接）才是本章开篇的**真实连续锚点**——写手早就拿到它
#:   （``m5_context`` 的 ``continuity_projection``），评委/落盘两端此前拿不到
#:   （ch15 实证：正文按承接演进而被判与"旧台账"冲突）。
#:
#:   ⇒ 这就是「章际承接」把「设定一致/人设稳定」从"结构无解"变成"可对齐"的钥匙：
#:   沿承接状态连续演进 = 合格；与承接状态冲突/断裂才计 issue。
CARRIED_TO_JUDGE = (
    "【本章开篇承接状态（承接=）】以下为上一章结算结转下来的**权威状态**"
    "（事实/信息差/未闭环剧情线/上一章章末交接）。判定『设定一致』『人设稳定』"
    "『逻辑漏洞』时，以此承接状态为对齐**起点**：本章角色境界/心性/关系/资源若与"
    "承接状态**连续演进**（承接=1 且本章推进），不算前后矛盾/设定冲突；"
    "只有与承接状态**冲突或断裂**（倒退回旧值、无契机跳变）才计 issue。"
    "承接状态为空时，再回落设定台账/角色档案核验。"
)

#: 承接=（章首状态结转）——落盘端结转核对基线（2026-09-21）。
#:
#: 落盘端此前只写不读：`m5_persist` 章尾确实 commit 进 ``ContinuityLedger``
#: （承接的**写**），但没有把「上一章开了什么」拿回来对账（承接的**读**）。
#: 落盘时应以承接=为基线核对：本章是否把承接中未闭环的剧情线推进/关闭、
#: 是否延续了交接的 must_carry。核对结果随事实一起结账，下一章才能拾起。
CARRIED_TO_PERSIST = (
    "【承接=（本章开篇结转清单，写完后核对）】以下为本章开篇从上一章结转的状态。"
    "落盘时逐条核对：未闭环剧情线被推进/关闭了吗？信息差被反转了吗？交接的必带项"
    "延续了吗？**只把正文实际发生的变化结账**，未发生的保持承接原值，不得提前推进。"
)

#: 承接=（章首状态结转）——写手端起点的**不可突破锚点**（2026-09-21，三端供给补完）。
#:
#: ★ 为什么这条被漏掉：承接=初版只补给评委/落盘两端（judge/persist），写手端误以为
#:   ``m5_context`` 已有 ``continuity_projection`` 就够了。但投影是**说明性的**（"当前
#:   有哪些状态"），没有给写手**硬约束力**（"这些是**权威起点**，只能从它们连续演进，
#:   不得直接无契机跳变"）。ch25 实证：写手看不到"承接=引灵中期"，在引灵/栖气期边界
#:   连续跳了两级冲到淳真期初期，且把"单一属性/30%/三次"规则直接升级为"全属性/50%/
#:   五次"，完全脱离承接 ⇒ 被评委判设定一致=3/逻辑漏洞=2（真实违约）。
#:
#:   ⇒ 写法与 judge 版同源同字段（同 ``self.opening_state``），但措辞面向"产出侧"：
#:   强调**境界/设定/关系的连续演进**、禁止**无契机的越级跳变**、规则升级须有触发源。
CARRIED_TO_WRITER = (
    "【本章开篇承接状态（承接=，写手起点）】以下为上一章结转下来的**权威章首状态**"
    "（境界/心性/关系/资源/未闭环剧情线/上一章章末交接）。本章写作**必须以此为连续起点**："
    "境界/功法规则/关系等只能从承接值**连续演进**（有明确触发：突破契机/闭关/机缘/真源佐证）；"
    "不得**无契机的越级跳变**（如上一章还在引灵期边界、本章直接冲到更高境界），"
    "不得**无登记地升级规则**（如吞噬属性数/效率/次数的变更须有触发源与新增设定登记），"
    "不得**自相矛盾地改时间标签**。承接状态为空时，再回落到设定台账/角色档案检索。\n"
    "【优先级仲裁（与「本章设计意图与阶段方向」冲突时以此为准）】设计供给里可能同时出现"
    "两类信息：①标注「本章」的（本章须落实）；②标注「跨多章」/「阶段方向」的（阶段级、"
    "**本章不必完成**）。两类冲突时一律**以本承接状态（承接=）为准**：\n"
    "- 本章境界**至多从承接值推进一境**；承接未给出境界时，不得凭空跳到更高境界；\n"
    "- 功法/规则**至多升级一次**，且必须给出本章内的触发事件（突破契机/闭关/机缘/真源佐证）；\n"
    "- 标注「跨多章」的阶段目标**不得在单章内完成**——本章只写它的一步，其余留给后续章节。\n"
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


#: 截断时**必须优先保全**的「判定前提」行前缀。
#:
#: 2026-09-18（M1 端到端跑闸发现，纪律 #4「为兼容历史开的口子必须只挡历史」的反例）：
#: 「本章强度档位」是**判据的参照系**——评委据此决定用高潮尺还是放松尺。
#: 而 ``chapter_intent`` 块有 1200 字预算，此前契约行是**整段文本**（一章一行、
#: 单行常 300–500 字）⇒ 靠前的钩子/情节点两段就能把预算吃满 ⇒ 档位声明被
#: ``_clip`` 从**尾部**切断 ⇒ 「注入了但被截断」＝**没注入**，评委仍按高潮尺判
#: 放松章注水，死循环原样复现（D2 失效）。
#:
#: 故给正式档案（旧数据无档位 ⇒ 无此锚 ⇒ 行为与修复前一致）一个**守卫锚**：
#: 截断发生时，先把这些行整体保留，再从剩余预算里装正文。
_INTENT_KEEP_PREFIXES: tuple[str, ...] = ("- **本章强度档位：",)


def _clip(text: str, key: str) -> str:
    limit = _BUDGET.get(key, 1000)
    if len(text) <= limit:
        return text
    kept = [ln for ln in text.splitlines() if ln.startswith(_INTENT_KEEP_PREFIXES)]
    if not kept:
        return text[:limit] + "…（已截断）"
    head = "\n".join(kept)
    budget = limit - len(head) - 8  # 8 = 省略标记与换行的余量
    if budget <= 0:  # 守卫行本身超预算（理论不可达）⇒ 不硬塞，退回原行为
        return text[:limit] + "…（已截断）"
    body = "\n".join(ln for ln in text.splitlines() if not ln.startswith(_INTENT_KEEP_PREFIXES))
    return f"{head}\n{body[:budget]}…（已截断；判定前提已保全）"


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
    #: 本章强度档位（M3：写手侧平权规则分叉的**唯一信号源**）。
    #: 由 ``build_design_brief`` 从 ``chapter_contract.pace_tier_of`` 取，
    #: 严禁各端自行解析细纲（红线 ``test_design_brief_reaches_all_consumers``）。
    #: 老数据/未标档位 ⇒ 空串 ⇒ 写手侧走原规则（纪律 #4）。
    pace_tier: str = ""
    #: 本章档位是否**放松档**（``PaceTier.relaxed``，真源在 ``chapter_contract``）。
    #: ★ 写手提示词只消费**这个布尔**，不消费档位名 —— 否则"哪些档算放松"
    #: 会在提示词里出现第二份真源，改名即双向破裂（纪律 #19）。
    pace_relaxed: bool = False
    #: 窗口内**逐章**强度档位（M4：评委端判定参照系，渲染好的文本块）。
    #: ★ **只进评委端**：写手一次只写一章，给它整窗档位既无用又剧透
    #: （纪律 #18：供给必须定义作用域）。老数据无档位 ⇒ 空串 ⇒ 不渲染。
    window_pace_tiers: str = ""
    setting_facts: str = ""      # 设定真源（冻结段 + 台账 + 已知冲突）
    character_facts: str = ""    # 角色真源（内核/动机/弧光/关系/语言指纹）
    design_expectation: str = ""  # 落盘期望（本章设计上应推进的状态）
    opening_state: str = ""      # 承接=（上一章结转的权威章首状态：事实/信息差/未闭环/交接）

    @property
    def empty(self) -> bool:
        return not any(
            (self.rubric, self.route_track, self.chapter_intent,
             self.setting_facts, self.character_facts, self.design_expectation,
             self.opening_state)
        )

    # ---------------------------------------------------------- 三端渲染
    def render_for_writer(self) -> str:
        """写手端：承接=（章首权威状态）+ 本章设计意图 + 弧线轨迹 + 达标判据。

        「只设计不通知」的直接对症：写手必须知道**上一章结转的权威章首状态**
        （只此基础上连续演进）、**设计上本章要表现什么**、**弧线这一跳是设计好的**、
        **批末体检拿什么尺子量**。承接=（:data:`CARRIED_TO_WRITER`）放最前，
        是写作的连续起点，防止写手无契机的越级跳变/无登记规则升级。
        """
        blocks: list[str] = []
        # 承接=（章首权威状态）放**最前**：它是写作的连续起点，必须最先可见、
        # 最不易被预算挤出（"注入了但被截断"＝没注入）。
        if self.opening_state:
            blocks.append(CARRIED_TO_WRITER + "\n" + self.opening_state)
        if self.chapter_intent:
            # ★ 2026-09-24：标题此前是「本章须落实；不得自行改道」——而块里混排了
            #   **阶段级**（跨多章）的「支线目标/主线方向」（见 ``_render_chapter_intent``），
            #   写手只能服从"本章须落实" ⇒ 把跨多章的阶段目标挤进一章内
            #   （灵荒工坊 ch41 实证：引灵→淳真一章内连跳两境）。
            #   标题改中性，并显式声明"标注「本章」的须落实、标注「跨多章」的禁止单章完成"，
            #   与 :data:`CARRIED_TO_WRITER` 的「不得无契机的越级跳变」形成**同一口径**。
            blocks.append(
                "【本章设计意图与阶段方向（规划端已登记；标注「本章」的须落实，"
                "标注「跨多章」的禁止在单章内完成）】\n"
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
        # 承接=（章首权威状态）放**最前**：它是判定一致性的对齐起点，必须最先可见、
        # 最不易被预算挤出（"注入了但被截断"＝没注入）。
        if self.opening_state:
            blocks.append(CARRIED_TO_JUDGE + "\n" + self.opening_state)
        # 档位参照系：它是其余判据的尺子，且本块整体有预算，
        # 放在后面没有额外收益、只有被挤出 prompt 的风险（"注入了但被截断"＝没注入）。
        if self.window_pace_tiers:
            blocks.append(self.window_pace_tiers)
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
            # 强度维前提**只在真有档位时**追加（纪律 #4：不给老数据加新前提）
            if self.window_pace_tiers:
                blocks.append(DESIGN_EXEMPTION_PACE)
        return "\n\n".join(blocks)

    def render_for_persist(self) -> str:
        """落盘端：本章设计上应推进的状态（供结转核对）。

        对应作者命题的「最终落盘」——弧线推进/境界提升/关系演变必须在写完后
        **记账**，后续章节以新状态为准，而不是永远拿初始档案说话。
        """
        if not self.design_expectation and not self.opening_state:
            return ""
        blocks: list[str] = []
        if self.opening_state:
            blocks.append(CARRIED_TO_PERSIST + "\n" + self.opening_state)
        if self.design_expectation:
            blocks.append(
                "【本章设计期望推进的状态（写完后核对：正文若已推进，应落盘为新状态）】\n"
                + self.design_expectation
            )
        return "\n\n".join(blocks)


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


def _intent_label(text: str, chapter_num: int, what: str) -> str:
    """给章级意图块挑一个**与证据等级相符**的标签（N1 修复）。

    ★ 这是「判据强度必须与证据匹配」（纪律 #20）在渲染层的落地：
      - 文本自带非本章标注（最近前文回退）⇒ 标签**必须**说明"非本章"；
      - 取不到任何章级信息（整段兜底）⇒ 标"按压力阶段/整段"；
      - 精确命中本章 ⇒ 才能标"本章"。
    一律标"本章"会让评委拿上一章的尺量本章 ⇒ 与 D2 档位串档同型的**参照系错位**。
    """
    from agent.core.story.chapter_contract import (
        NON_CURRENT_LABEL_SUFFIX,
        _is_marked_as_non_current,
    )

    if not chapter_num:
        return f"{what}（按压力阶段）"
    if _is_marked_as_non_current(text):
        # 标签后缀取自真源（不得硬编码——两份字面量改名即双向破裂，纪律 #19）
        return f"{what}{NON_CURRENT_LABEL_SUFFIX}"
    if re.match(rf"^\s*第\s*{int(chapter_num)}\s*章\s*[：:]", text):
        return f"本章{what}"
    return f"{what}（按压力阶段/整段，非本章精确契约）"


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
    规划登记为**放松档**的章，评委就不该按高强度章的标准判它"没爆点/没钩子"。
    ⚠ 具体哪些档算放松由 ``chapter_contract.PaceTier.relaxed`` 定义，本文件
    **不写档位名单**（纪律 #19：一次改名即双向破裂）。
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
        # ★ 2026-09-24：**阶段级**（跨多章）目标必须自带粒度标注，否则写手会把它
        #   当成"本章须完成"，一章内做完整条支线（含境界连跳）。同函数下方的钩子/
        #   情节点早已按章切分并通过 ``_intent_label`` 标注证据等级，这两行是漏网之鱼。
        #   ⚠ 标注措辞刻意**避开**「非本章」三字：那是
        #   ``chapter_contract.NON_CURRENT_LABEL_SUFFIX`` 的既有专义（"回退到最近前文、
        #   证据弱"），复用会造出两个同词异义的参照系（纪律 #20 参照系错位），
        #   且粒度过粗的「跨多章」本身已把意思说全。
        parts.append(f"- 【阶段方向·跨多章｜本章不必完成】支线目标：{goal[:300]}")
    if route_title or route_result:
        line = f"主线方向：{route_title}｜结果预期：{route_result}"
        parts.append(f"- 【阶段方向·跨多章｜本章不必完成】{line[:300]}")
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
        # ★ 标签必须反映**实际证据等级**（N1）：`select_chapter_lines` 可能回退
        #   到「最近前文」（带非本章标注）或整段。若一律标"本章"，评委就会拿
        #   上一章的钩子当本章标准 ⇒ **参照系错位**（与 D2 档位串档同型缺陷）。
        label = _intent_label(hooks, chapter_num, "钩子设计")
        parts.append(f"- {label}：{hooks[:400]}")
    pts = select_chapter_lines(subline_md, POINTS_SECTION, chapter_num=chapter_num)
    if pts:
        label = _intent_label(pts, chapter_num, "情节点")
        parts.append(f"- {label}：{pts[:500]}")
    conflicts = _md_section(subline_md, "关键冲突")
    if conflicts:
        parts.append(f"- 关键冲突：{conflicts[:240]}")
    return "\n".join(parts)


def _render_window_pace_tiers(tiers: list[tuple[int, Any]]) -> str:
    """评委端判定参照系：**窗口内逐章**强度档位（M4，2026-09-18）。

    ★ 这是「整体有起伏、单章可以放松、部分注水不影响质量」在**质检侧**的落点。
      此前评委只拿得到 ``chapter_intent`` 里的「本章强度档位」（1 章），
      却要一次判 ``eval_window``（默认 5）章 —— 供给粒度 ≠ 消费粒度
      （纪律 #15 同型）⇒ 其余四章按高潮尺判 ⇒ 规划登记的放松章被判注水
      ⇒ 回退重写且**输入不变** ⇒ 死循环。

    ⚠ 「是否放松」一律由 ``PaceTier.relaxed`` 派生，**不在本文件写档位名单**
      （纪律 #19）：档位表改名/改档，本渲染自动跟随。
    """
    if not tiers:
        return ""
    rows: list[str] = []
    for num, tier in tiers:
        row = (
            f"- 第{num}章：**{tier.name}**（{tier.label}；"
            f"张力 {tier.tension_lo:g}-{tier.tension_hi:g}；{tier.chapter_req}）"
        )
        # ★ 直接读属性（不用 ``getattr(tier, "relaxed")``）：字符串键不受改名
        #   保护，且会绕过红线 ``test_relaxed_marks_are_derived_not_hardcoded``
        #   的派生关系检测（纪律 #19 要求判据是"成员/派生关系"）。
        if tier.relaxed:
            row += "  ← 放松章（规划登记，非注水）"
        rows.append(row)
    return (
        "【本窗口各章强度档位（**判定的参照系**：逐章按登记的档位判，"
        "不得整窗套用同一把尺）】\n" + "\n".join(rows)
    )


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


def _render_opening_state(project_dir: Path) -> str:
    """承接=（章首状态结转）：从连续性账本组装有界投影。

    与写手端 ``m5_context`` 注入的 ``continuity_projection`` **同源**
    （都走 ``core.continuity.project`` + ``project_to_text``），是"上一章结算
    结转下来的权威章首状态"。评委/落盘两端此前拿不到它 ⇒ 判定满足/设定一致
    只能靠被污染的正典 ⇒ 正文按承接演进而被判与旧台账冲突（ch15 实证）。

    只读账本、失败降级为空串（不阻断）；账本虽在 ``core.continuity``，本模块
    延迟导入以避免 import-time 循环（本模块属 ``core.story``，仅依赖 base+client）。
    """
    try:
        from agent.core.continuity import (
            ContinuityLedgerStore,
            project,
            project_to_text,
        )

        ledger = ContinuityLedgerStore(project_dir)
        ledger.load()
        if not ledger.has_any():
            return ""
        return project_to_text(project(ledger))
    except Exception as e:  # noqa: BLE001 - 承接投影失败→该块为空，显性降级
        degrade(
            "design_brief.carry",
            "连续性账本承接投影失败，承接=缺失（判定一致性对齐起点缺失）",
            e,
        )
        return ""


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

    # ---- M3：本章强度档位（写手侧平权规则分叉的唯一信号源）----
    # 与 ``_render_chapter_intent`` 内渲染用的档位**同源**（都走 pace_tier_of），
    # 冗余解析由红线 ``test_pace_tier_matches_rendered_intent`` 做机器交叉核对。
    from agent.core.story.chapter_contract import (
        PACE_TIER_BY_NAME,
        pace_tier_of,
        pace_tiers_of_window,
    )

    _tier = pace_tier_of(subline_md, int(chapter_num)) if int(chapter_num) else ""
    _tier_obj = PACE_TIER_BY_NAME.get(_tier)
    # ---- M4：评委端参照系 = 窗口内**逐章**档位（只进评委端，不进写手端）----
    # 与本章档位**同源**（都走 pace_tier_of），冗余解析由红线
    # ``test_window_tiers_match_pace_tier_of`` 做机器交叉核对。
    _window_tiers = pace_tiers_of_window(subline_md, window) if subline_md else []

    return DesignBrief(
        chapter_num=int(chapter_num),
        window=window,
        window_pace_tiers=_clip(
            _render_window_pace_tiers(_window_tiers), "window_pace_tiers"
        ),
        rubric=_clip(render_quality_rubric(plan.get("quality_targets")), "rubric"),
        route_track=_clip(_render_route_track(nodes, window), "route_track"),
        pace_tier=_tier,
        pace_relaxed=bool(_tier_obj and _tier_obj.relaxed),
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
        opening_state=_clip(_render_opening_state(root), "carry"),
    )
