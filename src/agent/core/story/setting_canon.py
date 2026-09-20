"""设定台账（Setting Canon）—— 把写作中涌现的「定义性约束」结构化沉淀下来。

背景（2026-09-12 五灵破归档反复回退复盘）：
    全书 185 章写完，``world.md`` 最后修改时间仍停留在开书当天（2026-09-07 09:10），
    全仓对 world.md 只有 ``if not exists`` 存在性检查、**没有任何一处回写**。
    于是写作过程中涌现的新设定（阵盘 / 镇灵符 / 镇魔符 / 导引纹 / 五行属性……）
    只存在于正文章节里，Writer 下一章要遵守它们只能靠 RAG 检索碰运气，
    隔几章就被模型重新「发明」一遍，形成章间矛盾 —— 这正是人设/设定/连贯性
    硬指标长期不达标（43 次体检仅 1 次通过）的根因。

本模块补上缺失的那一环：**设定只进 → 现在也出得去**。三件事：

1. :func:`extract_definitions`  —— 从章节正文确定性抽取「定义性约束」
   （X 是 Y / X 的 A 是 B / X 用于 Y / 「X」是 Y ……），主体取**实体名**，
   而不是既有 ``m5_persist`` 那种以章节号为 subject、只抽量词的噪音事实。
2. :class:`SettingCanon` —— 台账存取、去重、以及**同主体同属性不同取值**的
   冲突检测（冲突即为体检可直接消费的证据）。
3. :meth:`SettingCanon.sync_to_world_md` —— 把台账回写到 ``world.md`` 的托管区块，
   让设定文档随写作滚动更新，不再冻结在开书当天。

纯规则实现，不依赖 LLM；任何异常都不阻断写章（调用方负责降级）。
"""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Iterable

from agent.core.infra.degrade import degrade

CANON_FILE = ".state/continuity/setting_canon.json"
WORLD_CANON_BEGIN = "<!-- SETTING_CANON:BEGIN -->"
WORLD_CANON_END = "<!-- SETTING_CANON:END -->"

_SENT_SPLIT = re.compile(r"[。！？!?\n]")

# 术语界定符号：引号里的词即便不在已知实体表，也算「本章涌现的新设定」
_TERM_PATTERN = re.compile(r"[「『“\"]([^」』”\"\n]{1,12})[」』”\"]")
# 形如「术语」的判定：纯汉字/字母/数字/间隔号，2~8 字
_TERM_SHAPE = re.compile(r"^[\u4e00-\u9fa5A-Za-z0-9·\-]{2,8}$")
# 对话口气/虚词——引用号在真实语料里常成对出现在对白上，会把整句对白切进来
# （实况：``"能恢复。"`` ``"我知道。"`` ``"撤不撤。"`` 被当成实体名），必须挡掉。
_TERM_BAD = re.compile(
    r"[的了着过在正接来去又并将被把对向从和与或是那此我你他她们它是否没不想要会能说知道"
    r"个很太就都也还只吗呢吧啊]"
)
# 主体首字为虚词/连词/副词/指代 → 是切错的片段（实况：「但转化率」「然后」「至少」「两人看到」）
_SUBJECT_BAD_HEAD = re.compile(
    r"^(?:但|而|才|则|就|也|都|还|又|却|于是|然后|接着|所以|因此|至少|最高|最|很|太|以|使|让|"
    r"把|被|和|与|或|是|在|从|对|为|因|要|想|能|会|这|那|其|此|者|他|她|它|我|你|们|一|个|某|"
    r"每|各|另|同|再|既|若|如|虽|于|至|由|给|向|往|朝|跟|替|当|连|甚至|无论|不管|只要|只有|"
    r"如果|虽然|但是|已|曾|将|正|总|常|非|未|无|有|找|两|"
    r"分别|名单|额头|脸上|身上|心里|手里|众人|有人|各自|所有|整个|这些|那些|一切|许多|不少)"
)
# 主体尾字为虚词/方位词 → 同样切错（实况：「指印才」「名单上」）
_SUBJECT_BAD_TAIL = re.compile(
    r"(?:才|但|而|则|就|也|都|还|又|却|的|是|在|为|与|和|或|把|被|让|上|下|里|外|中|前|后|满)$"
)


