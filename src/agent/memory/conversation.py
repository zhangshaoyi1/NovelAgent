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
    """单条会话事件。"""

    kind: str  # plan | chapter | edit | eval | decision | rollback | note
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
            kind=str(d.get("kind", "note")),
            message=str(d.get("message", "")),
            data=dict(d.get("data", {}) or {}),
            at=float(d.get("at", 0.0)),
        )


class ConversationMemory:
    """会话（短期）记忆。

    Args:
        project_dir: 小说项目目录；None 表示纯内存（测试用）。
    """

    def __init__(self, project_dir: str | Path | None = None) -> None:
        self.project_dir = Path(project_dir) if project_dir else None
        self._lock = threading.RLock()
        self._events: list[ConversationEvent] = []
        self._file = None
        if self.project_dir is not None:
            self._file = self.project_dir / ".state" / "memory" / "conversation.jsonl"
            self._load()

    def _load(self) -> None:
        if self._file is None or not self._file.exists():
            return
        try:
            for line in self._file.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line:
                    continue
                self._events.append(ConversationEvent.from_dict(json.loads(line)))
        except (json.JSONDecodeError, OSError):
            self._events = []

    def _persist(self) -> None:
        if self._file is None:
            return
        self._file.parent.mkdir(parents=True, exist_ok=True)
        tmp = self._file.with_suffix(".tmp")
        lines = [json.dumps(e.to_dict(), ensure_ascii=False) for e in self._events]
        tmp.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")
        tmp.replace(self._file)

    def append(
        self,
        kind: str,
        message: str,
        data: dict[str, Any] | None = None,
    ) -> ConversationEvent:
        """记录一条会话事件。"""
        ev = ConversationEvent(kind=kind, message=message, data=data or {})
        with self._lock:
            self._events.append(ev)
            self._persist()
        return ev

    # 便捷封装
    def log_plan(self, message: str, data: dict[str, Any] | None = None) -> None:
        self.append("plan", message, data)

    def log_chapter(self, chapter_num: int, title: str, summary: str = "") -> None:
        self.append(
            "chapter",
            f"第 {chapter_num} 章《{title}》已产出",
            {"chapter_num": chapter_num, "title": title, "summary": summary},
        )

    def log_edit(self, chapter_num: int, passed: bool, conflicts: int) -> None:
        self.append(
            "edit",
            f"第 {chapter_num} 章编辑{'通过' if passed else '有冲突'}",
            {"chapter_num": chapter_num, "passed": passed, "conflicts": conflicts},
        )

    def log_eval(self, overall_pass: bool, score: float) -> None:
        self.append(
            "eval", "全书体检完成", {"overall_pass": overall_pass, "score": score}
        )

    def log_rollback(self, target_chapter: int, archived: int) -> None:
        self.append(
            "rollback",
            f"回退至第 {target_chapter} 章（归档 {archived} 章）",
            {"target_chapter": target_chapter, "archived": archived},
        )

    def query(
        self,
        *,
        recent: int | None = None,
        kinds: list[str] | None = None,
        keyword: str | None = None,
    ) -> list[ConversationEvent]:
        """查询会话事件。

        - ``recent``：仅返回最近 N 条。
        - ``kinds``：仅限事件类型。
        - ``keyword``：message/data 中包含该子串。
        """
        evs = list(self._events)
        if kinds:
            evs = [e for e in evs if e.kind in kinds]
        if keyword:
            kw = keyword.lower()
            evs = [
                e
                for e in evs
                if kw in e.message.lower()
                or any(kw in str(v).lower() for v in e.data.values())
            ]
        if recent is not None:
            evs = evs[-recent:]
        return evs

    def all(self) -> list[ConversationEvent]:
        with self._lock:
            return list(self._events)

    def clear(self) -> None:
        with self._lock:
            self._events = []
            self._persist()
