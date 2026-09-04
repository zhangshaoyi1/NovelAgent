"""EventBus：append-only 事件流（M1 完整版：schema 校验 + 分区索引 + 归档）"""

from __future__ import annotations

import json
import sqlite3
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any


# 已知事件类型的 schema 定义（M1 扩展点）
EVENT_SCHEMAS: dict[str, list[str]] = {
    "task.started": ["run_id", "spec_name", "kind"],
    "task.succeeded": ["run_id", "duration_ms"],
    "task.failed": ["run_id", "error", "error_class"],
    "task.skipped": ["run_id", "reason"],
    "task.timed_out": ["run_id", "timeout_s"],
    "llm.request": ["run_id", "provider", "model", "estimated_tokens"],
    "llm.response": ["run_id", "provider", "model", "input_tokens", "output_tokens", "latency_ms"],
    "llm.error": ["run_id", "provider", "model", "error", "error_class"],
    "checkpoint.saved": ["run_id", "seq", "fingerprint"],
    "budget.warn": ["run_id", "used_ratio", "remaining_cents"],
    "budget.melt": ["run_id", "used_ratio"],
    "metric.recorded": ["run_id", "name", "value"],
}


class EventSchemaError(ValueError):
    """事件 schema 校验失败"""


class EventBus:
    """事件总线：append-only、不可变；所有状态变化都落事件。

    M1 增强：
    - append() 对已知事件类型做 schema 校验
    - 按时间分区索引（每月分区）
    - 归档机制（移动旧事件到归档表）
    """

    def __init__(self, db_path: str | Path = "") -> None:
        self._db_path = str(db_path) if db_path else ":memory:"
        self._conn = sqlite3.connect(self._db_path)
        self._init_db()

    def _init_db(self) -> None:
        # 主表
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS events (
                seq INTEGER PRIMARY KEY AUTOINCREMENT,
                run_id TEXT NOT NULL,
                type TEXT NOT NULL,
                payload TEXT NOT NULL,
                created_at TEXT NOT NULL,
                year_month TEXT NOT NULL DEFAULT ''
            )
            """
        )
        self._conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_events_run_id ON events(run_id)"
        )
        # 按时间分区索引
        self._conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_events_ym ON events(year_month)"
        )
        self._conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_events_type ON events(type)"
        )
        # 归档表
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS events_archive (
                seq INTEGER,
                run_id TEXT NOT NULL,
                type TEXT NOT NULL,
                payload TEXT NOT NULL,
                created_at TEXT NOT NULL,
                year_month TEXT NOT NULL,
                archived_at TEXT NOT NULL,
                PRIMARY KEY (seq, year_month)
            )
            """
        )
        self._conn.commit()

    @staticmethod
    def _validate_event(type: str, payload: dict) -> None:
        """对已知事件类型做 schema 校验"""
        required = EVENT_SCHEMAS.get(type)
        if required is None:
            return  # 未知类型，跳过校验
        if not isinstance(payload, dict):
            raise EventSchemaError(f"事件 {type} 的 payload 必须是 dict")
        for field in required:
            if field not in payload:
                raise EventSchemaError(f"事件 {type} 缺少必填字段: {field}")

    def append(self, run_id: str, type: str, payload: dict | str) -> int:
        """append-only、不可变；返回 seq。"""
        payload_dict = payload if isinstance(payload, dict) else {"value": str(payload)}
        self._validate_event(type, payload_dict)
        payload_str = json.dumps(payload_dict, ensure_ascii=False)
        now = datetime.now(timezone.utc)
        year_month = now.strftime("%Y%m")
        cursor = self._conn.execute(
            "INSERT INTO events (run_id, type, payload, created_at, year_month) VALUES (?, ?, ?, ?, ?)",
            (run_id, type, payload_str, now.isoformat(), year_month),
        )
        self._conn.commit()
        return cursor.lastrowid or 0

    def get_events(self, run_id: str) -> list[dict]:
        """按 run_id 查询事件列表（含归档）"""
        rows = self._conn.execute(
            "SELECT seq, run_id, type, payload, created_at FROM events WHERE run_id = ? ORDER BY seq",
            (run_id,),
        ).fetchall()
        result = [
            {
                "seq": r[0],
                "run_id": r[1],
                "type": r[2],
                "payload": json.loads(r[3]),
                "created_at": r[4],
            }
            for r in rows
        ]
        # 也查归档
        archived = self._conn.execute(
            "SELECT seq, run_id, type, payload, created_at FROM events_archive WHERE run_id = ? ORDER BY seq",
            (run_id,),
        ).fetchall()
        result.extend([
            {
                "seq": r[0],
                "run_id": r[1],
                "type": r[2],
                "payload": json.loads(r[3]),
                "created_at": r[4],
            }
            for r in archived
        ])
        return result

    def archive_older_than(self, days: int = 30) -> int:
        """归档指定天数前的事件，返回归档条数"""
        cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
        rows = self._conn.execute(
            "SELECT seq, run_id, type, payload, created_at, year_month FROM events WHERE created_at < ?",
            (cutoff,),
        ).fetchall()
        count = 0
        for row in rows:
            seq, run_id, type, payload, created_at, year_month = row
            self._conn.execute(
                "INSERT OR IGNORE INTO events_archive (seq, run_id, type, payload, created_at, year_month, archived_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (seq, run_id, type, payload, created_at, year_month, datetime.now(timezone.utc).isoformat()),
            )
            self._conn.execute("DELETE FROM events WHERE seq = ?", (seq,))
            count += 1
        self._conn.commit()
        return count

    def query_by_type(self, type: str, limit: int = 100) -> list[dict]:
        """按事件类型查询"""
        rows = self._conn.execute(
            "SELECT seq, run_id, type, payload, created_at FROM events WHERE type = ? ORDER BY seq DESC LIMIT ?",
            (type, limit),
        ).fetchall()
        return [
            {
                "seq": r[0],
                "run_id": r[1],
                "type": r[2],
                "payload": json.loads(r[3]),
                "created_at": r[4],
            }
            for r in rows
        ]

    def close(self) -> None:
        self._conn.close()