def is_term_like(text: str) -> bool:
    """判断一段引号内容是否像「术语/实体名」（而非被切进来的对白片段）。

    背景（2026-09-12）：正文对白用 ASCII 引号 ``"…"``，与中文引号同为成对符号，
    正则配对时会把两段对白之间的文字整段当成引号内容，抽出 ``能恢复。`` 这类
    噪音主体。要求「纯词形 + 无虚词/口气词」后再用。
    """
    t = _clean(text)
    if not _TERM_SHAPE.match(t):
        return False
    return not _TERM_BAD.search(t)


def is_term_subject(text: str) -> bool:
    """判断一个候选主体是否像「设定名」（用于放行非角色实体，如 阵盘 / 导引纹）。

    收紧前 ``_absorb`` 只认「已知角色 ∪ 引号术语」，导致正文里真实涌现、但既非角色
    也没加引号的设定名（阵盘 / 导引纹 / 五行灵脉）**一条都抽不到**（真实语料回放：
    ch176-185 共抽出 2 条，全是比喻噪音）。放宽为「词形合法 + 无虚词」，
    既能收住设定名，也挡得住「声音很轻」这类叙述片段。
    """
    t = _clean(text)
    if not _TERM_SHAPE.match(t):
        return False
    if _TERM_BAD.search(t) or _SUBJECT_BAD_HEAD.match(t) or _SUBJECT_BAD_TAIL.search(t):
        return False
    return True


_NOISE_VALUE_PREFIX = ("像", "在", "从", "把", "被", "说", "做", "让", "给", "有", "没", "不")
# 取值以「的/了/着」收尾 → 是形容词性描写而非可执行的设定约束（「颜色是暗红的」）
_NOISE_VALUE_TAIL = ("的", "了", "着")
# 属性槽：X 的 <属性> 是 Y
# 收紧（2026-09-12，真实语料回放）：
#   - 属性里不得出现标点（否则「林凡的声音很轻，轻得像…」整段被当属性）；
#   - 排除「像」（"像是…" 是比喻，不是定义）；
#   - 长度上限 6，避免把长描述当属性名。
_ATTR_SLOT = r"(?:的([^\s的是为，。；：、！？“”\"'像]{1,6}?))?"
# 定义动词：带负向断言，排除「像/不/算/好/要/真/可/总/因/认/作/以/成/变」+「是」的组合
# （“像是在陈述”“不是”“算是”都不是定义）。
_DEF_VERB = (
    r"(?:(?<![像不算好要真可总因认作以成变])(?:是|就是|指的是|即是|为|意味着|代表|称作|称为|名叫))"
)

# （正则, 主语组号, 属性组号(可为 None), 取值组号）
_DEF_PATTERNS: tuple[tuple[re.Pattern[str], int, int | None, int], ...] = (
    # X的<属性>是Y
    (re.compile(rf"([^\s，。；：、]{{1,12}}?){_ATTR_SLOT}{_DEF_VERB}([^\s，。；！？]{{2,40}})"), 1, 2, 3),
    # X 用来 / 用于 / 的作用是 Y
    (
        re.compile(r"([^\s，。；：、]{1,12}?)(?:用来|用于|的作用是|的功能是|的用途是)([^\s，。；！？]{2,40})"),
        1,
        None,
        2,
    ),
    # 所谓 X，是 Y
    (
        re.compile(rf"所谓([^\s，。；：、]{{1,12}}?)[，,]?{_DEF_VERB}([^\s，。；！？]{{2,40}})"),
        1,
        None,
        2,
    ),
)

