"""语义记忆（SemanticMemory，Phase 2）

长期事实记忆：世界观设定、角色事实、已确立的剧情节点、伏笔登记等"应该长期记得"
的结构化事实。后端默认用离线 char-bigram 打分（``base.default_scorer``），
不依赖向量库或网络，保证测试/离线可用；Phase 3/4 可替换为真实向量检索。

持久化：``<project>/.state/memory/semantic.jsonl``（每行一个 MemoryEntry）。
"""

from __future__ import annotations

import json
import threading
import uuid
from pathlib import Path
from typing import Any, Callable

from agent.memory.base import MemoryEntry, default_scorer


class SemanticMemory:
    """语义（长期事实）记忆。

    Args:
        project_dir: 小说项目目录；None 表示纯内存（测试用）。
        scorer: 检索打分器，默认离线 char-bigram 余弦。
    """

    def __init__(
        self,
        project_dir: str | Path | None = None,
        scorer: Callable[[str, str], float] | None = None,
    ) -> None:
        self.project_dir = Path(project_dir) if project_dir else None
        self.scorer = scorer or default_scorer
        self._lock = threading.RLock()
        self._entries: list[MemoryEntry] = []
        self._file = None
        if self.project_dir is not None:
            self._file = self.project_dir / ".state" / "memory" / "semantic.jsonl"
            self._load()

    # ---------------------------------------------------------------- 持久化
    def _load(self) -> None:
        if self._file is None or not self._file.exists():
            return
        try:
            for line in self._file.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line:
                    continue
                self._entries.append(MemoryEntry.from_dict(json.loads(line)))
        except (json.JSONDecodeError, OSError):
            # 损坏的文件不阻断，视为空
            self._entries = []

    def _persist(self) -> None:
        if self._file is None:
            return
        self._file.parent.mkdir(parents=True, exist_ok=True)
        tmp = self._file.with_suffix(".tmp")
        lines = [json.dumps(e.to_dict(), ensure_ascii=False) for e in self._entries]
        tmp.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")
        tmp.replace(self._file)

    # ---------------------------------------------------------------- 写入
    def add(
        self,
        text: str,
        *,
        type: str = "fact",
        tags: list[str] | None = None,
        source: str = "",
        meta: dict[str, Any] | None = None,
        entry_id: str | None = None,
    ) -> MemoryEntry:
        """新增一条语义记忆，返回该条目。"""
        entry = MemoryEntry(
            type=type,
            text=text,
            id=entry_id or uuid.uuid4().hex[:12],
            tags=tags or [],
            source=source,
            meta=meta or {},
        )
        with self._lock:
            self._entries.append(entry)
            self._persist()
        return entry

    def add_many(self, entries: list[MemoryEntry]) -> None:
        with self._lock:
            self._entries.extend(entries)
            self._persist()

    # ---------------------------------------------------------------- 检索
    def retrieve(
        self,
        query: str,
        *,
        top_k: int = 5,
        min_score: float = 0.0,
        types: list[str] | None = None,
        tags: list[str] | None = None,
    ) -> list[tuple[MemoryEntry, float]]:
        """语义检索，按相似度降序返回 (条目, 分数)。

        - ``types``：仅限定类型（None 表示不限）。
        - ``tags``：要求命中至少一个标签（None 表示不限）。
        - ``min_score``：低于该分数的不返回。
        """
        scored: list[tuple[MemoryEntry, float]] = []
        for e in self._entries:
            if types and e.type not in types:
                continue
            if tags and not (set(e.tags) & set(tags)):
                continue
            s = self.scorer(query, e.text)
            if s >= min_score:
                scored.append((e, s))
        scored.sort(key=lambda x: x[1], reverse=True)
        return scored[:top_k]

    def get(self, entry_id: str) -> MemoryEntry | None:
        with self._lock:
            return next((e for e in self._entries if e.id == entry_id), None)

    def all(self) -> list[MemoryEntry]:
        with self._lock:
            return list(self._entries)

    def count(self) -> int:
        with self._lock:
            return len(self._entries)
