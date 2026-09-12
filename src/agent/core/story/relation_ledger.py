"""关系网账本（relation ledger，长线一致性设计稿第二期·维度轨迹账本 P1 #2）

解决：关系标签是静态的，而"盟友 → 因误会反目 → 揭开后和解但留疤"这条轨迹
才是写对手戏真正需要的。三层模式：当前状态 + 演化事件链 + "留疤"标记
（和解了但信任没完全恢复——后文可反复使用的戏剧资源）。

变更治理：关系是**叙事变更**，写入必须经 ``change_gate.propose_change``
（kind="relation"），确定性校验：状态非空、原因非空；LLM 裁决可选注入
（判断转变是否与双方心性/既有事件链自洽）。元数据落 change_log 可回溯。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field, asdict
from pathlib import Path

from agent.core.story.change_gate import ArbiterFn, ProposedChange, propose_change
from agent.core.story.change_log import ChangeLogStore


class RelationError(Exception):
    """关系网账本操作违规（文件损坏等）。"""


@dataclass
class RelationEvent:
    """一次关系变化事件。"""

    ch: int
    from_state: str
    to_state: str
    reason: str


@dataclass
class RelationEntry:
    """一对角色之间的关系（无序对，内部按 sorted(a,b) 归一化存储）。"""

    pair: str  # 归一化键："A|B"（字典序）
    a: str
    b: str
    state: str  # 当前关系标签（盟友/敌对/师徒/反目/和解…）
    note: str = ""
    scars: list[str] = field(default_factory=list)  # 留疤：修复后仍遗留的裂痕
    events: list[RelationEvent] = field(default_factory=list)


def _pair_key(a: str, b: str) -> str:
    return "|".join(sorted([a.strip(), b.strip()]))


class RelationLedgerStore:
    """关系网账本存取（``.state/continuity/relations.json``）。"""

    def __init__(self, project_dir: str | Path) -> None:
        self.project_dir = Path(project_dir)
        self.path = self.project_dir / ".state" / "continuity" / "relations.json"
        self.entries: list[RelationEntry] = []

    def load(self) -> "RelationLedgerStore":
        if not self.path.exists():
            self.entries = []
            return self
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as e:
            raise RelationError(f"关系网账本文件损坏（{self.path}）：{e}") from e
        self.entries = [
            RelationEntry(
                **{
                    **{k: v for k, v in it.items() if k in RelationEntry.__dataclass_fields__},
                    "events": [
                        RelationEvent(**ev) for ev in it.get("events", []) if isinstance(ev, dict)
                    ],
                }
            )
            for it in raw.get("relations", [])
            if isinstance(it, dict)
        ]
        return self

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"relations": [asdict(r) for r in self.entries]}
        tmp = self.path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(self.path)

    def get(self, a: str, b: str) -> RelationEntry | None:
        key = _pair_key(a, b)
        for e in self.entries:
            if e.pair == key:
                return e
        return None

    def set_baseline(self, a: str, b: str, state: str, note: str = "") -> RelationEntry:
        """登记初始关系（开局设定，非叙事变更，直接写——簿记/叙事分流）。"""
        if not a.strip() or not b.strip() or not state.strip():
            raise RelationError("初始关系必须指定双方与状态")
        entry = self.get(a, b)
        if entry is None:
            entry = RelationEntry(pair=_pair_key(a, b), a=a.strip(), b=b.strip(), state=state.strip(), note=note)
            self.entries.append(entry)
        else:
            entry.state = state.strip()
            if note.strip():
                entry.note = note.strip()
        return entry

    def involving(self, name: str) -> list[RelationEntry]:
        """某角色参与的全部关系（按 pair 键排序）。"""
        out = [e for e in self.entries if name in (e.a, e.b)]
        out.sort(key=lambda e: e.pair)
        return out

    # ------ 写时渲染 ------
    def render_for_prompt(self, holders: list[str], limit: int = 10) -> str:
        """出场角色涉及的关系 + 全部留疤（留疤是全书资产，不过滤）。"""
        seen: dict[str, str] = {}
        scars: list[str] = []
        for name in holders:
            for e in self.involving(name):
                if e.pair in seen:
                    continue
                parts = [f"{e.a} ↔ {e.b}：{e.state}"]
                if e.events:
                    last = e.events[-1]
                    parts.append(f"（第{last.ch}章由「{last.from_state}」变为「{last.to_state}」，{last.reason[:36]}）")
                if e.note:
                    parts.append(f"〔{e.note[:30]}〕")
                seen[e.pair] = "".join(parts)
                for s in e.scars:
                    scars.append(f"{e.a} ↔ {e.b}：{s}")
        lines = [seen[k] for k in sorted(seen)][:limit]
        if not lines and not scars:
            return ""
        out = ["\n【关系网轨迹（对手戏必须符合当前状态与事件链）】"]
        out.extend(f"- {ln}" for ln in lines)
        if scars:
            out.append("- 留疤（关系修复后仍存在的裂痕，后文互动可呼应）：")
            out.extend(f"  · {s}" for s in scars[:6])
        return "\n".join(out)


def propose_relation_change(
    project_dir: str | Path,
    a: str,
    b: str,
    new_state: str,
    reason: str,
    chapter: int = 0,
    evidence: str = "",
    scar: str = "",
    arbiter: ArbiterFn | None = None,
    log: ChangeLogStore | None = None,
):
    """提交一次关系变更（叙事变更，走 ChangeGate 准入；scar=本次修复后遗留的裂痕）。"""
    store = RelationLedgerStore(project_dir).load()
    entry = store.get(a, b)
    old_state = entry.state if entry else "（未登记）"

    def deterministic_check(change: ProposedChange) -> "tuple[bool, str]":
        if not new_state.strip():
            return False, "关系状态不能为空"
        if entry is not None and entry.state == new_state.strip():
            return False, f"关系已是「{new_state}」，无变化不需变更"
        return True, ""

    def apply(change: ProposedChange) -> None:
        st = RelationLedgerStore(project_dir).load()
        e = st.get(a, b)
        if e is None:
            e = st.set_baseline(a, b, new_state)
        else:
            e.state = new_state.strip()
        e.events.append(
            RelationEvent(ch=int(chapter or 0), from_state=old_state, to_state=new_state.strip(),
                          reason=reason.strip())
        )
        if scar.strip():
            e.scars.append(scar.strip())
        st.save()

    change = ProposedChange(
        kind="relation",
        subject=_pair_key(a, b),
        before=old_state,
        after=new_state.strip(),
        reason=reason,
        chapter=chapter,
        evidence=evidence,
    )
    return propose_change(
        project_dir,
        change,
        deterministic_check=deterministic_check,
        arbiter=arbiter,
        apply=apply,
        log=log,
    )


__all__ = [
    "RelationError",
    "RelationEvent",
    "RelationEntry",
    "RelationLedgerStore",
    "propose_relation_change",
]