# 主语/宾语位置的代词、虚词——命中即判为噪音，不入台账
_NOISE_SUBJECTS = frozenset(
    {
        "他", "她", "它", "他们", "她们", "它们", "这", "那", "这个", "那个",
        "这里", "那里", "什么", "怎么", "为什么", "谁", "自己", "大家", "有人",
        "没有", "不是", "就是", "也许", "可能", "应该", "因为", "所以", "但是",
        "一个", "一种", "一样", "事情", "东西", "时候", "问题", "结果", "声音",
    }
)
_NOISE_VALUES = frozenset({"什么", "这样", "那样", "真的", "假的", "一样", "如此", "这个", "那个"})


@dataclass
class SettingEntry:
    """一条定义性设定约束。"""

    subject: str          # 实体名，如「阵盘」
    attribute: str        # 属性槽，如「水属性」；无属性时为 ""
    value: str            # 取值，如「净化」
    chapter: int          # 首次确立的章号
    evidence: str = ""    # 原文证据
    updated_chapter: int = 0  # 最近一次被重申/改写的章号

    @property
    def key(self) -> tuple[str, str]:
        return (self.subject, self.attribute)

    def label(self) -> str:
        return f"{self.subject}·{self.attribute}" if self.attribute else self.subject

    def render(self) -> str:
        ch = self.updated_chapter or self.chapter
        return f"- {self.label()} = {self.value}（ch{ch}）"


@dataclass
class SettingConflict:
    """同一主体同一属性出现了不同取值——章间矛盾的硬证据。"""

    subject: str
    attribute: str
    old_value: str
    old_chapter: int
    new_value: str
    new_chapter: int
    evidence: str = ""

    def render(self) -> str:
        return (
            f"- {self.subject}·{self.attribute or '定义'}：ch{self.old_chapter} 说「{self.old_value}」，"
            f"ch{self.new_chapter} 说「{self.new_value}」——同一设定前后冲突"
        )


def _clean(text: str) -> str:
    return re.sub(r"[\s\"'「」『』“”]", "", text or "").strip("，,。.；;：:、")


def _is_noise(subject: str, value: str) -> bool:
    s = _clean(subject)
    v = _clean(value)
    if not s or not v:
        return True
    if s in _NOISE_SUBJECTS or v in _NOISE_VALUES:
        return True
    if len(s) < 2 or len(v) < 2:
        return True
    # 主体必须是「词形合法」的设定名/实体名（挡掉「声音很轻」这类叙述片段）
    if not is_term_subject(s):
        return True
    # 取值以比喻/介词/否定开头 → 是叙述而非定义（「像是在陈述」「从胸腔深处挤出来的」）
    if v.startswith(_NOISE_VALUE_PREFIX):
        return True
    # 取值以「的/了/着」收尾 → 形容词性描写（「颜色是暗红的」），不构成硬约束
    if v.endswith(_NOISE_VALUE_TAIL):
        return True
    # 整句是问句/祈使，不沉淀
    if v.endswith(("吗", "呢")):
        return True
    return False


