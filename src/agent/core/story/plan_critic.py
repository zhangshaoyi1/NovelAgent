"""规划评委（M5，2026-09-19）—— 规划层语义评审，**只记录不拦**（采样模式）。

定位
----
``plan_managers.py`` 是**确定性**审计（零 LLM 的四管理者：连续性/结构/债务/
角色），其开篇注释自认缺口：

    「语义类判断（"这条弧线是否合理推进传承线"）留给 LLM 管理者（未落地）」

本模块就是那个「未落地」的 LLM 管理者——**规划评委**。它与正文评委职责分离
（D5：独立于正文评委）：正文评委判"写出来的章好不好"，规划评委判"**还没写**的
规划合不合理"。

为什么先「只记录不拦」（D3 定稿，§8.3 关键纪律）
----------------------------------------------
规划评审**可以严格**（拦的是还没写的东西，无损失），但**不能立刻就严格**：

    ① 先「只记录不拦」采样（零风险）
    ② 跑一本新书前 20 章，统计两个量：
       · 误报率 = 判不合格中"实际没问题"的比例
       · ★ 历史达成率 = 该判据是否曾被达成过
    ③ 据 ①② 定档：哪些升 blocking，哪些留告警

⚠ 两条硬约束（源自本项目真实事故，纪律 #13）：
  - **历史达成率 < 50% 的判据不可升 blocking** —— 否则阈值变成"摧毁扳机"；
  - **不可达判据**先修判据本身，而非提高严格度。

故本模块 `Returns: 恒 []`（不产生致命错误）。保留返回值以与同族闸门同形，
并便于 M8 定档后升级为告警/拦截时**不改调用契约**。

与 ``plan_managers`` 的分工（避免重复报障）
------------------------------------------
=========================  ==========================  ==========================
维度                         plan_managers（确定性）      plan_critic（本模块）
=========================  ==========================  ==========================
弧线衔接/重叠                 ✅ BLOCK                    —
弧线空洞/越界                 ✅ WARN/BLOCK               —
高 urgency 叙事线零推进       ✅ WARN（点名匹配）          —
休眠实体无安排                ✅ WARN                      —
**弧线是否合理推进母题**      —（自认缺口）                ✅ **语义**（LLM 抽样）
**章级档位供给完整度**        —（采样闸在 plan_consistency）✅ 机器统计（零 LLM）
**段级/章级双级粒度**         —                           ✅ 机器统计（零 LLM）
=========================  ==========================  ==========================

留痕
----
每次评审写 ``.state/plan_critic.jsonl``（纯观测面）。
⚠ 落盘失败**不转致命**：本闸恒不阻断，留痕失败只降级为"观测缺失"，
若在此转致命就等于用"观测面失败"去阻断写作——动作强度超过判据（纪律 #2）。

依赖方向
--------
本模块属 ``agent.core.story``（领域层）：只依赖标准库 + 同层 ``chapter_contract``
（档位契约唯一真源）+ 同层 ``degrade``。**不依赖 workflows 层**（反向依赖会
造出环，且规划评委要被 workflows 与 daemon 两侧调用）。
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable

__all__ = [
    "CRITIC_LEDGER",
    "Finding",
    "PlanCriticReport",
    "judge_plan_structure",
    "judge_arc_semantics",
    "review_plan",
    "record_plan_critic",
    "read_plan_critic",
    "aggregate_readings",
]

#: 规划评委的采样台账（纯观测面；落盘失败不转致命）。
CRITIC_LEDGER = Path(".state") / "plan_critic.jsonl"

#: 评审级别（**刻意不复用 plan_managers 的 BLOCK**）：
#: 采样期本模块没有任何 blocking 能力，若沿用 "block" 字面量会让
#: "这条能不能拦住"在留痕里不可区分 ⇒ 下游统计误报率时把观测项当拦截项。
#: ⇒ 用独立枚举，且**制造性区别**：本模块恒不可能产出 BLOCK。
LEVEL_NOTE = "note"      # 记录用（样本内判据成立，但采样期不作为结论）
LEVEL_WARN = "warn"      # 值得作者/运维看一眼
LEVEL_UNREACHABLE = "unreachable"  # 判据自身不可达（须先修判据，纪律 #13）


@dataclass
class Finding:
    """一条规划评审发现（**恒非致命**）。"""

    level: str          # note | warn | unreachable
    judge: str          # 判据 id（供历史达成率统计做**稳定键**）
    manager: str        # 归属：semantic | granularity | rhythm | coverage
    message: str
    evidence: dict[str, Any] = field(default_factory=dict)


@dataclass
class PlanCriticReport:
    """规划评审报告（采样模式：不拦，只留痕）。"""

    current_chapter: int = 0
    scope: str = ""                          # 作用域描述（窗口/全支线）
    findings: list[Finding] = field(default_factory=list)
    reviewed_at: str = ""
    #: 本次是否真的跑了 LLM 语义评审（成本相关；未跑时要能区分"没问题"与"没审"）
    semantic_ran: bool = False
    #: 语义评审失败原因（None = 未尝试或成功）。★ 失败必须显性化（纪律 #1）
    semantic_error: str = ""

    @property
    def notes(self) -> list[Finding]:
        return [f for f in self.findings if f.level == LEVEL_NOTE]

    @property
    def warns(self) -> list[Finding]:
        return [f for f in self.findings if f.level == LEVEL_WARN]

    @property
    def unreachable(self) -> list[Finding]:
        return [f for f in self.findings if f.level == LEVEL_UNREACHABLE]

    def to_dict(self) -> dict[str, Any]:
        return {
            "current_chapter": self.current_chapter,
            "scope": self.scope,
            "reviewed_at": self.reviewed_at,
            "semantic_ran": self.semantic_ran,
            "semantic_error": self.semantic_error,
            "findings": [
                {
                    "level": f.level,
                    "judge": f.judge,
                    "manager": f.manager,
                    "message": f.message,
                    "evidence": f.evidence,
                }
                for f in self.findings
            ],
        }

    def render(self) -> str:
        if not self.findings:
            tail = "" if self.semantic_ran else "（语义评审未运行）"
            return f"规划评审（第 {self.current_chapter} 章后，{self.scope}）：无发现{tail}"
        lines = [
            f"规划评审（第 {self.current_chapter} 章后，{self.scope}）："
            f"WARN {len(self.warns)} / NOTE {len(self.notes)} / "
            f"UNREACHABLE {len(self.unreachable)}（**仅采样，不拦**）"
        ]
        for f in self.findings:
            lines.append(f"  [{f.level.upper()}][{f.manager}] {f.message}")
        if not self.semantic_ran:
            why = self.semantic_error or "未尝试"
            lines.append(f"  [NOTE][semantic] 语义评审未运行：{why}")
        return "\n".join(lines)


# ============================================================
# 判据 1（确定性 / 零 LLM）：段级 + 章级**双级**粒度
#
# ★ 为什么要有这条（方案 §8.2「段与章是两个刻度」）：
#   段级定"形状"（弧线），章级定"每一格的高度"（强度档位）。
#   只标段级 ⇒ 写手不知道"上升 5 章里哪章该强、哪章该弱" ⇒ **段内落差被填平**
#   ⇒ 即"95 章同阶段"问题的**微缩版**（纪律 #21 同型：缺口 ⇒ 静默失真）。
#
# ⚠ 判据强度必须与证据匹配（纪律 #20）：真实项目实测**6 本在写的书**
#   靠阶段级供给写成 150–350 章 ⇒「阶段级 ⇒ 不合格」不成立 ⇒ 本条只记 NOTE。
# ============================================================

def _extract_section(content: str, title: str) -> str:
    """取 ``## <title>`` 小节正文（委托 chapter_contract，禁止在此重写正则）。"""
    from agent.core.story.chapter_contract import extract_section

    return extract_section(content, title)


