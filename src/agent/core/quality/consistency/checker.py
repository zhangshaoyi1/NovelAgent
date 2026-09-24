"""一致性校验器（5.5）

职责：在设定更新、写作前、章节产出后三个时机执行规则校验。

校验项（内置规则集，均为真实实现）：
    - field_conflict：设定字段冲突（委托 ConflictArbiter.check_new_setting）
    - timeline_conflict：角色生死 / 时间线矛盾（POST_WRITE，比对 characters/*.md 真源）
    - relation_conflict：关系网一致性（POST_WRITE，比对 relations/graph.md 活跃边）
    - golden_finger_overstep：金手指/系统越界（POST_WRITE，比对角色「禁用词」）
    - realm_overstep：境界越级（POST_WRITE，比对 world.md 境界体系）
    - realm_span：境界**跨度**越级（POST_WRITE，比对连续性账本的承接境界；
      与 realm_overstep 的区别：后者只校验"境界名是否在体系内"，本规则校验
      "本章宣称的境界相对承接值跳了几档"，差 ≥2 档即 BLOCK）

冲突输出：一致性影响报告（冲突条目 + 涉及章节 + 处理建议）
"""

from __future__ import annotations

import json
import re
import logging
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any


class CheckTrigger(str, Enum):
    """校验时机"""

    PRE_WRITE = "pre-write"
    POST_WRITE = "post-write"
    PRE_UPDATE_SETTING = "pre-update-setting"


class Severity(str, Enum):
    """冲突严重度"""

    BLOCK = "block"      # 阻断，必须处理
    WARN = "warn"        # 警告，可忽略


@dataclass
class Conflict:
    """一致性冲突"""

    rule_id: str
    severity: Severity
    description: str
    affected_chapters: list[str] = field(default_factory=list)
    suggestions: list[str] = field(default_factory=list)


@dataclass
class ConsistencyReport:
    """一致性影响报告"""

    passed: bool
    trigger: CheckTrigger
    conflicts: list[Conflict] = field(default_factory=list)

    def to_markdown(self) -> str:
        """渲染为 Markdown 报告"""
        if not self.conflicts:
            return f"# 一致性校验报告（{self.trigger.value}）\n\n通过，无冲突。\n"
        lines = [
            f"# 一致性校验报告（{self.trigger.value}）",
            "",
            f"结论：{'未通过（存在阻断项）' if not self.passed else '通过（仅警告）'}",
            "",
        ]
        for c in self.conflicts:
            lines.append(f"- **[{c.severity.value}] {c.rule_id}**：{c.description}")
            if c.suggestions:
                lines.append("  - 建议：" + "；".join(c.suggestions))
            if c.affected_chapters:
                lines.append("  - 涉及章节：" + ", ".join(c.affected_chapters))
        return "\n".join(lines) + "\n"


# ============================================================================
# 解析器与启发式（post-write 规则复用）
# ============================================================================

# 角色档案中判定「故事开始前已故 / 早年便亡」的标记
_CHAR_DEAD_AT_START = [
    "已故", "早已离世", "早年便已故去", "已死多年", "早已亡故",
    "自幼便失去", "早已过世", "已逝", "早已作古",
]
# 角色档案中判定「故事中后期才牺牲 / 当前应存活」的标记
_CHAR_DIES_LATE = [
    "为保护", "而死", "死前", "殉", "牺牲", "临终", "含恨而终",
    "命丧", "战死", "殒命", "以身殉", "就义", "罹难",
]
_CHAR_ALIVE = ["在世", "存活", "尚在", "健在", "仍然活着", "未死", "并未死去", "尚在人世"]

# 章节正文中「断言某角色已死」的模式（需锚定到角色名附近）
_DEATH_ASSERTION = re.compile(
    r"(?:已经死了|已然死去|早(?:已|就)(?:死|亡|故去|逝世|不在人世)|"
    r"早在[^，。\n]{0,14}?便(?:已|经)?(?:死|亡|故去|逝世)|"
    r"便已故去|早已(?:死|亡|故去|逝世)|含恨而终|命丧|"
    r"殉(?:国|职|难|身|于)|牺牲了?|已(?:经)?(?:死|亡|故去|逝世)|"
    r"长眠于|化作枯骨|尸骨已寒|早已作古|撒手人寰)"
)
# 章节正文中「断言某角色仍存活」的模式
_ALIVE_ASSERTION = re.compile(
    r"(?:依然在世|仍然活着|安然无恙|并未死去|尚在人世|依旧存活|还活着|尚在人间)"
)
# 关系网中「互动型」边（暗示角色在对应章节仍活跃/在世）
_GRAPH_INTERACTIVE_TYPES = {
    "和解", "师徒", "主仆", "敌对", "合作", "同伴", "逼问", "指引", "相助",
    "盟友", "恋人", "父子", "兄弟", "挚友", "同僚", "护持", "纠缠",
}
# 金手指 / 系统类越界调用模式
_GOLDEN_FINGER_INVOKE = re.compile(
    r"(系统提示|金手指|外挂|激活了系统|触发系统|召唤系统|开启了系统|"
    r"系统加持|系统空间|系统奖励|脑海中的系统|面板跳出)"
)
# 境界突破 / 晋入模式（后接境界 token）
_REALM_BREAK = re.compile(r"(?:突破至|晋升为|晋入|踏入|突破.+?境界|迈入)(.{2,8}?)(?:境|之境|境界|期|阶)")


