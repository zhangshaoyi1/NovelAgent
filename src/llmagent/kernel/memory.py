"""Memory：记忆写入 + SalienceFilter（M3.4）

管理执行经验的沉淀与检索。
"""

from __future__ import annotations

import json
import sqlite3
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Protocol

from .task import TaskRun, TaskStatus


@dataclass
class MemoryEntry:
    """记忆条目"""
    entry_id: str = ""
    scope: str = "task"  # task / session / global
    content: str = ""
    tags: dict[str, str] = field(default_factory=dict)
    source: str = ""  # success / failure / human_correction
    task_name: str = ""
    run_id: str = ""
    created_at: str = ""
    priority: float = 0.5  # 0~1

    def __post_init__(self) -> None:
        if not self.entry_id:
            self.entry_id = f"mem-{uuid.uuid4().hex[:12]}"
        if not self.created_at:
            self.created_at = datetime.now(timezone.utc).isoformat()


# ===== MemoryWritePolicy =====


class MemoryWritePolicy(Protocol):
    """记忆写入策略协议"""

    def should_write(self, run: TaskRun, entry: MemoryEntry) -> bool:
        ...


class WriteOnSuccess:
    """成功时写入"""

    @staticmethod
    def should_write(run: TaskRun, entry: MemoryEntry) -> bool:
        return run.status == TaskStatus.SUCCEEDED


class WriteFailureCase:
    """失败时写入"""

    @staticmethod
    def should_write(run: TaskRun, entry: MemoryEntry) -> bool:
        return run.status == TaskStatus.FAILED


class WriteHumanCorrection:
    """人工修正时写入"""

    @staticmethod
    def should_write(run: TaskRun, entry: MemoryEntry) -> bool:
        return entry.source == "human_correction"


# ===== SalienceFilter =====


class SalienceFilter:
    """显著性过滤器：防记忆污染"""

    def __init__(self, min_priority: float = 0.3, dedup_threshold: float = 0.8) -> None:
        self._min_priority = min_priority
        self._dedup_threshold = dedup_threshold

    def should_keep(self, entry: MemoryEntry, existing: list[MemoryEntry]) -> bool:
        """判断是否应保留该记忆"""
        # 优先级过滤
        if entry.priority < self._min_priority:
            return False

        # 去重：Jaccard 相似度
        for existing_entry in existing:
            similarity = self._jaccard_similarity(entry.content, existing_entry.content)
            if similarity >= self._dedup_threshold:
                return False

        return True

    @staticmethod
    def _jaccard_similarity(a: str, b: str) -> float:
        """Jaccard 相似度（字符级）"""
        if not a or not b:
            return 0.0
        set_a = set(a)
        set_b = set(b)
        intersection = set_a & set_b
        union = set_a | set_b
        return len(intersection) / len(union) if union else 0.0


# ===== MemoryStore =====


class MemoryStore:
    """记忆存储：SQLite 持久化"""

    def __init__(self, db_path: str | Path = "") -> None:
        self._db_path = str(db_path) if db_path else ":memory:"
        self._conn = sqlite3.connect(self._db_path)
        self._init_db()

    def _init_db(self) -> None:
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS memories (
                entry_id TEXT PRIMARY KEY,
                scope TEXT NOT NULL DEFAULT 'task',
                content TEXT NOT NULL,
                tags TEXT NOT NULL DEFAULT '{}',
                source TEXT NOT NULL DEFAULT '',
                task_name TEXT NOT NULL DEFAULT '',
                run_id TEXT NOT NULL DEFAULT '',
                priority REAL NOT NULL DEFAULT 0.5,
                created_at TEXT NOT NULL
            )
            """
        )
        self._conn.execute("CREATE INDEX IF NOT EXISTS idx_mem_scope ON memories(scope)")
        self._conn.execute("CREATE INDEX IF NOT EXISTS idx_mem_task ON memories(task_name)")
        self._conn.execute("CREATE INDEX IF NOT EXISTS idx_mem_source ON memories(source)")
        self._conn.commit()

    def write(self, entry: MemoryEntry) -> None:
        self._conn.execute(
            "INSERT OR REPLACE INTO memories (entry_id, scope, content, tags, source, task_name, run_id, priority, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (entry.entry_id, entry.scope, entry.content, json.dumps(entry.tags, ensure_ascii=False), entry.source, entry.task_name, entry.run_id, entry.priority, entry.created_at),
        )
        self._conn.commit()

    def query(self, scope: str = "", task_name: str = "", source: str = "", limit: int = 20) -> list[MemoryEntry]:
        conditions = []
        params: list[Any] = []
        if scope:
            conditions.append("scope = ?")
            params.append(scope)
        if task_name:
            conditions.append("task_name = ?")
            params.append(task_name)
        if source:
            conditions.append("source = ?")
            params.append(source)

        sql = "SELECT * FROM memories"
        if conditions:
            sql += " WHERE " + " AND ".join(conditions)
        sql += " ORDER BY priority DESC, created_at DESC LIMIT ?"
        params.append(limit)

        rows = self._conn.execute(sql, params).fetchall()
        return [
            MemoryEntry(
                entry_id=r[0], scope=r[1], content=r[2],
                tags=json.loads(r[3]) if isinstance(r[3], str) else r[3],
                source=r[4], task_name=r[5], run_id=r[6],
                priority=r[7], created_at=r[8],
            )
            for r in rows
        ]

    def delete(self, entry_id: str) -> None:
        self._conn.execute("DELETE FROM memories WHERE entry_id = ?", (entry_id,))
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()


# ===== MemoryManager =====


class MemoryManager:
    """记忆管理器门面"""

    def __init__(
        self,
        store: MemoryStore | None = None,
        salience_filter: SalienceFilter | None = None,
        policies: list[MemoryWritePolicy] | None = None,
    ) -> None:
        self._store = store or MemoryStore()
        self._filter = salience_filter or SalienceFilter()
        self._policies = policies or [WriteOnSuccess(), WriteFailureCase()]

    @property
    def store(self) -> MemoryStore:
        return self._store

    def add_policy(self, policy: MemoryWritePolicy) -> None:
        self._policies.append(policy)

    def write(self, run: TaskRun, content: str, source: str = "", tags: dict[str, str] | None = None) -> bool:
        """写入记忆（经策略和过滤器）"""
        entry = MemoryEntry(
            content=content,
            source=source or ("success" if run.status == TaskStatus.SUCCEEDED else "failure"),
            task_name=run.spec.name,
            run_id=run.run_id,
            tags=tags or {},
            priority=0.8 if run.status == TaskStatus.FAILED else 0.5,
        )

        # 策略检查
        if not any(p.should_write(run, entry) for p in self._policies):
            return False

        # 显著性过滤
        existing = self._store.query(task_name=run.spec.name, limit=50)
        if not self._filter.should_keep(entry, existing):
            return False

        self._store.write(entry)
        return True

    def recall(self, task_name: str = "", scope: str = "task", limit: int = 10) -> list[MemoryEntry]:
        """回忆相关记忆"""
        return self._store.query(task_name=task_name, scope=scope, limit=limit)