def _chapter_numbers_in(content: str, title: str) -> set[int]:
    """某小节内出现的逐章行章号集合。"""
    section = _extract_section(content, title)
    if not section:
        return set()
    return {int(n) for n in re.findall(r"第\s*(\d+)\s*章\s*[：:]", section)}


def _subline_chapter_span(content: str) -> tuple[int, int]:
    """细纲覆盖的章区间：取压力曲线表区间的最小起点/最大上界。

    解析不出返回 ``(0, 0)``（调用方据此不做区间过滤）。
    """
    spans = [
        (int(a), int(b))
        for a, b in re.findall(r"\|\s*(\d+)\s*-\s*(\d+)\s*\|", content)
    ]
    if not spans:
        return (0, 0)
    return (min(a for a, _ in spans), max(b for _, b in spans))


def judge_plan_granularity(subline_md: str, *, name: str = "") -> list[Finding]:
    """判据 G1：细纲是否为**段级 + 章级双级**供给（方案 §8.2）。

    作用域：整条支线（这是"规划产出质量"的判据，不是"窗口够不够"）。
    ⚠ 与 ``plan_consistency.check_subline_plot_source`` 的分工：那条判
      "**即将写的窗口内**有没有逐章行"（写前闸，fail-fast 级），本条判
      "**整条支线**的双级粒度"（规划质量，采样级）。两者作用域不同，
      故不重复报障（纪律 #18：闸门必须定义作用域）。
    """
    from agent.core.story.chapter_contract import (
        HOOKS_SECTION,
        POINTS_SECTION,
        TIERS_SECTION,
    )

    tag = name or "（未命名支线）"
    out: list[Finding] = []
    # 段级：压力曲线的四个阶段必须有内容
    curve = _extract_section(subline_md, "剧集压力曲线") or _extract_section(
        subline_md, "压力曲线"
    )
    stages = [s for s in ("铺垫", "冲突", "高潮", "舒缓") if s in curve]
    if len(stages) < 4:
        out.append(Finding(
            LEVEL_WARN, "G1.stage_level", "granularity",
            f"支线「{tag}」段级供给不完整：压力曲线只含 {len(stages)}/4 阶段"
            f"（缺 {'、'.join(s for s in ('铺垫','冲突','高潮','舒缓') if s not in stages)}）",
            {"stages_found": stages},
        ))
    # 章级：章级契约行 + 档位行
    hook_nums = _chapter_numbers_in(subline_md, HOOKS_SECTION)
    point_nums = _chapter_numbers_in(subline_md, POINTS_SECTION)
    tier_nums = _chapter_numbers_in(subline_md, TIERS_SECTION)
    chapter_level = hook_nums | point_nums
    if not chapter_level:
        lo, hi = _subline_chapter_span(subline_md)
        out.append(Finding(
            LEVEL_NOTE, "G1.chapter_level_absent", "granularity",
            f"支线「{tag}」（{lo}-{hi}）**只有段级**、无逐章行："
            f"段内落差会被填平（写手不知道哪章该强哪章该弱）。"
            f"⚠ 历史书的合法降级模式（实测 6 本靠阶段级写成 150-350 章），"
            f"故**只记 NOTE**，不作为不合格（纪律 #20）",
            {"span": [lo, hi], "hook_nums": sorted(hook_nums), "point_nums": sorted(point_nums)},
        ))
    elif not tier_nums:
        out.append(Finding(
            LEVEL_WARN, "G1.tier_level_absent", "granularity",
            f"支线「{tag}」有 {len(chapter_level)} 章逐章行但**零档位**："
            f"D2 要求「强度档位必填」⇒ 该支线 M2/M3/M4 三端参照系全部空转",
            {"chapter_level_count": len(chapter_level)},
        ))
    elif len(tier_nums & chapter_level) < len(chapter_level):
        missing = sorted(chapter_level - tier_nums)
        out.append(Finding(
            LEVEL_NOTE, "G1.tier_level_partial", "granularity",
            f"支线「{tag}」档位覆盖不全：{len(chapter_level)} 章逐章行中 "
            f"{len(missing)} 章未标档位（{'、'.join(f'第{n}章' for n in missing[:8])}"
            f"{'…' if len(missing) > 8 else ''}）",
            {"missing": missing},
        ))
    return out