def _cjk_substrings(name: str) -> list[str]:
    """从一个名字中取所有长度>=2 的连续中文字子串（用于正文模糊匹配「周伯」<->「仵作周伯」）。"""
    out: list[str] = []
    runs = re.findall(r"[一-鿿]+", name)
    for run in runs:
        n = len(run)
        if n < 2:
            continue
        for length in (3, 2):
            for i in range(n - length + 1):
                sub = run[i : i + length]
                if sub not in out:
                    out.append(sub)
    # 优先长匹配：按长度降序，便于命中「周伯」而非「仵作」
    return sorted(out, key=len, reverse=True)


def _parse_character_status(text: str) -> str:
    """从角色档案正文推断生死状态。

    返回：DEAD_AT_START / ALIVE_THEN_DIES / ALIVE / UNKNOWN
    """
    if any(m in text for m in _CHAR_DEAD_AT_START):
        return "DEAD_AT_START"
    if any(m in text for m in _CHAR_DIES_LATE):
        return "ALIVE_THEN_DIES"
    if any(m in text for m in _CHAR_ALIVE):
        return "ALIVE"
    return "UNKNOWN"


def _parse_character_forbidden(text: str) -> list[str]:
    """从角色档案提取「禁用词」列表（用于金手指越界校验）。"""
    m = re.search(r"禁用词\**\s*[：:]\s*(.+)", text)
    if not m:
        return []
    raw = m.group(1)
    terms = re.split(r"[、,，\s]+", raw)
    return [t.strip(" '\"*") for t in terms if t.strip(" '\"*")]


def _load_character_index(project_dir: Path) -> dict[str, dict[str, Any]]:
    """加载 characters/ 下所有角色档案，返回 {规范化名: {status, forbidden, raw_name}}。"""
    index: dict[str, dict[str, Any]] = {}
    chars_dir = project_dir / "characters"
    if not chars_dir.is_dir():
        return index
    for p in chars_dir.glob("*.md"):
        try:
            text = p.read_text(encoding="utf-8")
        except Exception:  # noqa: BLE001
            continue  # noqa: SILENT_DEGRADE
        # frontmatter name 优先，否则用文件名
        fm = re.search(r"name\s*[:=]\s*[\"']?([^\"'\n]+)", text)
        display = fm.group(1).strip().strip('"\'') if fm else p.stem
        index[display] = {
            "status": _parse_character_status(text),
            "forbidden": _parse_character_forbidden(text),
            "raw_name": display,
        }
    return index


def _load_graph_edges(project_dir: Path) -> list[dict[str, str]]:
    """加载 relations/graph.md，返回活跃边列表（不含归档边）。

    每条边含 from/to/type/start 四个字段，便于按任一端角色名检索。
    """
    edges: list[dict[str, str]] = []
    graph_path = project_dir / "relations" / "graph.md"
    if not graph_path.is_file():
        return edges
    try:
        text = graph_path.read_text(encoding="utf-8")
    except Exception:  # noqa: BLE001
        return edges
    # 仅解析「边（关系）」表（非归档边）
    section = re.search(r"## 边（关系）(.*?)(?:\n## |\Z)", text, re.S)
    if not section:
        return edges
    for line in section.group(1).splitlines():
        if not line.startswith("|"):
            continue
        parts = [x.strip() for x in line.strip().strip("|").split("|")]
        if len(parts) < 6:
            continue
        if parts[0] in ("起",) or set(parts[0]) <= set("- "):
            continue
        edges.append({
            "from": parts[0],
            "to": parts[1],
            "type": parts[2],
            "start": parts[4],
        })
    return edges


def _load_world_realms(project_dir: Path) -> set[str]:
    """从 world.md 提取注册的境界集合（仅当显式定义境界体系时返回非空）。"""
    world_path = project_dir / "world.md"
    if not world_path.is_file():
        return set()
    try:
        text = world_path.read_text(encoding="utf-8")
    except Exception:  # noqa: BLE001
        return set()
    realms: set[str] = set()
    # 形如「境界：凡人 < 练气 < 筑基」或「修炼体系：...、...、...」
    for m in re.finditer(r"(?:境界|修炼体系|境界体系)\s*[:：]\s*(.+)", text):
        fragment = m.group(1)
        for tok in re.split(r"[<、，,\s]+", fragment):
            tok = tok.strip(" '\"")
            if tok and len(tok) <= 8:
                realms.add(tok)
    return realms


#: 境界体系小节标题（world.md）。编号列表形态优先于单行内联形态。
_REALM_SECTION_RE = re.compile(
    r"^(#{2,4})[ \t]*[^\n]*?(?:修炼境界体系|境界体系|修炼体系)[^\n]*?$", re.M
)
#: 小节内的有序编号条目：``1. **引灵**：说明`` / ``12. **归一（…）**``
_REALM_ITEM_RE = re.compile(r"^[ \t]*\d+[ \t]*[.、][ \t]*\*\*(.+?)\*\*", re.M)
#: 单行内联形态：``境界：凡人 < 练气 < 筑基``
_REALM_INLINE_RE = re.compile(r"(?:境界|修炼体系|境界体系)\s*[:：]\s*(.+)")
_REALM_INLINE_SEP_RE = re.compile(r"[<＜→]")

#: 境界字段名集合（账本事实的 ``field`` 由 LLM 结算产出、命名自由）。
#: 只认**显式**境界字段，避免把 ``cultivation_insight``（心得）之类的同前缀字段
#: 误当境界。命中不了 ⇒ 规则放行（宁漏不误）。
_REALM_FIELD_NAMES = frozenset(
    {
        "realm", "realm_level", "cultivation", "cultivation_level",
        "cultivation_realm", "境界", "修为", "境界修为", "修为境界",
        "修炼境界",
        # ★ 2026-09-24 灵荒工坊实证：结算员把同一概念写成了三个字段名
        # （``cultivation`` / ``修为状态`` / ``修为``）。别名不在集合里 ⇒ 承接锚点
        # 只看到其中一个 ⇒ 漏读（此处 ``修为状态`` 曾被完全忽略）。补入**显式**
        # 带「状态」后缀的境界别名；``_match_realm_index`` 仍要求取值命中世界体系，
        # 故「修为状态=紊乱」这类非境界取值不会被误当境界。
        "修为状态", "修炼状态", "境界状态", "realm_state", "cultivation_state",
    }
)


