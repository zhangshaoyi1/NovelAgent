"""会话记忆（ConversationMemory，Phase 2）

短期/会话级记忆：本轮自主写作过程中的"决策轨迹"——规划决策、章节产出事件、
编辑反馈、评测结论等。用于上下文回溯（例如把"最近 N 条决策"喂给 Planner 修订计划），
以及排障/可观测（Phase 3 LLMOps 可直接消费）。

持久化：``<project>/.state/memory/conversation.jsonl``（每行一个事件）。
"""

from __future__ import annotations

import json
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class ConversationEvent:
    """单条会话事件"""

    kind: str
    message: str
    data: dict[str, Any] = field(default_factory=dict)
    at: float = field(default_factory=lambda: time.time())

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "message": self.message,
            "data": self.data,
            "at": self.at,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "ConversationEvent":
        return cls(
            kind=str(d.get("kind", "")),
            message=str(d.get("message", "")),
            data=dict(d.get("data", {}) or {}),
            at=float(d.get("at", 0.0)),
        )


class ConversationMemory:
    """会话记忆（决策轨迹，JSONL 持久化）"""

    def __init__(self, project_dir: Path | str | None = None) -> None:
        self.project_dir = Path(project_dir) if project_dir else None
        self._lock = threading.RLock()
        self._events: list[ConversationEvent] = []
        self._file = (
            self.project_dir / ".state" / "memory" / "conversation.jsonl"
            if self.project_dir
            else None
        )
        self._load()

    def _load(self) -> None:
        if not self._file or not self._file.exists():
            return
        try:
            for line in self._file.read_text(encoding="utf-8").splitlines():
                if line.strip():
                    self._events.append(ConversationEvent.from_dict(json.loads(line)))
        except (json.JSONDecodeError, OSError):
            pass

    def _persist(self) -> None:
        if not self._file:
            return
        try:
            self._file.parent.mkdir(parents=True, exist_ok=True)
            tmp = self._file.with_suffix(".jsonl.tmp")
            tmp.write_text(
                "\n".join(json.dumps(e.to_dict(), ensure_ascii=False) for e in self._events),
                encoding="utf-8",
            )
            tmp.replace(self._file)
        except OSError:
            pass

    def append(
        self,
        kind: str,
        message: str,
        data: dict[str, Any] | None = None,
    ) -> ConversationEvent:
        """记录一条会话事件。"""
        event = ConversationEvent(kind=kind, message=message, data=dict(data or {}))
        with self._lock:
            self._events.append(event)
            self._persist()
        return event

    def log_plan(self, message: str, data: dict[str, Any] | None = None) -> None:
        self.append("plan", message, data)

    def log_chapter(
        self, chapter_num: int, title: str, summary: str = ""
    ) -> None:
        self.append(
            "chapter",
            f"第 {chapter_num} 章已写出：{title}",
            {"chapter_num": chapter_num, "title": title, "summary": summary},
        )

    def log_edit(
        self, chapter_num: int, passed: bool, conflicts: int = 0
    ) -> None:
        self.append(
            "edit",
            f"第 {chapter_num} 章编辑判定：{'通过' if passed else '冲突阻断'}",
            {"chapter_num": chapter_num, "passed": passed, "conflicts": conflicts},
        )

    def log_eval(self, overall_pass: bool, score: float | None = None) -> None:
        self.append(
            "eval",
            f"全书体检：{'通过' if overall_pass else '未通过'}",
            {"overall_pass": overall_pass, "score": score},
        )

    def log_rollback(self, target_chapter: int, archived: int = 0) -> None:
        self.append(
            "rollback",
            f"回滚至第 {target_chapter} 章（归档 {archived} 章）",
            {"target_chapter": target_chapter, "archived": archived},
        )

    def query(
        self,
        recent: int | None = None,
        kinds: list[str] | tuple[str, ...] | None = None,
        keyword: str | None = None,
    ) -> list[ConversationEvent]:
        """查询会话事件。

        - ``recent``：仅返回最近 N 条。
        - ``kinds``：仅限事件类型。
        - ``keyword``：message/data 中包含该子串。
        """
        events = list(self._events)
        if kinds:
            events = [e for e in events if e.kind in kinds]
        if keyword:
            kw = keyword.lower()
            events = [
                e
                for e in events
                if kw in e.message.lower()
                or any(kw in str(v).lower() for v in e.data.values())
            ]
        if recent is not None:
            events = events[-recent:]
        return events

    def all(self) -> list[ConversationEvent]:
        with self._lock:
            return list(self._events)

    def clear(self) -> None:
        with self._lock:
            self._events.clear()
            self._persist()