# ============================================================
# 判据 2（确定性 / 零 LLM）：段内**峰值章 / 谷底章**是否显式标出
#
# ★ D11 定稿 C：「规划给段级骨架 + 标出峰值章/谷底章，其余章由波形规则推导」。
#   ⇒ 若规划**只给段级骨架、不标峰谷**，则"其余章由波形规则推导"这一环
#     在**供给端**就没有输入（系统只能猜）⇒ 同族纪律 #15/#22。
#
# ⚠ 采样级判据（证据不足）：本判据**从未在真实项目上验证过**，
#   且「峰谷必须显式标」是 D11 的**建议形态**而非既有事实 ⇒ 只记 NOTE。
# ============================================================

def judge_rhythm_peaks(subline_md: str, *, name: str = "") -> list[Finding]:
    """判据 G2：段内峰谷是否显式登记（D11 方案 C 的供给前提）。

    ⚠ 本判据的**可达性未知**（真实项目从未按 D11 产出过峰谷标注）⇒
      按纪律 #13，采样期只能记 NOTE，且**标注为不可达风险项**，
      供 M8 定档时优先核对"这条判据是否有人达成过"。
    """
    from agent.core.story.chapter_contract import PACE_TIER_NAMES

    tag = name or "（未命名支线）"
    out: list[Finding] = []
    tide = _extract_section(subline_md, "章节强度档位")
    if not tide:
        return out  # 无档位小节 ⇒ 归 G1 报障，不重复
    tiers = [
        m.group(1)
        for m in re.finditer(r"第\s*\d+\s*章\s*[：:]\s*([^\n]+)", tide)
    ]
    vals = [next((t for t in PACE_TIER_NAMES if t in ln), "") for ln in tiers]
    vals = [v for v in vals if v]
    if len(vals) < 4:
        return out  # 样本太短，无法判断有无起伏
    if len(set(vals)) == 1:
        out.append(Finding(
            LEVEL_WARN, "G2.flat_tier_sequence", "rhythm",
            f"支线「{tag}」档位序列**全程同一档**（{vals[0]}，{len(vals)} 章）："
            f"段内零落差 ⇒ 这正是「95 章同阶段」问题的章级微缩版",
            {"tier": vals[0], "count": len(vals)},
        ))
    return out