def _clean_realm_name(raw: str) -> str:
    """清掉境界名里的括注与装饰（``归一（主角独有终极境界）`` → ``归一``）。"""
    name = re.sub(r"[（(][^）)]*[）)]", "", raw or "")
    return name.strip().strip("*：: \t").strip()


def _load_world_realm_order(project_dir: Path) -> list[str]:
    """从 world.md 提取**有序**境界序列（供跨度检测比较"序位"）。

    两种登记形态（前者优先）：
    1. 小节 + 有序编号列表：``## 修炼境界体系`` 下的 ``N. **境界名**：说明``；
    2. 单行内联：``境界：凡人 < 练气 < 筑基``（``<``/``＜``/``→`` 分隔）。

    无法提取 → 返回 ``[]`` ⇒ 调用方放行（对齐 :func:`_load_world_realms`
    "仅当显式定义境界体系才判"的纪律）。重名去重、保序。
    """
    world_path = project_dir / "world.md"
    if not world_path.is_file():
        return []
    try:
        text = world_path.read_text(encoding="utf-8")
    except Exception:  # noqa: BLE001
        return []

    names: list[str] = []
    m = _REALM_SECTION_RE.search(text)
    if m:
        level = len(m.group(1))
        rest = text[m.end():]
        # 截到下一个同级或更高级标题为止（``### 凡修四境`` 这类子标题不截断）
        nxt = re.search(rf"^#{{2,{level}}}[ \t]", rest, re.M)
        body = rest[: nxt.start()] if nxt else rest
        for im in _REALM_ITEM_RE.finditer(body):
            name = _clean_realm_name(im.group(1))
            if name and name not in names:
                names.append(name)
    if len(names) < 2:
        for lm in _REALM_INLINE_RE.finditer(text):
            frag = lm.group(1)
            if not _REALM_INLINE_SEP_RE.search(frag):
                continue
            cand = [
                _clean_realm_name(p)
                for p in _REALM_INLINE_SEP_RE.split(frag)
                if p.strip()
            ]
            cand = [c for c in cand if c and len(c) <= 8]
            if len(cand) >= 2:
                for c in cand:
                    if c not in names:
                        names.append(c)
                break
    return names


def _match_realm_index(text: str, order: list[str]) -> int | None:
    """在 ``text`` 里定位境界名并返回其在 ``order`` 中的序位（最长匹配优先）。"""
    if not text:
        return None
    best: int | None = None
    for i, name in enumerate(order):
        if name and name in text:
            if best is None or len(name) > len(order[best]):
                best = i
    return best