def extract_definitions(
    chapter_num: int, body: str, known_entities: Iterable[str] | None = None
) -> list[SettingEntry]:
    """从章节正文确定性抽取「定义性约束」。

    与 ``m5_persist._extract_chapter_facts`` 的分工：那条抽「谁在场 / 什么状态 /
    道具在谁手里 / 几件」这类**短程状态事实**（P0-2 起已改实体锚定、有界三段取样）；
    本函数抽**长程设定定义**（X 是 Y、X 的 A 是 B、X 用于 Y），主体取实体名。

    Args:
        chapter_num: 章号，作为来源锚点。
        body: 章节正文（可含 frontmatter，会被跳过）。
        known_entities: 已知实体名（角色/势力/地点/物品），用于提升主语识别质量；
            为 None 时仍可通过引号术语捕获涌现的新设定。

    Returns:
        去重后的定义条目列表（同 key 只保留首次命中）。
    """
    if not body:
        return []
    if body.startswith("---"):
        parts = body.split("---", 2)
        if len(parts) >= 3:
            body = parts[2]

    known = {_clean(e) for e in (known_entities or []) if _clean(e)}
    out: dict[tuple[str, str], SettingEntry] = {}

    for raw in _SENT_SPLIT.split(body):
        sentence = raw.strip()
        if len(sentence) < 6:
            continue
        # 引号术语：即便不在已知实体表，也算本章涌现的新设定。
        # 必须过 ``is_term_like``——否则 ASCII 对白引号会把整句对白切进来当主体。
        quoted = [
            q for q in _TERM_PATTERN.findall(sentence)
            if is_term_like(q) and not _is_noise(q, "定义")
        ]

        def _absorb(attr: str, val: str, subj: str, sentence: str) -> None:
            attr, val, subj = _clean(attr), _clean(val), _clean(subj)
            if _is_noise(subj, val):
                return
            # 主语须是已知实体、本章引号术语、「所谓X」显式界定，或**词形合法的设定名**
            # （放宽后者是 2026-09-12 的修复：阵盘/导引纹 既非角色也没加引号，
            #  旧门槛把它们全挡在门外，真实语料上几乎抽不出定义）。
            if (
                subj not in known
                and subj not in quoted
                and not sentence.startswith("所谓")
                and not is_term_subject(subj)
            ):
                return
            key = (subj, attr)
            if key in out:
                out[key].updated_chapter = chapter_num
                return
            out[key] = SettingEntry(
                subject=subj,
                attribute=attr,
                value=val,
                chapter=chapter_num,
                evidence=sentence[:80],
                updated_chapter=chapter_num,
            )

        # 定向匹配「术语 + 属性槽 + 定义动词」——不用裸 finditer，
        # 否则非贪婪回溯会把「终于确认阵盘」整段吃成主语，实体永远匹配不上。
        seen: set[str] = set()
        for term in [q for q in quoted] + sorted(known):
            t = _clean(term)
            if not t or t in seen:
                continue
            seen.add(t)
            # 术语后允许隔着引号/逗号（「引灵枢」，是……）
            m = re.search(
                rf"{re.escape(t)}[」』”\"']?[，,、]?\s*{_ATTR_SLOT}{_DEF_VERB}([^\s，。；！？]{{2,40}})",
                sentence,
            )
            if m:
                _absorb(m.group(1) or "", m.group(2) or "", t, sentence)

        for pattern, g_subj, g_attr, g_val in _DEF_PATTERNS:
            for m in pattern.finditer(sentence):
                _absorb(
                    m.group(g_attr) if g_attr else "",
                    m.group(g_val),
                    m.group(g_subj),
                    sentence,
                )
    return list(out.values())


