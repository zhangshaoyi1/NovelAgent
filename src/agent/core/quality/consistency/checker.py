"""一致性校验器（5.5）

职责：在设定更新、写作前、章节产出后三个时机执行规则校验。

校验项（内置规则集，均为真实实现）：
    - field_conflict：设定字段冲突（委托 ConflictArbiter.check_new_setting）
    - timeline_conflict：角色生死 / 时间线矛盾（POST_WRITE，比对 characters/*.md 真源）
    - relation_conflict：关系网一致性（POST_WRITE，比对 relations/graph.md 活跃边）
    - golden_finger_overstep：金手指/系统越界（POST_WRITE，比对角色「禁用词」）
    - realm_overstep：境界越级（POST_WRITE，比对 world.md 境界体系）

冲突输出：一致性影响报告（冲突条目 + 涉及章节 + 处理建议）
"""

from __future__ import annotations

import re
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
            continue
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


def _nearest_mention(
    a_start: int, a_end: int, mentions: list[tuple[str, int, int]], max_dist: int = 24
) -> tuple[str, float] | None:
    """返回离某断言最近的角色称呼 (name, dist) 或 None（避免把死亡断言张冠李戴）。"""
    best: str | None = None
    best_d: float | None = None
    a_center = (a_start + a_end) / 2
    for name, s, e in mentions:
        d = abs(((s + e) / 2) - a_center)
        if d <= max_dist and (best_d is None or d < best_d):
            best_d = d
            best = name
    return (best, best_d) if best is not None else None


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
        near = _nearest_mention(dm.start(), dm.end(), mentions)
        if near is None:
            continue
        name = near[0]
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
        near = _nearest_mention(am.start(), am.end(), mentions)
        if near is None:
            continue
        name = near[0]
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
        near = _nearest_mention(dm.start(), dm.end(), mentions)
        if near is not None:
            dead_here.add(near[0])

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
        id_to_name = {}

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
                continue
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