def _load_protagonist_name(project_dir: Path) -> str:
    """从 ``plan.json.character_skeleton`` 取 ``role`` 含「主角」的角色名。"""
    plan_file = project_dir / ".state" / "plan.json"
    if not plan_file.is_file():
        return ""
    try:
        plan = json.loads(plan_file.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001 - 规划缺失/损坏 → 无法定承接主体，规则放行
        return ""
    if not isinstance(plan, dict):
        return ""
    for c in plan.get("character_skeleton") or []:
        if isinstance(c, dict) and "主角" in str(c.get("role") or ""):
            return str(c.get("name") or "").strip()
    return ""


def _carried_realm_index(
    project_dir: Path, order: list[str]
) -> tuple[str, int] | None:
    """承接境界：账本中**主角**的境界事实 → 在 ``order`` 中的序位。

    只认 ``domain=character`` 且 ``subject_id=主角`` 且 ``field`` 属
    :data:`_REALM_FIELD_NAMES` 的事实；字段名或值任一不匹配世界体系 → ``None``
    （放行，宁漏不误）。多条命中取**最高序位**（账本可能留有历史境界事实，
    取已确认推进到的最高值，避免用陈旧的早期值误判）。
    """
    protagonist = _load_protagonist_name(project_dir)
    if not protagonist:
        return None
    try:
        from agent.core.continuity import ContinuityLedgerStore

        ledger = ContinuityLedgerStore(project_dir).load()
    except Exception:  # noqa: BLE001 - 账本不可用 → 承接不可确定，放行
        return None
    best: int | None = None
    for f in ledger.facts:
        if f.domain != "character" or f.subject_id != protagonist:
            continue
        if f.field.strip().lower() not in _REALM_FIELD_NAMES:
            continue
        idx = _match_realm_index(f.value, order)
        if idx is not None and (best is None or idx > best):
            best = idx
    if best is None:
        return None
    return protagonist, best


#: 境界「宣告式」断言：动词与境界名之间**只允许标点/空白**（``突破了。栖气期初期。``）。
#: 与 :data:`_REALM_BREAK` 的区别：后者禁止跨句（``[^，。\n]``），因此抓不到对白里
#: **独立成句**的突破宣告——2026-09-24 灵荒工坊 ch043 实证：正文
#: ``"突破了。栖气期初期。"`` 既没被确定性抽取器落盘、也没被 LLM 结算记入 ⇒
#: 承接锚点停在「引灵」⇒ ch044/ch045 按旧境界写 ⇒ 与 ch043 自相矛盾 ⇒
#: 批末体检判「人设稳定 2.0 / 逻辑漏洞 3.0」两项硬指标不达标 ⇒ 回溯 3 次仍不收敛。
_REALM_DECLARE_RE = re.compile(
    r"(突破|晋升|晋入|踏入|进阶|跨入|修成|炼成|凝成|登临)(?:到|至|为|了|成)?"
    r"[，,、。；;！!？?…—\s“”\"'（）()]{0,4}"
    r"([\u4e00-\u9fa5]{1,4}(?:期|境|阶|层))"
)


def derive_realm_fact(
    project_dir: Path, chapter_text: str
) -> dict[str, str] | None:
    """从本章正文确定性推导**主角**的境界推进事实（返回给账本落盘）。

    为什么需要它（2026-09-24 灵荒工坊 ch043 实证）
    --------------------------------------------
    ``m5_persist._extract_chapter_facts`` 的境界抽取（步骤 6）要求**角色名与境界名
    同句**；正文把突破写成对白里的独立短句（``"突破了。栖气期初期。"``）时两者不在
    同一句 ⇒ 漏抽；LLM 结算同样没记 ⇒ 承接锚点在账本里**永不前进** ⇒ 后续章节继续
    按旧境界写 ⇒ 与已发生正文自相矛盾（评委据此判人设崩坏/逻辑漏洞，属硬指标）。

    判定口径（宁漏不误）
    ------------------
    - 只追认**主角**（``plan.json`` 里 role 含「主角」）的境界事实；
    - 只追认「承接 + **恰好 1 档**」的推进：与承接同档（无变化）、低于承接（回退）、
      高于承接 1 档以上（**越级跳变**）一律不追认——越级留给 :func:`_rule_realm_span`
      判 BLOCK，代码不得替它追认；
    - 世界体系取不到（``< 3`` 档）/ 主角不可知 / 承接不可确定 ⇒ 返回 ``None``；
    - 归属：取声明点之前最近的角色名，不是主角（配角越级/他人突破）⇒ 不追认。

    Returns:
        命中返回 ``{"subject_id", "field", "value", "evidence"}``（``domain`` 恒为
        ``character``、``field`` 为规范字段名 ``realm``）；未命中返回 ``None``。
    """
    if not chapter_text:
        return None
    order = _load_world_realm_order(project_dir)
    if len(order) < 3:
        return None
    protagonist = _load_protagonist_name(project_dir)
    if not protagonist:
        return None
    carried = _carried_realm_index(project_dir, order)
    if carried is None:
        return None  # 承接不可确定 ⇒ 无从判断"是否只推进一档"，不追认
    holder, base_idx = carried
    if holder != protagonist:
        return None
    names = [*_load_character_index(project_dir), holder]

    hit: tuple[str, str] | None = None  # (value, evidence)，取最后一次（章末为准）
    for m in _REALM_DECLARE_RE.finditer(chapter_text):
        value = _clean_realm_name(m.group(2))
        idx = _match_realm_index(value, order)
        if idx != base_idx + 1:
            continue  # 无变化 / 回退 / 越级（>=2 档）均不追认
        near = _nearest_registered_name(
            chapter_text, m.start(), names, _REALM_SPAN_SUBJECT_WINDOW
        )
        if near and near != holder:
            continue  # 声明归属他人（配角突破）⇒ 不记到主角头上
        hit = (value, m.group(0))
    if hit is None:
        return None
    return {
        "subject_id": protagonist,
        "field": "realm",
        "value": hit[0],
        "evidence": hit[1][:100],
    }


def _nearest_registered_name(
    text: str, pos: int, names: list[str], window: int
) -> str:
    """``pos`` 之前 ``window`` 字符内**最近**出现的已登记角色名（无则空串）。"""
    seg = text[max(0, pos - window):pos]
    best_name = ""
    best_at = -1
    best_len = 0
    for name in names:
        if not name:
            continue
        for sub in (_cjk_substrings(name) or [name]):
            at = seg.rfind(sub)
            if at < 0:
                continue
            if at > best_at or (at == best_at and len(sub) > best_len):
                best_at, best_name, best_len = at, name, len(sub)
    return best_name


def _collect_mentions(chapter_text: str, index: dict[str, dict[str, Any]]) -> list[tuple[str, int, int]]:
    """收集正文中所有角色称呼的出现位置 (name, start, end)。"""
    mentions: list[tuple[str, int, int]] = []
    seen_spans: set[tuple[int, int]] = set()
    for name in index:
        for sub in _cjk_substrings(name):
            for m in re.finditer(re.escape(sub), chapter_text):
                span = (m.start(), m.end())
                if span in seen_spans:
                    continue
                seen_spans.add(span)
                mentions.append((name, m.start(), m.end()))
    return mentions


#: 主体与「生死断言」之间允许出现的字符（连接词/副词/数量时间/标点）。
#: 只要空隙里出现别的汉字，就说明真正的主语多半是**未被登记**的实体 —— 视为主体不明。
_SUBJECT_FILLER: frozenset[str] = frozenset(
    "，,。、：:；;！!？?…—～~　 \t\"'“”‘’()（）[]【】〈〉《》·"
    "的了早就已经都也其此那人那位名是而且在当时候终于实原来看还才便即为因所被把"
    "着过我你您谁全曾前后之内以间中上下里外更又再没有不无"
    "零一二三四五六七八九十百千万两数半余多年月日天时分秒世纪载岁顿个"
)
#: 主体槽与断言之间的最大字符间距（超过即认定主体离得太远，不归因）。
_SUBJECT_GAP_MAX: int = 8


def _assertion_subject(
    a_start: int,
    mentions: list[tuple[str, int, int]],
    text: str,
    max_gap: int = _SUBJECT_GAP_MAX,
) -> tuple[str, int] | None:
    """把「生死断言」归因到**紧邻前置**的已登记角色；主体不明时返回 ``None``。

    Args:
        a_start: 断言匹配的起始下标。
        mentions: ``(name, start, end)`` 列表（仅含**已登记**角色）。
        text: 章节正文。
        max_gap: 主体与断言之间允许的最大字符数。

    Returns:
        ``(角色名, 间距)``；找不到可信主体时 ``None``。

    2026-09-15 主体锚定（灵荒薪传 ch017-024 假失败复盘）
    --------------------------------------------------
    旧实现用「24 字半径内找**最近**角色名」的方式归因，于是
    「**周德顺**已经死了」（周德顺是配角、不在 ``characters/`` 索引里）的断言
    被扣到附近**有名有姓**的角色头上 —— 活着的主角被判「已故」，还与其关系网
    「合作」活跃边冲突，连报 ch17/18/19/21/24 五章，且**重写无法消除**
    （新稿照样写「周德顺死了」）。

    本函数把归因改成「主体槽」判定：断言之前必须有一个已登记角色，且两者之间
    只允许出现连接词/副词/数量时间/标点。空隙里一旦出现其它汉字（多半正是那个
    未登记配角的姓名），就判**主体不明**并放弃断言 ——
    **宁可漏报，不可栽赃**（本规则产出的 BLOCK 会阻断落盘，误报代价远高于漏报）。
    """
    best: tuple[str, int] | None = None
    for name, _s, e in mentions:
        if e > a_start:
            continue                       # 只认前置主体（中文「X已经死了」语序）
        gap = text[e:a_start]
        if len(gap) > max_gap:
            continue
        if any(ch not in _SUBJECT_FILLER for ch in gap):
            continue                       # 空隙含实体性汉字 → 主体不是此人
        if best is None or e > best[1]:
            best = (name, e)               # 取**最近**的前置主体
    if best is None:
        return None
    return (best[0], a_start - best[1])


# ============================================================================
# 规则实现
# ============================================================================

def _rule_field_conflict(ctx: dict[str, Any], arbiter: Any) -> list[Conflict]:
    """字段冲突检测：委托 ConflictArbiter.check_new_setting（T-5）"""
    if arbiter is None:
        return []
    new_setting = ctx.get("new_setting", "")
    if not new_setting:
        return []
    report = arbiter.check_new_setting(new_setting, ctx.get("subline_id"))
    conflicts: list[Conflict] = []
    for c in getattr(report, "conflicts", []) or []:
        conflicts.append(Conflict(
            rule_id="field_conflict",
            severity=Severity.BLOCK if getattr(c, "is_block", True) else Severity.WARN,
            description=str(getattr(c, "description", "")),
            affected_chapters=getattr(c, "affected_chapters", []) or [],
            suggestions=getattr(c, "suggestions", []) or [],
        ))
    return conflicts


def _rule_timeline_conflict(ctx: dict[str, Any], checker: "ConsistencyChecker") -> list[Conflict]:
    """POST_WRITE：比对章节正文与 characters/*.md 角色生死/时间线真源。

    抓「角色在档案中在世/后期才牺牲，本章却称其已故」这类矛盾（如 ch049 周伯）。
    """
    chapter_text = ctx.get("chapter_text", "")
    if not chapter_text:
        return []
    project_dir = checker.project_dir
    index = _load_character_index(project_dir)
    if not index:
        return []

    mentions = _collect_mentions(chapter_text, index)
    if not mentions:
        return []

    conflicts: list[Conflict] = []
    seen: set[tuple[str, str]] = set()
    for dm in _DEATH_ASSERTION.finditer(chapter_text):
        subject = _assertion_subject(dm.start(), mentions, chapter_text)
        if subject is None:
            continue  # 主体不明（多为未登记配角）→ 不下断言，避免栽赃在场角色
        name = subject[0]
        status = index[name]["status"]
        if status in ("ALIVE", "ALIVE_THEN_DIES"):
            key = ("death", name)
            if key in seen:
                continue
            seen.add(key)
            conflicts.append(Conflict(
                rule_id="timeline_conflict",
                severity=Severity.BLOCK,
                description=(
                    f"角色「{name}」在角色档案中为"
                    f"{'在世' if status == 'ALIVE' else '后期才牺牲（当前应存活）'}，"
                    f"但本章称其「{dm.group(0)}」，时间线/生死矛盾。"
                ),
                affected_chapters=[],
                suggestions=[
                    "以 characters/ 角色档案为唯一真源：若本章需交代其死亡，"
                    "须先更新角色档案的生死状态与对应章节，再回写正文。",
                ],
            ))
    for am in _ALIVE_ASSERTION.finditer(chapter_text):
        subject = _assertion_subject(am.start(), mentions, chapter_text)
        if subject is None:
            continue  # 同死亡断言：主体不明则不下结论
        name = subject[0]
        status = index[name]["status"]
        if status == "DEAD_AT_START":
            key = ("alive", name)
            if key in seen:
                continue
            seen.add(key)
            conflicts.append(Conflict(
                rule_id="timeline_conflict",
                severity=Severity.BLOCK,
                description=(
                    f"角色「{name}」在角色档案中为故事开始前已故，"
                    f"但本章称其「{am.group(0)}」，生死矛盾。"
                ),
                affected_chapters=[],
                suggestions=["以角色档案为唯一真源，统一其生死状态。"],
            ))
    return conflicts


def _rule_relation_conflict(ctx: dict[str, Any], checker: "ConsistencyChecker") -> list[Conflict]:
    """POST_WRITE：比对章节正文与 relations/graph.md 关系网活跃边。

    若本章断言某角色已故，但关系网存在该角色的互动型活跃边（暗示其仍活跃），则告警。
    """
    chapter_text = ctx.get("chapter_text", "")
    if not chapter_text:
        return []
    project_dir = checker.project_dir
    edges = _load_graph_edges(project_dir)
    index = _load_character_index(project_dir)
    if not edges or not index:
        return []

    # 先定位正文中被断言「已故」的角色（死亡断言归属到最近角色称呼）
    mentions = _collect_mentions(chapter_text, index)
    if not mentions:
        return []
    dead_here: set[str] = set()
    for dm in _DEATH_ASSERTION.finditer(chapter_text):
        subject = _assertion_subject(dm.start(), mentions, chapter_text)
        if subject is not None:
            dead_here.add(subject[0])

    if not dead_here:
        return []

    # graph.md 用单字 ID 指向角色；需要 ID->显示名 映射
    graph_path = project_dir / "relations" / "graph.md"
    id_to_name: dict[str, str] = {}
    try:
        gtext = graph_path.read_text(encoding="utf-8")
        node_sec = re.search(r"## 节点(.*?)(?:\n## |\Z)", gtext, re.S)
        if node_sec:
            for line in node_sec.group(1).splitlines():
                if not line.startswith("|"):
                    continue
                parts = [x.strip() for x in line.strip().strip("|").split("|")]
                if len(parts) >= 3 and re.fullmatch(r"[A-Z]", parts[0]):
                    id_to_name[parts[0]] = parts[1]
    except Exception:  # noqa: BLE001
        id_to_name = {}  # noqa: SILENT_DEGRADE

    conflicts: list[Conflict] = []
    for name in dead_here:
        for e in edges:
            ename = id_to_name.get(e["from"], e["from"])
            tname = id_to_name.get(e["to"], e["to"])
            if name not in (ename, tname):
                continue
            if e["type"] in _GRAPH_INTERACTIVE_TYPES:
                conflicts.append(Conflict(
                    rule_id="relation_conflict",
                    severity=Severity.WARN,
                    description=(
                        f"关系网(graph.md)显示「{name}」存在互动型关系"
                        f"（{e['type']}，起于 {e['start']}），但本章称其已故，关系网一致性存疑。"
                    ),
                    affected_chapters=[],
                    suggestions=["核实该角色生死与关系网边是否同步更新。"],
                ))
                break
    return conflicts


def _rule_golden_finger_overstep(ctx: dict[str, Any], checker: "ConsistencyChecker") -> list[Conflict]:
    """POST_WRITE：比对章节正文与角色「禁用词」（金手指/系统越界）。

    如周伯角色档注明禁用词含「系统、金手指」，本章却让其触发/使用系统，则告警。
    """
    chapter_text = ctx.get("chapter_text", "")
    if not chapter_text:
        return []
    project_dir = checker.project_dir
    index = _load_character_index(project_dir)
    gf_chars = {
        name: info["forbidden"]
        for name, info in index.items()
        if any(t in ("系统", "金手指", "外挂") for t in info["forbidden"])
    }
    if not gf_chars:
        return []

    conflicts: list[Conflict] = []
    for name, forbidden in gf_chars.items():
        mention = None
        for sub in _cjk_substrings(name):
            m = re.search(re.escape(sub), chapter_text)
            if m:
                mention = (sub, m.start())
                break
        if mention is None:
            continue
        sub, pos = mention
        window = chapter_text[max(0, pos - 40) : pos + len(sub) + 40]
        inv = _GOLDEN_FINGER_INVOKE.search(window)
        if inv:
            conflicts.append(Conflict(
                rule_id="golden_finger_overstep",
                severity=Severity.WARN,
                description=(
                    f"角色「{name}」的禁用词含「{','.join(forbidden)}」，"
                    f"但本章出现「{inv.group(0)}」，疑似金手指/系统越界。"
                ),
                affected_chapters=[],
                suggestions=["该角色不应使用系统/金手指类能力，请改写或更新角色设定。"],
            ))
    return conflicts


def _rule_realm_overstep(ctx: dict[str, Any], checker: "ConsistencyChecker") -> list[Conflict]:
    """POST_WRITE：比对章节正文与 world.md 境界体系。

    仅当 world.md 显式定义境界列表时才触发；若本章宣称的境界不在体系内则告警（防误报）。
    """
    chapter_text = ctx.get("chapter_text", "")
    if not chapter_text:
        return []
    project_dir = checker.project_dir
    realms = _load_world_realms(project_dir)
    if not realms:
        return []

    conflicts: list[Conflict] = []
    for m in _REALM_BREAK.finditer(chapter_text):
        claimed = m.group(1).strip()
        if claimed and claimed not in realms:
            conflicts.append(Conflict(
                rule_id="realm_overstep",
                severity=Severity.WARN,
                description=(
                    f"本章宣称突破至「{claimed}」，但 world.md 境界体系中未登记该境界"
                    f"（已登记：{', '.join(sorted(realms))}），疑似境界越级。"
                ),
                affected_chapters=[],
                suggestions=["核实境界体系，或先在 world.md 中补登该境界。"],
            ))
    return conflicts


#: 主体锚定窗口：境界突破声明之前多少字符内找"最近已登记角色"。
#: 突破常在账册/独白段里出现（不重复点名），故窗口远比生死断言的 8 字宽。
_REALM_SPAN_SUBJECT_WINDOW = 200


def _rule_realm_span(ctx: dict[str, Any], checker: "ConsistencyChecker") -> list[Conflict]:
    """POST_WRITE：**境界跨度**检测——承接境界 → 本章宣称境界跳了几档。

    为什么需要它（2026-09-24 灵荒工坊 ch41 实证）
    --------------------------------------------
    :func:`_rule_realm_overstep` **不是**跨度检测：它只校验"正文宣称的境界名是否在
    world.md 清单内"，且严重度仅为 ``WARN``；对"一章内 引灵→淳真 连跳两境"
    （两个境界名都在体系内）完全无感。结果是越级跳变**落盘固化**，
    批末体检才发现，回退也无法收敛。

    判定口径（宁漏不误）
    ------------------
    - 承接境界从**连续性账本**取（主角的境界事实）；账本无该事实 / 主角不可知
      → 放行（无法确定基线就不判）。
    - 世界体系序位从 ``world.md`` 的**有序**境界列表取；取不到 → 放行。
    - 正文宣称的境界若**不在**体系内，交给 :func:`_rule_realm_overstep` 报 WARN，
      本规则放行（避免同因两报）。
    - 序位差 ``>= 2`` 才 BLOCK（差 1 档是"连续演进一境"，符合
      ``CARRIED_TO_WRITER`` 的仲裁口径）。
    - 声明归属**非承接主体**（窗口内最近的角色名不是主角）→ 放行，
      避免把配角的越级算到主角头上。
    """
    chapter_text = ctx.get("chapter_text", "")
    if not chapter_text:
        return []
    order = _load_world_realm_order(checker.project_dir)
    if len(order) < 3:
        # 少于 3 档时"跨度 >=2"恒等于"至少跳两档"，误报风险高，放行。
        return []
    carried = _carried_realm_index(checker.project_dir, order)
    if carried is None:
        return []
    holder, base_idx = carried
    # 主体索引：已登记角色 + 承接主体（承接主体可能未被登记）
    names = [*_load_character_index(checker.project_dir), holder]

    conflicts: list[Conflict] = []
    reported: set[str] = set()
    for m in _REALM_BREAK.finditer(chapter_text):
        claimed = m.group(1).strip()
        claimed_idx = _match_realm_index(claimed, order)
        if claimed_idx is None:
            continue
        if claimed_idx - base_idx < 2:
            continue
        near = _nearest_registered_name(
            chapter_text, m.start(), names, _REALM_SPAN_SUBJECT_WINDOW
        )
        if near and near != holder:
            continue
        if claimed in reported:
            continue
        reported.add(claimed)
        conflicts.append(Conflict(
            rule_id="realm_span",
            severity=Severity.BLOCK,
            description=(
                f"本章宣称突破至「{claimed}」，但承接境界（{holder}）为"
                f"「{order[base_idx]}」，两者在 world.md 境界体系中相差 "
                f"{claimed_idx - base_idx} 档，属**无契机的越级跳变**"
                f"（章际连续性断裂）。若要合法推进，须先在正文章节内写出"
                f"「{order[base_idx + 1]}」这一境并落盘，再于后续章节推进到"
                f"「{claimed}」；确有设计依据（机缘/传承）的，须先在设定真源中登记。"
            ),
            affected_chapters=[],
            suggestions=[
                f"把本章境界改写为承接值「{order[base_idx]}」或至多推进一境"
                f"「{order[base_idx + 1]}」；跨多章的境界目标不得在单章内完成。",
                "若确需跳境，请先补写中间的突破章节并在世界书/角色档案中登记该次跃迁。",
            ],
        ))
    return conflicts


def _rule_presence_conflict(ctx: dict[str, Any], checker: "ConsistencyChecker") -> list[Conflict]:
    """POST_WRITE：问题债务登记簿（issue_debt）的 presence_ban 强制执行。

    被禁主体（死亡/离场/封印等已确认不应在当前场景出现的角色）在本章正文
    出现即 BLOCK——这是"确认问题登记后强制约束后续章节"闭环的门禁端
    （2026-09-12：此前"下落/离场"只有事实记录没有约束消费，死人/离场角色
    再出场只能靠 RAG 碰运气）。登记簿损坏/缺失时静默放行（G3）。
    """
    chapter_text = ctx.get("chapter_text", "")
    if not chapter_text:
        return []
    try:
        from agent.core.story.issue_debt import KIND_PRESENCE_BAN, IssueDebtStore

        debts = IssueDebtStore(checker.project_dir).load().open_items(
            kinds=[KIND_PRESENCE_BAN]
        )
    except Exception:  # noqa: BLE001 - 登记簿异常不阻断（写时注入端同样降级）
        return []
    conflicts: list[Conflict] = []
    for d in debts:
        subject = (d.subject or "").strip()
        if subject and subject in chapter_text:
            conflicts.append(Conflict(
                rule_id="presence_conflict",
                severity=Severity.BLOCK,
                description=(
                    f"被禁主体「{subject}」出现在本章正文，但问题债务 {d.id} 已确认"
                    f"其不应出场（{d.constraint}"
                    f"{'，登记于第' + str(d.registered_ch) + '章' if d.registered_ch else ''}）。"
                    "请删除/改写相关情节，或先走设定更新流程显性推翻该禁令并销账。"
                ),
                affected_chapters=[],
                suggestions=[
                    f"处理方式二选一：① 按债务约束改写本章（移除{subject}的出场）；"
                    f"② 若剧情确需其出场，用 issue-debt resolve {d.id} 销账后再写。"
                ],
            ))
    return conflicts


class ConsistencyChecker:
    """一致性校验器（T-5：可配置 rule 集，至少 1 条委托 ConflictArbiter）"""

    def __init__(self, project_dir: Path) -> None:
        self.project_dir = Path(project_dir)
        self._arbiter: "Any" = None

    # ------ 内置规则集（可配置，至少 1 条委托 ConflictArbiter）------
    def _builtin_rules(self) -> list[Any]:
        """返回内置一致性规则（每项 check(ctx, arbiter) -> list[Conflict]）"""
        return [
            _ConsistencyRule(
                id="field_conflict",
                name="字段冲突",
                severity=Severity.BLOCK,
                check=_rule_field_conflict,
            ),
            _ConsistencyRule(
                id="timeline_conflict",
                name="时间线冲突",
                severity=Severity.BLOCK,
                check=lambda c, a: _rule_timeline_conflict(c, self),
            ),
            _ConsistencyRule(
                id="relation_conflict",
                name="关系网一致性",
                severity=Severity.WARN,
                check=lambda c, a: _rule_relation_conflict(c, self),
            ),
            _ConsistencyRule(
                id="golden_finger_overstep",
                name="金手指越界",
                severity=Severity.WARN,
                check=lambda c, a: _rule_golden_finger_overstep(c, self),
            ),
            _ConsistencyRule(
                id="realm_overstep",
                name="境界越级",
                severity=Severity.WARN,
                check=lambda c, a: _rule_realm_overstep(c, self),
            ),
            _ConsistencyRule(
                id="realm_span",
                name="境界跨度越级",
                severity=Severity.BLOCK,
                check=lambda c, a: _rule_realm_span(c, self),
            ),
            _ConsistencyRule(
                id="presence_conflict",
                name="禁出场主体违规",
                severity=Severity.BLOCK,
                check=lambda c, a: _rule_presence_conflict(c, self),
            ),
        ]

    def _get_arbiter(self) -> "Any":
        """懒加载 ConflictArbiter（同包 quality/conflict_service）"""
        if self._arbiter is None:
            from agent.core.quality.consistency.conflict_service import ConflictArbiter

            self._arbiter = ConflictArbiter(self.project_dir)
        return self._arbiter

    def check(
        self,
        trigger: CheckTrigger,
        ctx: dict[str, Any] | None = None,
    ) -> ConsistencyReport:
        """执行校验（T-5：遍历内置 rule 集，不再 raise）

        Args:
            trigger: 校验时机
            ctx: 上下文（设定变更内容 / 章节内容等）

        Returns:
            ConsistencyReport
        """
        ctx = ctx or {}
        conflicts: list[Conflict] = []
        # 仅当存在设定变更时才惰性构造 ConflictArbiter（field_conflict 需要），
        # 避免 post-write 等无关触发去初始化/连接 LLM 造成的开销与潜在挂起。
        arbiter = self._get_arbiter() if ctx.get("new_setting") else None
        for rule in self._builtin_rules():
            try:
                rule_conflicts = rule.check(ctx, arbiter)
            except Exception:  # noqa: BLE001 - 单条规则异常不影响整体校验
                continue  # noqa: SILENT_DEGRADE
            if rule_conflicts:
                conflicts.extend(rule_conflicts)
        passed = not any(c.severity == Severity.BLOCK for c in conflicts)
        return ConsistencyReport(passed=passed, trigger=trigger, conflicts=conflicts)

    def assess_architecture_impact(self) -> ConsistencyReport:
        """架构修订时评估下游影响（M14 F14.7，T-5：返回空壳报告）"""
        return ConsistencyReport(passed=True, trigger=CheckTrigger.PRE_WRITE, conflicts=[])


@dataclass
class _ConsistencyRule:
    """内置一致性规则项"""

    id: str
    name: str
    severity: Severity
    check: Any  # Callable[[dict, ConflictArbiter | None], list[Conflict]]


def recheck_rule(
    project_dir: "Path | str", rule_id: str, chapter_num: int
) -> "list[Conflict] | None":
    """在指定章原文上重跑**单条**内置规则（问题债务复查销账用，2026-09-15）。

    为什么需要它
    ------------
    ``watch`` 级一致性警告会被登记成问题债务，再由 ``render_constraints`` 注入
    每一章的 writer。这是必要的（否则问题静默复发），但**债务此前只进不出**：
    规则本身被修复后，历史债务仍原样注入，规划者/写手被一个已不存在的矛盾
    持续牵着走（实测灵荒薪传：``relation_conflict`` 主体锚定修复后，8 条
    "林凡/沈长风已故"误报仍挂在账上，并成为批间复规划裁决的写作焦点）。

    Returns:
        命中列表；``None`` 表示**无法复查**（规则不存在 / 章节文件缺失 /
        正文为空）。调用方必须显式区分「复查后不再命中」与「没复查成」——
        把后者当前者会静默销掉真实债务（同族纪律：失败必须显性化）。
    """
    from agent.core.story.chapters import strip_frontmatter

    checker = ConsistencyChecker(project_dir)
    rule = next((r for r in checker._builtin_rules() if r.id == rule_id), None)
    if rule is None:
        return None
    path = Path(project_dir) / "chapters" / f"ch{int(chapter_num):03d}.md"
    if not path.exists():
        return None
    try:
        text = strip_frontmatter(path.read_text(encoding="utf-8")).strip()
    except OSError:
        return None
    if not text:
        return None
    try:
        return list(rule.check({"chapter_text": text}, None))
    except Exception as e:  # noqa: BLE001 - 规则自身异常 ⇒ 无法复查（不是"不再命中"）
        from agent.core.infra.degrade import degrade

        degrade(
            "consistency.recheck_rule",
            f"债务复查规则执行失败（rule={rule_id} ch={chapter_num}），"
            "该条债务保持未复查状态（不自动销账）",
            e,
            level=logging.DEBUG,
        )
        return None