# ============================================================
# 判据 3（语义 / LLM 抽样）：弧线是否**合理推进**母题/传承线
#
# ★ 这条正是 ``plan_managers.py:11`` 自认的缺口：
#   「语义类判断（"这条弧线是否合理推进传承线"）留给 LLM 管理者（未落地）」
#
# ⚠ 采样模式：本判据**只产 Finding，不产致命错误**。
#   且 LLM 失败必须显性化（semantic_error），不得静默当成"通过"（纪律 #1）。
# ============================================================

#: 语义评审的输出契约（规划评委与被评审的规划者共用同一份措辞锚，
#: 语言锚与解析式是同一件事的两半，纪律 #3）。
SEMANTIC_JSON_KEYS = ("findings",)

_SEMANTIC_SYSTEM = (
    "你是长篇小说规划的**审稿主编**（规划评委），只评审**还没写**的规划，"
    "不评正文。你的任务是判断：这批剧情弧线是否**合理推进**作品的核心命题"
    "（母题/主线/人物弧光），是否存在「同质化推进」「母题停滞」「弧线之间无因果」"
    "三类结构问题。\n"
    "严格只输出一个 JSON 对象，形如 "
    '{"findings": [{"judge": "S1.motif_advance|S2.homogeneous|S3.causal_gap", '
    '"message": "一句话指出问题（引用弧线名与章区间）"}]}。\n'
    "⚠ 没有问题时输出 {\"findings\": []}。**不要为了凑数而报问题**——"
    "本评审目前处于采样期，误报会直接污染后续定档数据。"
)


