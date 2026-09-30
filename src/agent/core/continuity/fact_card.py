"""事实卡对账（长线一致性二期 T1，登记单 20260930）。

从章正文中**确定性抽取**可校验事实卡（日期/倒计时/死亡/持有物），与增量状态
（可由 ``ContinuityLedger`` facts 播种）比对，产出三条红线的违规发现：

- R1 ``death_irreversibility``：角色被标记死亡后仍作为动作主体活动（死亡不可逆）；
- R2 ``sold_then_used``：物品已脱手（卖/交/送）后又无来由地被取出使用（持有物账平）；
- R3 ``day_mark_conflict``：同章两个「第N日」/日期账标记互相矛盾，或同章内
  「最后一天」与「还有N天」并存（时间线单调 + 同章自撞）。

设计约束（登记单 §三.T1）：
- 只做**确定性**判定（能正则+状态机算的绝不问 LLM），语义级疑似冲突不在本模块职责内
  （归 T2 真相断言裁决）；
- 判定保守：无回闪标记（回想/回忆/当年/梦中/幻象/生前）时才判 error，其余降 warn；
- 写时门禁接线（workflows/writing）消费 ``FactCardIssues``；离线标定走
  ``reconcile_book``（CLI ``fact-checkup``），对存量书全量跑产出标定报告。
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

# ---------------- 中文数字 ----------------

_CN_DIGIT = {
    "零": 0, "一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5,
    "六": 6, "七": 7, "八": 8, "九": 9,
}
_CN_UNIT = {"十": 10, "百": 100, "千": 1000}


def cn2int(s: str) -> int | None:
    """中文数字/阿拉伯数字 → int；不支持或为空返回 None（「两」按 2）。"""
    s = (s or "").strip()
    if not s:
        return None
    if s.isdigit():
        return int(s)
    total, cur = 0, 0
    for ch in s:
        if ch in _CN_DIGIT:
            cur = _CN_DIGIT[ch]
        elif ch in _CN_UNIT:
            unit = _CN_UNIT[ch]
            total += (cur or 1) * unit
            cur = 0
        else:
            return None
    return total + cur


# ---------------- 抽取模式 ----------------

# 「第N日/第N天」账册式日期标记
_DAY_MARK_RE = re.compile(r"第([零一二两三四五六七八九十百千\d]+)[日天]")
# 倒计时/期限表达式：N天之后 / 还有(只剩/仅剩/只有)N天 / 最后一天（哨兵=1）
_DEADLINE_RE = re.compile(
    r"(?:([零一二两三四五六七八九十百千\d]+)[天日])(?:之?后)"
    r"|(?:还有|还剩|只剩|仅剩|只有|限时|为期)([零一二两三四五六七八九十百千\d]+)[天日]"
    r"|最后一天"
)
# 相对日期词：明日/明天=1、后日/后天=2、大后天=3（后随主体短语）
_REL_DAY_RE = re.compile(r"(明天|明日|后天|后日|大后天)")
# 回闪/插叙标记：出现则该句不参与单调性判定
_FLASHBACK_RE = re.compile(r"回想|回忆|记得|那时|当年|此前|之前|梦中|幻象|仿佛|梦里|犹记|脑海中")
# 死亡标记：「X死了/病故/去世/丧命/毙命/尸/生前/遗言/灵位」
_DEATH_RE = re.compile(
    r"([\u4e00-\u9fa5]{2,4})(?:已经)?(?:死了|去世了?|病故了?|丧命|毙命|战死了?|身亡|殒命|"
    r"的死讯|的尸体|的尸身|的遗体|的灵位|的坟墓|的遗言|的遗物|生前)"
)
# 动作主体（懒捕获防止「周德海说道」把「说」吞进人名）
_ACTION_SUBJ_RE = re.compile(
    r"([\u4e00-\u9fa5]{2,4}?)"
    r"(?:没说话|说道|点了点头|点头|摇头|开口|笑了|皱眉|叹了|冷笑|沉默|蹲下|伸手|迈步|"
    r"站起|坐下|转身|走进|走出|接过|递给|看着|盯着|赶回|赶到|离开|说|问)"
)
# 引用/翻旧账线索：句内出现则「第N日」视为引用旧账，不是当日断言
_DAY_REF_CUE_RE = re.compile(r"翻[开到]|写着|记着|记了|记下过|昨日|昨天|前日|『|』|此前|上次")
# 当日断言线索（强 live 信号）
_DAY_LIVE_CUE_RE = re.compile(r"今天|今日|现在是|最新|当日|此刻|一早")
# 不可逆脱手动词（交给/递给/还给属临时交接，不算脱手）
_ITEM_OUT_RE = re.compile(
    r"(?:把|将)?([\u4e00-\u9fa5]{2,8}?)(?:全部|全都|一并|连)?"
    r"(?:卖(?:出|掉|了|给)|抵了|赔了|送人了?|换成了?)"
)
# 柜台/交易场景交付：把 X 放到/摆在柜台上 ⇒ 视为脱手（交易场景语义）
_ITEM_COUNTER_RE = re.compile(
    r"(?:把|将)([\u4e00-\u9fa5]{2,8}?)(?:一[枚块颗只])?(?:放|摆|递)(?:到|在)柜台"
)
# 以物易物：A 换了/换得 B ⇒ A 脱手、B 获得（「换了个肩/换了下姿势」类动作不算）
_ITEM_TRADE_RE = re.compile(
    r"([\u4e00-\u9fa5]{2,8}?)(?:换了(?![个下这])|换来了|换得|换来)([\u4e00-\u9fa5]{2,6}?)(?:[，。；]|下品|中品|上品|$)"
)
# 物品取用：取出/拿出/翻开/摊开
_ITEM_IN_RE = re.compile(r"(?:取出|拿出|掏出|取出之前|翻开|摊开|取出那)(?:了)?(?:之前|自己)?(?:的)?([\u4e00-\u9fa5]{2,8})")
# 物品再获得：买/赢回/找回/换回/得到（用于清持有账）
_ITEM_ACQ_RE = re.compile(
    r"(?:买(?:了|回|下|来)|赢回|找回|换回|换来了|得到|领了|重新炼了?|补齐了?|寻回)"
    r"(?:一[枚块颗只把些])?([\u4e00-\u9fa5]{2,6})"
)

# 物品名尾部截断字（「取出残灵石，又…」「丹瓶倒出一粒」类过捕获）
_ITEM_TRAIL_STOP = "倒了给放又并再和与拿取的了记录写，。；：！？"
# 物品位停用词（连词/副词被卷入捕获时剔除）
_ITEM_STOPWORDS = {
    "而是", "但是", "然后", "接着", "于是", "直接", "只好", "只能", "赶紧",
    "马上", "立刻", "随后", "最后", "之前", "之后", "这次", "那个", "这个",
}
# 物品名归一化：剥离数量/品级/来源修饰，得到可比对的物品键
_ITEM_KEY_STRIP_RE = re.compile(
    r"(?:[零一二两三四五六七八九十百\d]+[枚块颗只把个])"
    r"|(?:下品|中品|上品|劣品|残缺的|残破的)"
    r"|(?:之前|先前|刚刚)?(?:提炼|提纯)?的"
    r"|那[枚块颗只个]"
)


def _trim_item(s: str) -> str:
    for i, ch in enumerate(s):
        if ch in _ITEM_TRAIL_STOP:
            return s[:i]
    return s


def _clean_item(s: str | None) -> str:
    item = re.sub(r"^[他把将她它用]+", "", (s or "").strip())
    item = _trim_item(item)
    return "" if item in _ITEM_STOPWORDS else item


def _item_key(s: str) -> str:
    """物品名归一键（比对用）：剥数量/品级/来源修饰后去重复字。"""
    k = _ITEM_KEY_STRIP_RE.sub("", s or "")
    k = re.sub(r"^(?:一枚|一块|一颗)", "", k)
    return k or (s or "")

_SENT_SPLIT_RE = re.compile(r"[。！？\n]")


def _sentences(text: str) -> list[str]:
    return [s for s in _SENT_SPLIT_RE.split(text or "") if s.strip()]


def _strip_frontmatter(text: str) -> str:
    if text.startswith("---"):
        parts = text.split("---", 2)
        if len(parts) >= 3:
            return parts[2]
    return text


# ---------------- 数据模型 ----------------


@dataclass
class FactCard:
    """一章的事实卡（确定性抽取产物）。"""

    chapter: int
    day_marks: list[tuple[int, str]] = field(default_factory=list)   # (值, 证据句)
    deadlines: list[tuple[int, str]] = field(default_factory=list)   # (天数, 证据句)
    deadline_tags: list[tuple[int, str, str]] = field(default_factory=list)  # (天数, 主体标签, 证据句)
    death_marks: list[tuple[str, str]] = field(default_factory=list)  # (人名, 证据句)
    items_out: list[tuple[str, str]] = field(default_factory=list)   # (物品, 证据句)
    items_in: list[tuple[str, str]] = field(default_factory=list)    # (物品, 证据句)
    items_acquired: list[tuple[str, str]] = field(default_factory=list)  # (物品, 证据句)
    yesterday_refs: list[tuple[int, str]] = field(default_factory=list)  # 「昨天的记录：第N日」


@dataclass
class FactCardIssue:
    """一条对账发现。severity: error（确定性冲突）/ warn（疑似，人工或上游裁决）。"""

    rule_id: str          # death_irreversibility / sold_then_used / day_mark_conflict / countdown_conflict
    severity: str
    chapter: int
    message: str
    evidence: str = ""

    def to_dict(self) -> dict:
        return {
            "rule_id": self.rule_id,
            "severity": self.severity,
            "chapter": self.chapter,
            "message": self.message,
            "evidence": self.evidence[:160],
        }


@dataclass
class _ReconcileState:
    """跨章增量状态：死亡名单 / 物品持有账 / 上一次日期标记。"""

    dead: dict[str, int] = field(default_factory=dict)          # name -> 死亡章
    items_gone: dict[str, int] = field(default_factory=dict)    # item -> 脱手章
    last_day: tuple[int, int] | None = None                     # (章号, 第N日值)
    known_names: set[str] = field(default_factory=set)
    deadlines: dict[str, tuple[int, int]] = field(default_factory=dict)  # tag -> (剩余天数, 章号)


# ---------------- 抽取 ----------------


def extract_fact_card(chapter: int, text: str) -> FactCard:
    """从单章正文抽取事实卡（剥离 frontmatter）。"""
    body = _strip_frontmatter(text or "")
    sents = _sentences(body)
    card = FactCard(chapter=chapter)
    for s in sents:
        for m in _DAY_MARK_RE.finditer(s):
            v = cn2int(m.group(1))
            if v is None:
                continue
            if _DAY_REF_CUE_RE.search(s) and not _DAY_LIVE_CUE_RE.search(s):
                continue  # 引用旧账（翻账册/引文），不作为当日断言
            card.day_marks.append((v, s.strip()))
        # 「昨日/昨天 + 第N日」单独留存（golden H23：昨天的记录日期远早于当日账）
        if re.search(r"昨日|昨天", s) and not _DAY_LIVE_CUE_RE.search(s):
            for m in _DAY_MARK_RE.finditer(s):
                v = cn2int(m.group(1))
                if v is not None:
                    card.yesterday_refs.append((v, s.strip()))
        for m in _DEADLINE_RE.finditer(s):
            if m.group(0) == "最后一天":
                card.deadlines.append((1, s.strip()))
                continue
            g = m.group(1) or m.group(2)
            v = cn2int(g or "")
            if v is not None:
                card.deadlines.append((v, s.strip()))
                tail = s[m.end(): m.end() + 10]
                tag = re.sub(r"^[，。；：、\s]*", "", tail)[:6]
                if tag:
                    card.deadline_tags.append((v, tag, s.strip()))
        for m in _REL_DAY_RE.finditer(s):
            days = {"明天": 1, "明日": 1, "后天": 2, "后日": 2, "大后天": 3}.get(m.group(1))
            tail = s[m.end(): m.end() + 10]
            tag = re.sub(r"^[，。；：、\s]*(?:就是|的)?", "", tail)[:6]
            if days and tag:
                card.deadline_tags.append((days, tag, s.strip()))
        for m in _DEATH_RE.finditer(s):
            name = m.group(1)
            # 死亡主语剔除代词/泛称
            if name and not any(name.endswith(x) for x in ("他们", "自己", "人们", "这个", "那个")):
                card.death_marks.append((name, s.strip()))
        for m in _ITEM_OUT_RE.finditer(s):
            item = _clean_item(m.group(1))
            if item and len(item) >= 2 and not _FLASHBACK_RE.search(s):
                card.items_out.append((item, s.strip()))
        for m in _ITEM_IN_RE.finditer(s):
            item = _clean_item(m.group(1))
            if item and len(item) >= 2:
                card.items_in.append((item, s.strip()))
        for m in _ITEM_ACQ_RE.finditer(s):
            item = _clean_item(m.group(1))
            if item and len(item) >= 2:
                card.items_acquired.append((item, s.strip()))
        if "柜台" in s:
            for m in _ITEM_COUNTER_RE.finditer(s):
                item = _clean_item(m.group(1))
                if item and len(item) >= 2:
                    card.items_out.append((item, s.strip()))
        for m in _ITEM_TRADE_RE.finditer(s):
            left = _clean_item(m.group(1))
            right = _clean_item(m.group(2))
            if left and len(left) >= 2:
                card.items_out.append((left, s.strip()))
            if right and len(right) >= 2:
                card.items_acquired.append((right, s.strip()))
    return card


# ---------------- 对账 ----------------


def _death_confirmed(name: str, sents: list[str]) -> bool:
    """死亡标记去误报：句内或近句含否定/传闻标记则不确认。"""
    for s in sents:
        if name in s and re.search(r"没有死|并未死|没有{0,2}真死|假死|诈死|传闻|谣传|以为.{0,6}死", s):
            return False
    return True


def reconcile_card(
    card: FactCard,
    state: _ReconcileState,
    *,
    known_names: set[str] | None = None,
) -> list[FactCardIssue]:
    """把一张事实卡对到增量状态上，返回违规发现并就地推进状态。"""
    issues: list[FactCardIssue] = []
    ch = card.chapter
    sents = [ev for _, ev in card.day_marks + card.death_marks + card.items_out]

    # --- R1 死亡标记入账（死亡→复活的动作句扫描在 reconcile_text 整章级做） ---
    for name, ev in card.death_marks:
        names = {name} | {n for n in (known_names or set()) if n in ev}
        for n in names:
            if len(n) >= 2 and _death_confirmed(n, sents):
                state.dead.setdefault(n, ch)

    # --- R2 持有物账平 ---
    for item, ev in card.items_out:
        state.items_gone[item] = ch
    # 再获得清账（同章先卖后「买回」不算违规）
    for item, _ev in card.items_acquired:
        for gitem in list(state.items_gone):
            gk, ik = _item_key(gitem), _item_key(item)
            if gk in ik or ik in gk:
                state.items_gone.pop(gitem, None)
    for item, ev in card.items_in:
        ik = _item_key(item)
        gone_ch = next(
            (
                gch
                for gitem, gch in state.items_gone.items()
                if (gk := _item_key(gitem)) and (gk in ik or ik in gk)
            ),
            None,
        )
        if gone_ch is not None and gone_ch <= ch and not _FLASHBACK_RE.search(ev):
            issues.append(
                FactCardIssue(
                    rule_id="sold_then_used",
                    severity="error",
                    chapter=ch,
                    message=f"物品「{item}」在第{gone_ch}章已脱手，第{ch}章无再获得记录又被取出使用",
                    evidence=ev,
                )
            )
            state.items_gone.pop(item, None)

    # --- R3a 同章「第N日」多标记（工坊/实验流一章跨多日合法 → 只 warn 提示人工） ---
    live_marks = [(v, ev) for v, ev in card.day_marks if not _FLASHBACK_RE.search(ev)]
    day_vals = sorted({v for v, _ in live_marks})
    for i in range(len(day_vals)):
        for j in range(i + 1, len(day_vals)):
            a, b = day_vals[i], day_vals[j]
            ev_b = next((ev for v, ev in live_marks if v == b), "")
            if abs(b - a) >= 3:
                issues.append(
                    FactCardIssue(
                        rule_id="day_mark_conflict",
                        severity="warn",
                        chapter=ch,
                        message=f"同章出现多个日期标记：第{a}日 与 第{b}日（相差 {b - a} 日；一章跨多日为合法叙事，请人工确认非矛盾）",
                        evidence=ev_b,
                    )
                )
    # 跨章日期单调（回退 ≥3 日 → error；1-2 日 → warn）；当日值只取非回闪标记
    if live_marks:
        cur = max(live_marks)[0]
        if state.last_day is not None:
            prev_ch, prev_v = state.last_day
            drop = prev_v - cur
            if drop >= 3:
                issues.append(
                    FactCardIssue(
                        rule_id="day_mark_conflict",
                        severity="error",
                        chapter=ch,
                        message=f"日期账倒退：第{prev_ch}章记「第{prev_v}日」，第{ch}章记「第{cur}日」（倒退 {drop} 日）",
                        evidence=live_marks[0][1],
                    )
                )
            elif 0 < drop < 3:
                issues.append(
                    FactCardIssue(
                        rule_id="day_mark_conflict",
                        severity="warn",
                        chapter=ch,
                        message=f"日期账小幅回退：第{prev_ch}章「第{prev_v}日」→ 第{ch}章「第{cur}日」（疑回闪未标记）",
                        evidence=live_marks[0][1],
                    )
                )
        state.last_day = (ch, cur)

    # --- R3d 跨章期限缩水：同一主体标签的剩余天数比「章数流逝」还应得更慢 ---
    for days, tag, ev in card.deadline_tags:
        prev = state.deadlines.get(tag)
        if prev is not None and not _FLASHBACK_RE.search(ev):
            prev_days, prev_ch = prev
            elapsed = ch - prev_ch
            allowed = prev_days - elapsed
            if elapsed >= 1 and days < allowed:
                issues.append(
                    FactCardIssue(
                        rule_id="countdown_conflict",
                        severity="error",
                        chapter=ch,
                        message=f"期限「{tag}」缩水：第{prev_ch}章称还剩{prev_days}天，第{ch}章（隔{elapsed}章）只余{days}天，快于时间流速",
                        evidence=ev,
                    )
                )
        # 同章内同标签多口径也抓（取最小值入账，配合同章自撞判据）
        cur = state.deadlines.get(tag)
        if cur is None or days < cur[0]:
            state.deadlines[tag] = (days, ch)

    # --- R3c「昨天的记录」日期与当日账差距过大（golden H23） ---
    cur_day = max(live_marks)[0] if live_marks else (
        state.last_day[1] if state.last_day else None
    )
    if cur_day is not None:
        for v, ev in card.yesterday_refs:
            if cur_day - v > 2:
                issues.append(
                    FactCardIssue(
                        rule_id="day_ref_conflict",
                        severity="error",
                        chapter=ch,
                        message=f"「昨天的记录」记为第{v}日，但当日账已是第{cur_day}日（相差 {cur_day - v} 日，昨天不可能相隔超过一天）",
                        evidence=ev,
                    )
                )

    # --- R3b 同章倒计时自撞：「最后一天/只剩一天」与「还有N天(N≥2)」并存 ---
    has_last_day = any(
        re.search(r"最后一天|只剩(?:下)?一天|仅剩一天", ev) for _, ev in card.deadlines
    ) or any("最后一天" in ev for _, ev in card.day_marks)
    multi_days = {v for v, _ in card.deadlines if v >= 2}
    if has_last_day and multi_days:
        ev = next((ev for v, ev in card.deadlines if v >= 2), "")
        if not _FLASHBACK_RE.search(ev):
            issues.append(
                FactCardIssue(
                    rule_id="countdown_conflict",
                    severity="error",
                    chapter=ch,
                    message=f"同章倒计时自撞：既称「最后一天」又出现「还有 {sorted(multi_days)} 天」",
                    evidence=ev,
                )
            )

    return issues


def reconcile_text(
    chapter: int,
    text: str,
    state: _ReconcileState,
    *,
    known_names: set[str] | None = None,
) -> list[FactCardIssue]:
    """抽取 + 对账一步走（含整章动作句扫描，供写时门禁/离线标定共用）。"""
    card = extract_fact_card(chapter, text)
    issues = reconcile_card(card, state, known_names=known_names)
    body = _strip_frontmatter(text or "")
    pending = dict(state.dead)
    for m in _ACTION_SUBJ_RE.finditer(body):
        name = m.group(1)
        ctx = m.string[max(0, m.start() - 20): m.end() + 40]
        if name not in pending:
            continue
        if _FLASHBACK_RE.search(ctx):
            continue
        died_ch = pending.pop(name)
        issues.append(
            FactCardIssue(
                rule_id="death_irreversibility",
                severity="error",
                chapter=chapter,
                message=f"「{name}」在第{died_ch}章已有死亡标记，第{chapter}章仍作为动作主体活动",
                evidence=ctx.strip(),
            )
        )
    return issues


# ---------------- 离线整书对账（标定/回归） ----------------


def reconcile_book(
    chapters: dict[int, str],
    *,
    known_names: set[str] | None = None,
) -> list[FactCardIssue]:
    """按章号顺序对整书做事实卡对账（离线标定入口）。"""
    state = _ReconcileState(known_names=set(known_names or ()))
    all_issues: list[FactCardIssue] = []
    for ch in sorted(chapters):
        all_issues.extend(reconcile_text(ch, chapters[ch], state, known_names=known_names))
    return all_issues


def load_chapters_dir(chapters_dir: str | Path) -> dict[int, str]:
    """读 chapters/ch*.md 为 {章号: 正文}（含 frontmatter，抽取侧自行剥离）。"""
    root = Path(chapters_dir)
    out: dict[int, str] = {}
    for p in sorted(root.glob("ch*.md")):
        m = re.match(r"ch(\d+)\.md", p.name)
        if not m:
            continue
        try:
            out[int(m.group(1))] = p.read_text(encoding="utf-8")
        except Exception:  # noqa: BLE001 - 单章读失败跳过（降级不阻断）
            pass  # noqa: SILENT_DEGRADE reason=best-effort ref=20260930_长线一致性二期_事实对账与真相SSOT.md
    return out


def write_calibration_report(issues: list[FactCardIssue], path: str | Path) -> None:
    """把对账发现写成标定报告（JSON lines，供与 golden cases 清单比对）。"""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    lines = [json.dumps(i.to_dict(), ensure_ascii=False) for i in issues]
    p.write_text("\n".join(lines) + "\n", encoding="utf-8")


# ---------------- 跨章状态持久化 + 写时门禁入口 ----------------

_FACT_STATE_FILE = Path(".state/continuity/fact_cards.json")


class FactCardStateStore:
    """跨章事实状态（死亡名单/物品账/日期账）的持久化。

    状态只反映**已发布**章节：写时门禁用它对当前稿对账；章节正式落盘后
    ``commit_fact_card`` 才把该章事实卡推进状态（重写期间不污染基准）。
    """

    def __init__(self, project_dir: str | Path) -> None:
        self.file = Path(project_dir) / _FACT_STATE_FILE

    def load(self) -> _ReconcileState:
        state = _ReconcileState()
        if not self.file.exists():
            return state
        try:
            data = json.loads(self.file.read_text(encoding="utf-8"))
            state.dead = {k: int(v) for k, v in (data.get("dead") or {}).items()}
            state.items_gone = {k: int(v) for k, v in (data.get("items_gone") or {}).items()}
            ld = data.get("last_day")
            state.last_day = (int(ld[0]), int(ld[1])) if ld else None
            state.deadlines = {
                k: (int(v[0]), int(v[1])) for k, v in (data.get("deadlines") or {}).items()
            }
        except Exception:  # noqa: BLE001 - 损坏降级为空状态
            pass  # noqa: SILENT_DEGRADE reason=best-effort ref=20260930_长线一致性二期_事实对账与真相SSOT.md
        return state

    def save(self, state: _ReconcileState) -> None:
        try:
            self.file.parent.mkdir(parents=True, exist_ok=True)
            data = {
                "dead": state.dead,
                "items_gone": state.items_gone,
                "last_day": list(state.last_day) if state.last_day else None,
                "deadlines": state.deadlines,
            }
            tmp = self.file.with_suffix(".json.tmp")
            tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
            tmp.replace(self.file)
        except Exception:  # noqa: BLE001 - 状态落盘失败不阻断写作
            pass  # noqa: SILENT_DEGRADE reason=best-effort ref=20260930_长线一致性二期_事实对账与真相SSOT.md


def gate_fact_card(
    project_dir: str | Path,
    chapter: int,
    text: str,
    known_names: set[str] | None = None,
) -> tuple[list[FactCardIssue], list[FactCardIssue]]:
    """写时门禁入口：对当前稿做事实卡对账（不推进状态）。

    Returns:
        (errors, warnings)——error 为确定性冲突（调用方应 blocking），
        warn 为疑似（随报告透出，不阻断）。
    """
    state = FactCardStateStore(project_dir).load()
    issues = reconcile_text(chapter, text, state, known_names=known_names)
    errors = [i for i in issues if i.severity == "error"]
    warns = [i for i in issues if i.severity != "error"]
    return errors, warns


def commit_fact_card(
    project_dir: str | Path,
    chapter: int,
    text: str,
    known_names: set[str] | None = None,
) -> None:
    """章节正式落盘后调用：把该章事实卡推进跨章状态（发布事实入账）。"""
    store = FactCardStateStore(project_dir)
    state = store.load()
    reconcile_text(chapter, text, state, known_names=known_names)
    store.save(state)


__all__ = [
    "FactCard",
    "FactCardIssue",
    "FactCardStateStore",
    "cn2int",
    "extract_fact_card",
    "reconcile_card",
    "reconcile_text",
    "reconcile_book",
    "load_chapters_dir",
    "write_calibration_report",
    "gate_fact_card",
    "commit_fact_card",
]
