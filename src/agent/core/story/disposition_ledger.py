"""心性账本（disposition ledger，长线一致性设计稿第二期·维度轨迹账本 P1 #1）

解决：主角卡上的"单纯"是第 1 章的状态，第 200 章他被刺过七次之后还按快照写
必然 OOC。本账本记录**信念值 + 变化事件链**（三层模式：当前值 / 事件链 /
计划目标值），writer 注入的是"当前心性画像 + 最近一次变化的原因"，而非
初始性格标签。

变更治理：心性是**叙事变更**，写入必须经 ``change_gate.propose_change``——
- 确定性校验：数值合法（0-10）；跳变 >4 级而未提供 LLM 裁决 → 驳回
  （大转折必须有裁决理由，防止写作者随手把心性写回去）；
- LLM 裁决（可选注入）：判断转变是否与既有事件链自洽（创作判断只建议不否决）；
- 无论批准/驳回，元数据落 ``change_log.json`` 可回溯。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any

from agent.core.story.change_gate import (
    ArbiterFn,
    ProposedChange,
    propose_change,
)
from agent.core.story.change_log import ChangeLogStore


class DispositionError(Exception):
    """心性账本操作违规（文件损坏等）。"""


@dataclass
class DispositionEvent:
    """一次心性变化事件（含变化前后的值与理由）。"""

    ch: int
    from_value: int
    to_value: int
    reason: str


@dataclass
class TraitEntry:
    """一条信念/性格维度：当前值 + 变化事件链。"""

    holder: str  # 角色（"主角"或角色名）
    trait: str  # 维度名（如 "对陌生人的信任"、"克制力"）
    value: int = 5  # 当前值 0-10
    note: str = ""  # 当前画像备注（如 "表面冷淡，内里仍念旧情"）
    events: list[DispositionEvent] = field(default_factory=list)


class DispositionLedgerStore:
    """心性账本存取（``.state/continuity/disposition.json``）。"""

    def __init__(self, project_dir: str | Path) -> None:
        self.project_dir = Path(project_dir)
        self.path = self.project_dir / ".state" / "continuity" / "disposition.json"
        self.traits: list[TraitEntry] = []

    def load(self) -> "DispositionLedgerStore":
        if not self.path.exists():
            self.traits = []
            return self
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as e:
            raise DispositionError(f"心性账本文件损坏（{self.path}）：{e}") from e
        self.traits = [
            TraitEntry(
                **{
                    **{k: v for k, v in it.items() if k in TraitEntry.__dataclass_fields__},
                    "events": [
                        DispositionEvent(**ev) for ev in it.get("events", []) if isinstance(ev, dict)
                    ],
                }
            )
            for it in raw.get("traits", [])
            if isinstance(it, dict)
        ]
        return self

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"traits": [asdict(t) for t in self.traits]}
        tmp = self.path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(self.path)

    def get(self, holder: str, trait: str) -> TraitEntry | None:
        for t in self.traits:
            if t.holder == holder.strip() and t.trait == trait.strip():
                return t
        return None

    def set_baseline(self, holder: str, trait: str, value: int, note: str = "") -> TraitEntry:
        """登记初始画像（开局设定，非叙事变更，直接写——簿记/叙事分流）。"""
        if not 0 <= int(value) <= 10:
            raise DispositionError(f"信念值必须 0-10：{value!r}")
        entry = self.get(holder, trait)
        if entry is None:
            entry = TraitEntry(holder=holder.strip(), trait=trait.strip(), value=int(value), note=note)
            self.traits.append(entry)
        else:
            entry.value = int(value)
            if note.strip():
                entry.note = note.strip()
        return entry

    # ------ 写时渲染 ------
    def render_for_prompt(self, holders: list[str], limit: int = 8) -> str:
        lines: list[str] = []
        for h in holders:
            entries = [t for t in self.traits if t.holder == h][:limit]
            if not entries:
                continue
            parts: list[str] = []
            for t in entries:
                line = f"{t.trait}={t.value}/10"
                if t.events:
                    last = t.events[-1]
                    line += f"（第{last.ch}章因「{last.reason[:40]}」由{last.from_value}变为{last.to_value}）"
                if t.note:
                    line += f"〔{t.note[:30]}〕"
                parts.append(line)
            lines.append(f"{h}当前心性画像：" + "；".join(parts))
        if not lines:
            return ""
        return "\n【心性轨迹（本章言行必须符合当前画像；转变需有事件支撑并走变更裁决）】\n" + "\n".join(
            f"- {ln}" for ln in lines
        )


# 跳变超过该级数必须提供 LLM 裁决（确定性防线：大转折不可绕过理由）
DISPOSITION_JUMP_REQUIRES_ARBITER = 4


def propose_disposition_change(
    project_dir: str | Path,
    holder: str,
    trait: str,
    new_value: int,
    reason: str,
    chapter: int = 0,
    evidence: str = "",
    arbiter: ArbiterFn | None = None,
    log: ChangeLogStore | None = None,
) -> "object":
    """提交一次心性变更（叙事变更，走 ChangeGate 准入）。

    Returns:
        ``ChangeVerdict``（批准时账本已更新并落日志；驳回时账本不变、日志留痕）。
    """
    store = DispositionLedgerStore(project_dir).load()
    entry = store.get(holder, trait)
    old_value = entry.value if entry else None
    new_value = int(new_value)

    def deterministic_check(change: ProposedChange) -> "tuple[bool, str]":
        if not 0 <= new_value <= 10:
            return False, f"信念值必须 0-10，收到 {new_value}"
        if old_value is not None and abs(new_value - old_value) > DISPOSITION_JUMP_REQUIRES_ARBITER and arbiter is None:
            return (
                False,
                f"心性跳变 {old_value}→{new_value} 超过 {DISPOSITION_JUMP_REQUIRES_ARBITER} 级，"
                "必须提供 LLM 裁决（大转折须有可回溯的裁决理由）",
            )
        return True, ""

    def apply(change: ProposedChange) -> None:
        # apply 在 load 过的 store 上操作后立即落盘（唯一真源仍是账本）
        st = DispositionLedgerStore(project_dir).load()
        e = st.get(holder, trait)
        if e is None:
            e = st.set_baseline(holder, trait, new_value)
        else:
            e.value = new_value
        e.events.append(
            DispositionEvent(ch=int(chapter or 0), from_value=(old_value if old_value is not None else new_value),
                             to_value=new_value, reason=reason.strip())
        )
        st.save()

    change = ProposedChange(
        kind="disposition",
        subject=f"{holder}.{trait}",
        before=f"{old_value}/10" if old_value is not None else "（未登记）",
        after=f"{new_value}/10",
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
    "DispositionError",
    "DispositionEvent",
    "TraitEntry",
    "DispositionLedgerStore",
    "propose_disposition_change",
    "DISPOSITION_JUMP_REQUIRES_ARBITER",
]