def judge_arc_semantics(
    project_dir: str | Path,
    arcs: Iterable[Any],
    *,
    current_chapter: int,
    llm: Any = None,
    sample: int = 5,
    console: Any = None,
) -> tuple[list[Finding], str]:
    """语义评审（LLM 抽样）：弧线是否合理推进母题（`plan_managers.py:11` 缺口）。

    Args:
        arcs: 未来的剧情弧（含 name/chapter_start/chapter_end/goal）。
        sample: 抽样条数（D4：机器统计全量 + LLM 抽样；控制成本）。
        llm: 已构造的 Gateway；``None`` 时**不跑 LLM**（返回空 + 原因），
            由调用方显式传入（避免本模块在领域层偷偷建连接）。

    Returns:
        ``(findings, error)``。``error`` 非空表示**语义评审未完成**
        （LLM 缺席/超时/解析失败）——**必须显性化**，不得当成"没问题"（纪律 #1）。
        这是本函数最容易被误用的地方：老实现会把 None/异常当"通过"。
    """
    arc_list = [a for a in arcs if int(getattr(a, "chapter_end", 0)) > current_chapter]
    if not arc_list:
        return [], ""
    if llm is None:
        return [], "未提供 LLM 客户端（采样模式允许不跑，但必须显式记录）"
    # ---- 抽样（确定性：取最靠前的 N 条，便于复现；不做随机）----
    picked = sorted(arc_list, key=lambda a: int(getattr(a, "chapter_start", 0)))[: max(1, int(sample))]
    lines: list[str] = []
    for a in picked:
        lines.append(
            f"- [{getattr(a, 'name', '?')}] 第{getattr(a, 'chapter_start', 0)}"
            f"-{getattr(a, 'chapter_end', 0)}章｜目标：{str(getattr(a, 'goal', '') or '（未填）')[:200]}"
        )
    user = (
        f"当前进度：第 {current_chapter} 章。以下是**尚未写**的剧情弧（抽样 "
        f"{len(picked)}/{len(arc_list)} 条）：\n" + "\n".join(lines)
        + "\n\n请判断这些弧线是否合理推进母题/主线/人物弧光，"
          "是否存在同质化推进、母题停滞、弧线间无因果三类问题。"
    )
    try:
        from agent.client.gateway_adapter import chat_creative

        raw = chat_creative(
            llm,
            messages=[
                {"role": "system", "content": _SEMANTIC_SYSTEM},
                {"role": "user", "content": user},
            ],
            temperature=0.2,
            max_tokens=2048,
            enable_thinking=False,
        )
    except Exception as e:  # noqa: BLE001 - LLM 失败必须显性（不得当"通过"）
        from agent.core.infra.degrade import degrade

        degrade("plan_critic.semantic", "规划语义评审 LLM 调用失败，本次语义结论缺失", e)
        return [], f"LLM 调用失败：{e}"
    try:
        from agent.utils import parse_llm_json

        data = parse_llm_json(raw)
    except Exception as e:  # noqa: BLE001 - 解析失败同样是"没审"
        return [], f"语义评审输出无法解析为 JSON：{e}"
    raw_findings = data.get("findings")
    if not isinstance(raw_findings, list):
        return [], f"语义评审输出缺 `findings` 列表（键：{sorted(data.keys())}）"
    out: list[Finding] = []
    for item in raw_findings:
        if not isinstance(item, dict):
            continue
        judge = str(item.get("judge") or "S?.unnamed").strip()
        msg = str(item.get("message") or "").strip()
        if not msg:
            continue
        out.append(Finding(
            LEVEL_NOTE, judge, "semantic", msg,
            {"sampled": len(picked), "total_arcs": len(arc_list)},
        ))
    return out, ""