@dataclass
class SettingCanon:
    """设定台账：存取、合并、冲突检测、回写 world.md。"""

    project_dir: Path
    entries: dict[str, SettingEntry] = field(default_factory=dict)
    conflicts: list[SettingConflict] = field(default_factory=list)

    # ---- 存取 ----
    @property
    def path(self) -> Path:
        return self.project_dir / CANON_FILE

    @classmethod
    def load(cls, project_dir: str | Path) -> "SettingCanon":
        p = Path(project_dir)
        canon = cls(project_dir=p)
        f = p / CANON_FILE
        if not f.exists():
            return canon
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as e:
            degrade("setting_canon.load", "设定台账损坏或不可读，视为空台账，不阻断写章", e)
            return canon
        for raw in data.get("entries", []):
            try:
                e = SettingEntry(**raw)
            except TypeError as exc:
                degrade("setting_canon.load.entry", "台账条目字段不兼容，跳过该条", exc)
                continue
            canon.entries[f"{e.subject}|{e.attribute}"] = e
        return canon

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "schema_version": 1,
            "entries": [asdict(e) for e in self.entries.values()],
            "conflicts": [asdict(c) for c in self.conflicts[-50:]],
        }
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(self.path)

    # ---- 合并 ----
    def merge(self, new_entries: Iterable[SettingEntry]) -> list[SettingConflict]:
        """并入新条目；同 key 不同 value 记为冲突（保留旧值，不改判）。

        Returns:
            本次新发现的冲突列表。
        """
        found: list[SettingConflict] = []
        for e in new_entries:
            k = f"{e.subject}|{e.attribute}"
            old = self.entries.get(k)
            if old is None:
                self.entries[k] = e
                continue
            if _clean(old.value) == _clean(e.value):
                old.updated_chapter = e.chapter
                continue
            found.append(
                SettingConflict(
                    subject=e.subject,
                    attribute=e.attribute,
                    old_value=old.value,
                    old_chapter=old.chapter,
                    new_value=e.value,
                    new_chapter=e.chapter,
                    evidence=e.evidence,
                )
            )
        if found:
            self.conflicts.extend(found)
        return found

    # ---- 消费 ----
    def render_for_prompt(self, limit: int = 40) -> str:
        """渲染成 Writer 可直接注入的「设定硬约束」文本。

        ★ A2（2026-09-20）：取**最近确立**的 ``limit`` 条。
        原实现按 ``(chapter, subject)`` **升序**取前 ``limit`` ⇒ 只给**最老**的一批，
        而实测最老条目恰是早期抽取留下的**断句碎片**（最脏），且「最近才确立、
        尚未沉淀进 world.md 的设定」才是最需要防"重新发明"的对象。
        选取后按章序（旧→新）呈现，便于阅读。
        """
        if not self.entries:
            return ""

        def _recency(e: SettingEntry) -> tuple[int, str]:
            # 与 render() 的展示口径一致：重申/改写过的按 updated_chapter 计
            return (e.updated_chapter or e.chapter, e.subject)

        ordered = sorted(self.entries.values(), key=_recency)
        items = ordered[-limit:] if limit > 0 else list(ordered)
        lines = ["【设定台账·已确立，禁止改写或重新发明】"]
        lines.extend(e.render() for e in items)
        return "\n".join(lines)

    def render_conflicts(self, limit: int = 10) -> str:
        if not self.conflicts:
            return ""
        lines = ["【设定冲突·必须服从较早的确立值】"]
        lines.extend(c.render() for c in self.conflicts[-limit:])
        return "\n".join(lines)

    def sync_to_world_md(self) -> bool:
        """把台账回写到 world.md 的托管区块（缺失则创建 world.md）。

        这是「设定只进不出」缺口的正面修复：world.md 不再冻结在开书当天。
        """
        world = self.project_dir / "world.md"
        try:
            original = world.read_text(encoding="utf-8") if world.exists() else "# 世界观设定\n"
        except OSError as e:
            degrade("setting_canon.world_md.read", "world.md 读不到，按空文档重建", e)
            original = "# 世界观设定\n"
        if not self.entries:
            return False
        items = sorted(self.entries.values(), key=lambda e: (e.chapter, e.subject))
        block_lines = [WORLD_CANON_BEGIN, "## 设定台账（写作过程中自动沉淀，勿手改）", ""]
        block_lines.extend(e.render() for e in items)
        block_lines.extend(["", WORLD_CANON_END])
        block = "\n".join(block_lines)

        if WORLD_CANON_BEGIN in original and WORLD_CANON_END in original:
            head, rest = original.split(WORLD_CANON_BEGIN, 1)
            _, tail = rest.split(WORLD_CANON_END, 1)
            new = f"{head}{block}{tail}"
        else:
            new = original.rstrip() + "\n\n" + block + "\n"

        if new == original:
            return False
        tmp = world.with_suffix(".tmp")
        tmp.write_text(new, encoding="utf-8")
        tmp.replace(world)
        return True

    def as_dict(self) -> dict[str, Any]:
        return {
            "entries": [asdict(e) for e in self.entries.values()],
            "conflicts": [asdict(c) for c in self.conflicts],
        }
