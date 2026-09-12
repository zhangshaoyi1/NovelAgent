"""长线一致性数据底座：实体名册 / 叙事线 / 动机档案 / 信息账本 / 战力标尺账
（2026-09-12 设计稿第一期骨架，见《项目文档/模块设计/长线一致性与多团队自动写作设计.md》§4-6）

背景：角色卡、world.md、style.md 都是"出生快照"，而故事实体是随事件演化的
轨迹。本模块提供五个确定性账本（无 LLM、纯文件读写，仿 IssueDebtStore 范式）：

1. **实体名册**（EntityLedgerStore，``.state/continuity/entity_ledger.json``）：
   任何被赋予名字的角色/势力/地点登记一条轻量条目——首末出场章、出场章列表、
   生命周期（提及→伴随→休眠→收束/退场）、未了义务、动机档案（核心欲望 +
   执念成因 + 行为逻辑）。配角"随口一提"的因果断言登记在案，再次出场注入，
   防前后矛盾；大能式跨支线实体由升卡建议标记（card_suggested）。
2. **叙事线**（thread，挂在实体名册内）：传承/复仇等跨支线叙事义务建模为
   一等对象，实体绑定 + 里程碑推进，规划者规划对象从"章"升级为"章 × 线"。
3. **信息账本**（KnowledgeLedgerStore，``.state/continuity/knowledge_ledger.json``）：
   谁知道什么（含读者视角 holder="__reader__"）——dramatic irony 与保密戏的前提。
4. **战力标尺账**（PowerScaleLedgerStore，``.state/continuity/power_scale.json``）：
   主角成长记录 + 战力参照物清单（某层级战力在何章由何事确立），防战力膨胀
   与"前文大能后文杂鱼"。

所有失败显性：文件损坏抛 ``EntityLedgerError``；写时渲染（render_*）失败走
``degrade()`` 降级为空并留痕，绝不静默吞掉。
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any


class EntityLedgerError(Exception):
    """账本操作违规（文件损坏 / 未知 id 等）。"""


# ---------------------------------------------------------------- 生命周期
LC_MENTIONED = "mentioned"          # 提及（随口一提级）
LC_ACCOMPANYING = "accompanying"    # 伴随（多次出场）
LC_DORMANT = "dormant"              # 休眠（长期未出现但带未了义务）
LC_CLOSED = "closed"                # 收束（弧光完成/义务清空）
LC_RETIRED = "retired"              # 退场（死亡/永久离场）

_VALID_LIFECYCLE = frozenset(
    {LC_MENTIONED, LC_ACCOMPANYING, LC_DORMANT, LC_CLOSED, LC_RETIRED}
)

READER_HOLDER = "__reader__"  # 信息账本中的"读者知道"槽位


@dataclass
class MotivationKernel:
    """动机档案：任何人只要正文留下因果断言，就必须能对上这里。"""

    desire: str = ""        # 核心欲望（他要什么）
    origin_event: str = ""  # 执念成因事件（为什么）
    logic: str = ""         # 行为逻辑链（所以他会怎么做）

    def present(self) -> bool:
        return bool(self.desire.strip() or self.origin_event.strip() or self.logic.strip())


@dataclass
class Obligation:
    """一条未了义务（对读者/对主角/对剧情的应答义务）。"""

    text: str
    registered_ch: int = 0
    status: str = "open"  # open | closed


@dataclass
class EntityEntry:
    """实体名册条目（轻量，与角色卡分离；角色卡只服务重要角色）。"""

    name: str
    kind: str = "char"  # char | faction | place
    lifecycle: str = LC_MENTIONED
    first_ch: int = 0
    last_ch: int = 0
    appearances: list[int] = field(default_factory=list)
    relation: str = ""  # 与主角关系标签
    has_card: bool = False  # 是否已升为完整角色卡（characters/*.md 存在）
    card_suggested: bool = False  # 名册建议升卡（重要度漂移越阈）
    obligations: list[Obligation] = field(default_factory=list)
    motivation: MotivationKernel = field(default_factory=MotivationKernel)
    note: str = ""

    def open_obligations(self) -> list[Obligation]:
        return [o for o in self.obligations if o.status == "open"]


@dataclass
class ThreadEntry:
    """叙事线：跨支线的叙事义务，绑定实体、按里程碑推进。"""

    id: str
    name: str  # 线名（如"青云传承线"）
    bound_entity: str = ""  # 绑定实体名（如某大能）
    status: str = "open"  # open | closed
    urgency: str = "mid"  # high | mid | low
    milestones: list[dict[str, Any]] = field(default_factory=list)  # {ch, note}
    note: str = ""


class EntityLedgerStore:
    """实体名册 + 叙事线存取（load/save 仿 IssueDebtStore）。"""

    def __init__(self, project_dir: str | Path) -> None:
        self.project_dir = Path(project_dir)
        self.path = self.project_dir / ".state" / "continuity" / "entity_ledger.json"
        self.entities: list[EntityEntry] = []
        self.threads: list[ThreadEntry] = []

    # ------ IO ------
    def load(self) -> "EntityLedgerStore":
        if not self.path.exists():
            self.entities, self.threads = [], []
            return self
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as e:
            raise EntityLedgerError(f"实体名册文件损坏（{self.path}）：{e}") from e
        self.entities = [
            EntityEntry(
                **{
                    **{k: v for k, v in it.items() if k in EntityEntry.__dataclass_fields__},
                    "obligations": [
                        Obligation(**o) for o in it.get("obligations", []) if isinstance(o, dict)
                    ],
                    "motivation": MotivationKernel(**(it.get("motivation") or {})),
                }
            )
            for it in raw.get("entities", [])
            if isinstance(it, dict)
        ]
        self.threads = [
            ThreadEntry(**{k: v for k, v in t.items() if k in ThreadEntry.__dataclass_fields__})
            for t in raw.get("threads", [])
            if isinstance(t, dict)
        ]
        return self

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "entities": [asdict(e) for e in self.entities],
            "threads": [asdict(t) for t in self.threads],
        }
        tmp = self.path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(self.path)

    # ------ 实体 ------
    def ensure(self, name: str, ch: int = 0, kind: str = "char") -> EntityEntry:
        """登记或命中实体（并记录一次出场；同章去重）。"""
        name = name.strip()
        if not name:
            raise EntityLedgerError("实体名不能为空")
        entry = self.get(name)
        if entry is None:
            entry = EntityEntry(name=name, kind=kind, first_ch=int(ch or 0))
            self.entities.append(entry)
        ch = int(ch or 0)
        if ch:
            if not entry.first_ch:
                entry.first_ch = ch
            entry.last_ch = max(entry.last_ch, ch)
            if ch not in entry.appearances:
                entry.appearances.append(ch)
                entry.appearances.sort()
            if entry.lifecycle not in (LC_CLOSED, LC_RETIRED):
                entry.lifecycle = LC_ACCOMPANYING
        return entry

    def get(self, name: str) -> EntityEntry | None:
        name = name.strip()
        for e in self.entities:
            if e.name == name:
                return e
        return None

    def set_lifecycle(self, name: str, lifecycle: str) -> None:
        if lifecycle not in _VALID_LIFECYCLE:
            raise EntityLedgerError(
                f"未知生命周期：{lifecycle!r}（合法：{sorted(_VALID_LIFECYCLE)}）"
            )
        entry = self.get(name)
        if entry is None:
            raise EntityLedgerError(f"实体不存在：{name!r}")
        entry.lifecycle = lifecycle

    def set_motivation(self, name: str, desire: str = "", origin_event: str = "", logic: str = "") -> MotivationKernel:
        """登记/补全动机档案——"提及即登记"的落点。"""
        entry = self.get(name)
        if entry is None:
            raise EntityLedgerError(f"实体不存在：{name!r}")
        m = entry.motivation
        if desire.strip():
            m.desire = desire.strip()
        if origin_event.strip():
            m.origin_event = origin_event.strip()
        if logic.strip():
            m.logic = logic.strip()
        return m

    def add_obligation(self, name: str, text: str, ch: int = 0) -> Obligation:
        entry = self.get(name)
        if entry is None:
            raise EntityLedgerError(f"实体不存在：{name!r}")
        ob = Obligation(text=text.strip(), registered_ch=int(ch or 0))
        if not ob.text:
            raise EntityLedgerError("义务内容不能为空")
        entry.obligations.append(ob)
        return ob

    def close_obligation(self, name: str, index: int) -> bool:
        entry = self.get(name)
        if entry is None or not (0 <= index < len(entry.obligations)):
            return False
        entry.obligations[index].status = "closed"
        return True

    def suggest_card(self, name: str) -> bool:
        """重要度漂移越阈 → 建议升卡（是否真升由规划者决策）。"""
        entry = self.get(name)
        if entry is None:
            return False
        entry.card_suggested = True
        return True

    def dormant_entities(self, current_ch: int, threshold: int = 20) -> list[EntityEntry]:
        """休眠告警：有名有姓、长期未出现且带未了义务的实体。"""
        out = [
            e
            for e in self.entities
            if e.lifecycle not in (LC_CLOSED, LC_RETIRED)
            and e.last_ch
            and current_ch - e.last_ch >= threshold
            and e.open_obligations()
        ]
        out.sort(key=lambda e: e.last_ch)
        return out

    # ------ 叙事线 ------
    def add_thread(self, name: str, bound_entity: str = "", urgency: str = "mid", note: str = "") -> ThreadEntry:
        if not name.strip():
            raise EntityLedgerError("叙事线名称不能为空")
        if urgency not in ("high", "mid", "low"):
            raise EntityLedgerError(f"未知 urgency：{urgency!r}")
        t = ThreadEntry(id=self._next_thread_id(), name=name.strip(), bound_entity=bound_entity.strip(),
                        urgency=urgency, note=note.strip())
        self.threads.append(t)
        return t

    def advance_thread(self, thread_id: str, ch: int, note: str = "") -> bool:
        for t in self.threads:
            if t.id == thread_id and t.status == "open":
                t.milestones.append({"ch": int(ch or 0), "note": note.strip()})
                return True
        return False

    def close_thread(self, thread_id: str) -> bool:
        for t in self.threads:
            if t.id == thread_id and t.status == "open":
                t.status = "closed"
                return True
        return False

    def open_threads(self) -> list[ThreadEntry]:
        return [t for t in self.threads if t.status == "open"]

    def _next_thread_id(self) -> str:
        nums = [int(m.group(1)) for t in self.threads if (m := re.fullmatch(r"THREAD-(\d+)", t.id))]
        return f"THREAD-{(max(nums) + 1) if nums else 1:03d}"

    # ------ 写时渲染 ------
    def render_for_prompt(self, appearing: list[str], current_ch: int = 0, limit: int = 8) -> str:
        """出场实体的名册摘要（防"复刻一个不同性格的同名者"与动机矛盾）。"""
        lines: list[str] = []
        for name in appearing[:limit]:
            e = self.get(name)
            if e is None:
                continue
            parts = [f"{e.name}（{e.kind}"]
            if e.relation:
                parts.append(f"/{e.relation}")
            parts.append(f"，{e.lifecycle}")
            parts.append("）")
            if e.has_card is False and e.motivation.present():
                m = e.motivation
                parts.append(f"欲望：{m.desire}；成因：{m.origin_event}；逻辑：{m.logic}。")
            for ob in e.open_obligations()[:3]:
                parts.append(f"未了义务：{ob.text}。")
            if e.card_suggested:
                parts.append("【名册建议升卡】")
            lines.append("".join(parts))
        open_ts = self.open_threads()
        if open_ts:
            lines.append("进行中叙事线：" + "；".join(
                f"{t.name}（绑定{t.bound_entity or '无'}，urgency={t.urgency}，已推进{len(t.milestones)}节）"
                for t in open_ts[:6]
            ))
        if current_ch:
            dorm = self.dormant_entities(current_ch)
            if dorm:
                lines.append("休眠预警（长期未出现且带未了义务）：" + "；".join(
                    f"{e.name}（末见第{e.last_ch}章）" for e in dorm[:6]
                ))
        if not lines:
            return ""
        return "\n【实体名册摘要（有名实体的既有档案，本章必须自洽）】\n" + "\n".join(
            f"- {ln}" for ln in lines
        )


@dataclass
class KnowledgeEntry:
    """一条"谁知道什么"。holder=__reader__ 表示读者已知（悬念/irony 引擎）。"""

    id: str
    holder: str  # 知晓者（角色名 / __reader__）
    fact: str
    learned_ch: int = 0
    source: str = ""  # 何章何事得知


class KnowledgeLedgerStore:
    """信息账本存取（``.state/continuity/knowledge_ledger.json``）。"""

    def __init__(self, project_dir: str | Path) -> None:
        self.project_dir = Path(project_dir)
        self.path = self.project_dir / ".state" / "continuity" / "knowledge_ledger.json"
        self.entries: list[KnowledgeEntry] = []

    def load(self) -> "KnowledgeLedgerStore":
        if not self.path.exists():
            self.entries = []
            return self
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as e:
            raise EntityLedgerError(f"信息账本文件损坏（{self.path}）：{e}") from e
        self.entries = [
            KnowledgeEntry(**{k: v for k, v in it.items() if k in KnowledgeEntry.__dataclass_fields__})
            for it in raw.get("entries", [])
            if isinstance(it, dict)
        ]
        return self

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"entries": [asdict(e) for e in self.entries]}
        tmp = self.path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(self.path)

    def record(self, holder: str, fact: str, learned_ch: int = 0, source: str = "") -> KnowledgeEntry:
        if not holder.strip() or not fact.strip():
            raise EntityLedgerError("信息账本条目必须指定 holder 与 fact")
        e = KnowledgeEntry(id=f"KB-{len(self.entries) + 1:04d}", holder=holder.strip(),
                           fact=fact.strip(), learned_ch=int(learned_ch or 0), source=source.strip())
        self.entries.append(e)
        return e

    def known_by(self, holder: str) -> list[KnowledgeEntry]:
        return [e for e in self.entries if e.holder == holder.strip()]

    def render_for_prompt(self, holders: list[str], limit: int = 10) -> str:
        """出场角色 + 读者的"已知信息"摘要。"""
        lines: list[str] = []
        for h in holders:
            items = self.known_by(h)[:limit]
            if items:
                lines.append(f"{h}已知：" + "；".join(e.fact for e in items))
        reader_items = self.known_by(READER_HOLDER)[:limit]
        if reader_items:
            lines.append("读者已知（角色未必知道——可经营信息差）：" + "；".join(e.fact for e in reader_items))
        if not lines:
            return ""
        return "\n【信息账本（谁知道什么，本章言行不得违背）】\n" + "\n".join(f"- {ln}" for ln in lines)


@dataclass
class PowerBenchmark:
    """战力参照物：某层级战力在何章由何事确立（防"前文大能后文杂鱼"）。"""

    tier: str  # 层级/境界名
    reference: str  # 参照事件（如"第12章一掌灭青云宗"）
    established_ch: int = 0


class PowerScaleLedgerStore:
    """战力标尺账存取（``.state/continuity/power_scale.json``）。"""

    def __init__(self, project_dir: str | Path) -> None:
        self.project_dir = Path(project_dir)
        self.path = self.project_dir / ".state" / "continuity" / "power_scale.json"
        self.progress: list[dict[str, Any]] = []  # {ch, desc} 主角成长记录
        self.benchmarks: list[PowerBenchmark] = []

    def load(self) -> "PowerScaleLedgerStore":
        if not self.path.exists():
            self.progress, self.benchmarks = [], []
            return self
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as e:
            raise EntityLedgerError(f"战力标尺账文件损坏（{self.path}）：{e}") from e
        self.progress = [p for p in raw.get("progress", []) if isinstance(p, dict)]
        self.benchmarks = [
            PowerBenchmark(**{k: v for k, v in b.items() if k in PowerBenchmark.__dataclass_fields__})
            for b in raw.get("benchmarks", [])
            if isinstance(b, dict)
        ]
        return self

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "progress": self.progress,
            "benchmarks": [asdict(b) for b in self.benchmarks],
        }
        tmp = self.path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(self.path)

    def record_progress(self, ch: int, desc: str) -> None:
        if not desc.strip():
            raise EntityLedgerError("成长记录不能为空")
        self.progress.append({"ch": int(ch or 0), "desc": desc.strip()})

    def set_benchmark(self, tier: str, reference: str, ch: int = 0) -> PowerBenchmark:
        if not tier.strip() or not reference.strip():
            raise EntityLedgerError("战力参照物必须指定 tier 与 reference")
        b = PowerBenchmark(tier=tier.strip(), reference=reference.strip(), established_ch=int(ch or 0))
        self.benchmarks = [x for x in self.benchmarks if x.tier != b.tier] + [b]
        return b

    def render_for_prompt(self, limit: int = 8) -> str:
        lines: list[str] = []
        if self.progress:
            recent = self.progress[-limit:]
            lines.append(
                "主角近期成长轨迹："
                + "；".join(f"第{p['ch']}章 {p['desc']}" for p in recent)
            )
        if self.benchmarks:
            lines.append(
                "战力参照物（本章战力描写不得与之矛盾）："
                + "；".join(f"{b.tier}＝{b.reference}" for b in self.benchmarks[:limit])
            )
        if not lines:
            return ""
        return "\n【战力标尺账】\n" + "\n".join(f"- {ln}" for ln in lines)


def render_ledger_context(
    project_dir: str | Path, appearing: list[str] | None = None, current_ch: int = 0
) -> str:
    """写时注入聚合口：名册 + 信息账 + 战力标尺三账合一（供 m5_context 调用）。

    任一账本损坏 → degrade() 显性降级为空（写章不能因账本挂掉），但错误必须留痕。
    """
    from agent.core.infra.degrade import degrade

    blocks: list[str] = []
    try:
        blocks.append(EntityLedgerStore(project_dir).load().render_for_prompt(appearing or [], current_ch))
    except EntityLedgerError as e:
        degrade("entity_ledger.render", "实体名册损坏，本轮注入为空", e)
    try:
        holders = list(dict.fromkeys(["主角", *(appearing or [])]))
        blocks.append(KnowledgeLedgerStore(project_dir).load().render_for_prompt(holders))
    except EntityLedgerError as e:
        degrade("knowledge_ledger.render", "信息账本损坏，本轮注入为空", e)
    try:
        blocks.append(PowerScaleLedgerStore(project_dir).load().render_for_prompt())
    except EntityLedgerError as e:
        degrade("power_scale.render", "战力标尺账损坏，本轮注入为空", e)
    return "\n".join(b for b in blocks if b)


__all__ = [
    "EntityLedgerError",
    "MotivationKernel",
    "Obligation",
    "EntityEntry",
    "ThreadEntry",
    "EntityLedgerStore",
    "KnowledgeEntry",
    "KnowledgeLedgerStore",
    "PowerBenchmark",
    "PowerScaleLedgerStore",
    "render_ledger_context",
    "LC_MENTIONED",
    "LC_ACCOMPANYING",
    "LC_DORMANT",
    "LC_CLOSED",
    "LC_RETIRED",
    "READER_HOLDER",
]