# ============================================================
# 留痕（纯观测面）
# ============================================================

def record_plan_critic(
    project_dir: str | Path,
    report: PlanCriticReport,
    *,
    sample_window: tuple[int, int] | None = None,
) -> bool:
    """把一次规划评审写入台账。Returns: 是否落盘成功。

    ⚠ 与 ``plan_managers.save_audit_report`` 的差异（**刻意的**）：
      那条是**拦截链的一部分**（BLOCK ⇒ 打回重排），落盘失败必须显性；
      本模块恒不阻断 ⇒ 留痕失败只意味着"这次没记上"，
      不构成对"能否开工"的证据 ⇒ 动作强度不得超过判据（纪律 #2）。
    """
    path = Path(project_dir) / CRITIC_LEDGER
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        rec = report.to_dict()
        rec["gate"] = "plan_critic"
        rec["mode"] = "sample"  # ★ 采样模式：恒不阻断（供下游统计时区分）
        if sample_window is not None:
            rec["sample_window"] = list(sample_window)
        with path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        return True
    except Exception:  # noqa: BLE001 - 纯观测面：落盘失败不授权拦截
        return False


def read_plan_critic(project_dir: str | Path) -> dict[str, Any]:
    """读取规划评审台账（供体检/看板/ M8 定档消费）。

    ★★ **无数据 ≠ 通过**（D1，纪律 #1）
    ----------------------------------
    本函数返回的 ``status`` **必须**被消费端当作出结论的前置条件：

    ==================  =======  ==============================================
    ``status``          含义     消费端**必须**怎么做
    ==================  =======  ==============================================
    ``"ok"``            有台账   可以据 ``latest`` / ``records`` 下结论
    ``"empty"``         有台账但全坏行 / 全空行 ⇒ 无有效记录 ⇒ 等同无数据
                        **不得**读成"规划无问题"
    ``"no_data"``       台账文件**不存在** ⇒ **从未评审过** ⇒ 更不得读成"没问题"
    ==================  =======  ==============================================

    ⚠ 旧实现只返回 ``total: 0``：调用方**无法区分**「评审过、没发现问题」
      与「从未评审过」。这正是本仓一号病（把"没数据"读成"通过"）的温床
      —— 而 ``plan_critic`` 恰好是**从未在生产跑过**的模块（D 项立项事实），
      所以这个区分在本模块上是**必答题，不是加分题**。

    Returns:
        ``{"status": "ok"|"empty"|"no_data", "total": 条数, "latest_ts": str,
           "latest": {judge: 最近一次该判据的发现}, "records": [...]}``

    文件缺失/损坏一律**不抛**（观测面不该反过来阻断写作），但必须以 ``status``
    显式标注「无数据」，不得静默返回一个看起来"干净"的空表（纪律 #1）。
    """
    path = Path(project_dir) / CRITIC_LEDGER
    out: dict[str, Any] = {
        "status": "no_data",
        "total": 0,
        "latest_ts": "",
        "latest": {},
        "records": [],
    }
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except Exception:  # noqa: BLE001 - 无台账＝从未评审（**≠ 通过**）
        return out  # noqa: SILENT_DEGRADE reason=expected-skip
    for ln in lines:
        ln = ln.strip()
        if not ln:
            continue
        try:
            rec = json.loads(ln)
        except Exception:  # noqa: BLE001 - 坏行跳过
            continue  # noqa: SILENT_DEGRADE reason=expected-skip
        out["total"] += 1
        out["latest_ts"] = str(rec.get("reviewed_at", out["latest_ts"]))
        out["records"].append(rec)
        for f in rec.get("findings") or []:
            judge = str((f or {}).get("judge", ""))
            if judge:
                out["latest"][judge] = f
    # 文件存在但零有效行 ⇒ "empty"（同样**不得**读成通过）
    out["status"] = "ok" if out["total"] > 0 else "empty"
    return out


# ============================================================
# 两个读数（M8 定档的**唯一输入**，§8.3）
# ============================================================

def aggregate_readings(
    project_dir: str | Path,
    *,
    confirmed_by_judge: dict[str, bool] | None = None,
) -> dict[str, dict[str, Any]]:
    """按判据聚合 M8 定档所需的两个读数。

    ★ 这是 ``§8.3`` ②的直接落地，也是本模块**唯一有"增值消费者"的出口**
      （纪律 #7：凡写"供 XX 消费"的注释，必须证明该消费者存在 ——
       消费者＝M8 定档流程 / 体检报告 / 看板）。

    ⚠⚠ **空表 ≠ 全部可达**（D1，纪律 #1）
    --------------------------------
    返回 ``{}`` 有两种含义，消费端**绝不允许**混为一谈：

    - 「采样过，但每条判据都没被触发过」⇒ 此时**不应该是 `{}`**，
      因为 ``aggregate_readings`` 只要有一次采样就会为出现过的判据建槽；
    - 「**从未采样过**（无台账）」⇒ 真正的 ``{}`` ⇒ **必须读成"无数据、
      不可定档"，而不是"所有判据都可达、可以放心升 blocking"**。
      ⇒ 这正是 D 项立项时 M8 卡住的那一环（无数据 ⇒ 拿不到达成率）。

    因此本函数在无数据时**不返回 `{}`**，而是返回一个自曝其缺的哨兵结构
    （键 ``__status__``），使调用方无法把"没数据"误读成"全绿"。
    需要纯字典语义的调用方请显式跳过 ``__status__``。

    两个读数
    --------
    - **历史达成率**（``achieved_rate``）：该判据在历史采样中**曾被达成过**的比例。
      定义：``无发现次数 / 采样次数``。⚠ 这是"该判据是否可达"的**上界估计**，
      不是精确值——但它足以回答纪律 #13 的那一问：「该判据历史达成率 < 50%？」
    - **误报率**（``false_positive_rate``）：判为不合格中"实际没问题"的比例。
      ⚠ **无法自动计算**：需要人工/复核回路标注（``confirmed_by_judge``）。
      本函数只在**有人工复核结论**时计算；否则返回 ``None`` 并**显式标注**，
      绝不猜（纪律 #22：判据不能建立在"调用方猜对语义"之上）。

    Args:
        confirmed_by_judge: ``{判据id: 该判据的发现是否被人工确认为真问题}``。
            缺省 ``None`` ⇒ 误报率一律为 ``None``（**不猜**）。

    Returns:
        ``{判据id: {"samples": n, "flagged": m, "achieved_rate": r,
                     "false_positive_rate": Optional[f], "unreachable_risk": bool}}``
        无采样时返回 ``{"__status__": "no_data", "total": 0}``（**不是 `{}`**）。
    """
    data = read_plan_critic(project_dir)
    if data.get("status") != "ok":
        # ★ 纪律 #1：无数据必须显性化，不得让消费端把 `{}` 读成"全判据可达"
        return {"__status__": str(data.get("status") or "no_data"), "total": 0}
    records: list[dict] = data.get("records") or []
    per_judge: dict[str, dict[str, Any]] = {}
    for rec in records:
        flagged = {str((f or {}).get("judge", "")) for f in (rec.get("findings") or [])}
        flagged.discard("")
        # 每次评审对"每个出现过的判据"计一次采样机会
        for judge in set(flagged) | set(per_judge.keys()):
            slot = per_judge.setdefault(
                judge, {"samples": 0, "flagged": 0, "confirmed": 0, "reviewed": 0}
            )
            slot["samples"] += 1
            if judge in flagged:
                slot["flagged"] += 1
    confirmed = confirmed_by_judge or {}
    out: dict[str, dict[str, Any]] = {}
    for judge, slot in per_judge.items():
        samples = int(slot["samples"])
        flagged = int(slot["flagged"])
        achieved = samples - flagged
        rate = (achieved / samples) if samples else 0.0
        fp: float | None = None
        if judge in confirmed and flagged:
            # 有人工复核结论时才计算；且**只对该判据**计算
            # （不能拿一个判据的复核结论去估另一个的误报率 —— 纪律 #22）
            fp = 0.0 if confirmed[judge] else 1.0
        out[judge] = {
            "samples": samples,
            "flagged": flagged,
            "achieved_rate": round(rate, 4),
            # ★ 纪律 #13：历史达成率 < 50% ⇒ **不可升 blocking**
            "unreachable_risk": rate < 0.5,
            "false_positive_rate": fp,
            "false_positive_known": judge in confirmed,
        }
    return out


# ============================================================
# 入口（唯一调用点：恒不阻断）
# ============================================================

def review_plan(
    project_dir: str | Path,
    *,
    arcs: Iterable[Any] = (),
    current_chapter: int = 0,
    llm: Any = None,
    semantic_sample: int = 5,
    console: Any = None,
    subline_dir: str | Path | None = None,
) -> PlanCriticReport:
    """规划评委入口：**只记录不拦**（M5，采样模式）。

    组成
    ----
    1. 机器统计判据（零 LLM，全量）：G1 双级粒度 / G2 段内落差
    2. LLM 抽样语义判据：S1/S2/S3（``plan_managers.py:11`` 自认缺口）

    ⚠ **恒不阻断**：无论如何都返回报告，调用方**不得**据其产出致命错误。
      D3 尚未定档（§8.3 要求先跑采样统计误报率与历史达成率），
      此时硬拦＝把阈值当摧毁扳机（纪律 #13）。

    Returns:
        :class:`PlanCriticReport`（**不是**错误列表 —— 采样模式没有"错误"概念；
        调用方若需要致命错误，请用 ``plan_managers.audit_plan``）。
        保留本签名以便 M8 定档后扩展为返回列表时**不改调用方式**。
    """
    root = Path(project_dir)
    report = PlanCriticReport(
        current_chapter=int(current_chapter or 0),
        reviewed_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
    )
    # ---- 1) 机器统计：逐支线 ----
    base = Path(subline_dir) if subline_dir else (root / "sublines")
    scope_bits: list[str] = []
    if base.is_dir():
        for sub in sorted(base.iterdir()):
            f = sub / "subline.md"
            if not (sub.is_dir() and f.exists()):
                continue
            try:
                content = f.read_text(encoding="utf-8")
            except Exception:  # noqa: BLE001 - 观测面：读不到就跳过
                continue  # noqa: SILENT_DEGRADE reason=expected-skip
            scope_bits.append(sub.name)
            report.findings.extend(judge_plan_granularity(content, name=sub.name))
            report.findings.extend(judge_rhythm_peaks(content, name=sub.name))
    report.scope = (
        f"支线 {len(scope_bits)} 条" + (f"（{'、'.join(scope_bits[:6])}"
        f"{'…' if len(scope_bits) > 6 else ''}）" if scope_bits else "")
    )
    # ---- 2) LLM 抽样语义评审 ----
    arc_list = list(arcs)
    if arc_list:
        sem, err = judge_arc_semantics(
            root, arc_list,
            current_chapter=int(current_chapter or 0),
            llm=llm, sample=semantic_sample, console=console,
        )
        report.findings.extend(sem)
        report.semantic_ran = err == ""
        report.semantic_error = err
    else:
        report.semantic_error = "无未来弧线（无可评审对象）"
    # ---- 3) 留痕（失败不转致命）----
    ok = record_plan_critic(root, report)
    if console is not None:
        console.print(f"[dim]{report.render()}[/dim]")
        if not ok:
            console.print(
                f"[yellow]⚠ 规划评审留痕失败（{CRITIC_LEDGER.as_posix()} 不可写）："
                f"本次采样未记入台账（不影响写作）[/yellow]"
            )
    return report
