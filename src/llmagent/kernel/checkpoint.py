"""CheckpointManager：Task 声明 checkpoint 时机 + 索引

M1 新增模块。
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


@dataclass
class Checkpoint:
    """检查点数据"""

    run_id: str
    seq: int
    fingerprint: str = ""
    provider: str = ""
    model: str = ""
    budget_ref: str = ""
    budget_remaining: float = 0.0
    context_fingerprint: str = ""
    status: str = ""
    created_at: str = ""
    meta: dict[str, Any] = field(default_factory=dict)


class CheckpointManager:
    """检查点管理器：Task 声明 checkpoint 时机 + 索引

    终态必落；Fork 的锁定变量在此（fingerprint + route 可分叉时锁定）。
    """

    def __init__(self, db_path: str | Path = "") -> None:
        self._db_path = str(db_path) if db_path else ":memory:"
        self._conn = sqlite3.connect(self._db_path)
        self._init_db()

    def _init_db(self) -> None:
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS checkpoints (
                run_id TEXT NOT NULL,
                seq INTEGER NOT NULL,
                fingerprint TEXT NOT NULL DEFAULT '',
                provider TEXT NOT NULL DEFAULT '',
                model TEXT NOT NULL DEFAULT '',
                budget_ref TEXT NOT NULL DEFAULT '',
                budget_remaining REAL NOT NULL DEFAULT 0.0,
                context_fingerprint TEXT NOT NULL DEFAULT '',
                status TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL,
                meta TEXT NOT NULL DEFAULT '{}',
                PRIMARY KEY (run_id, seq)
            )
            """
        )
        self._conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_cp_run_id ON checkpoints(run_id)"
        )
        self._conn.commit()

    def save(
        self,
        run_id: str,
        seq: int,
        fingerprint: str = "",
        provider: str = "",
        model: str = "",
        budget_ref: str = "",
        budget_remaining: float = 0.0,
        context_fingerprint: str = "",
        status: str = "",
        meta: dict[str, Any] | None = None,
    ) -> None:
        """保存检查点"""
        now = datetime.now(timezone.utc).isoformat()
        self._conn.execute(
            """
            INSERT OR REPLACE INTO checkpoints
            (run_id, seq, fingerprint, provider, model, budget_ref, budget_remaining, context_fingerprint, status, created_at, meta)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                run_id,
                seq,
                fingerprint,
                provider,
                model,
                budget_ref,
                budget_remaining,
                context_fingerprint,
                status,
                now,
                json.dumps(meta or {}, ensure_ascii=False),
            ),
        )
        self._conn.commit()

    def get(self, run_id: str, seq: int | None = None) -> list[Checkpoint]:
        """获取检查点列表（seq 为 None 返回所有，按 seq 降序）"""
        if seq is not None:
            rows = self._conn.execute(
                "SELECT * FROM checkpoints WHERE run_id = ? AND seq = ? ORDER BY seq DESC",
                (run_id, seq),
            ).fetchall()
        else:
            rows = self._conn.execute(
                "SELECT * FROM checkpoints WHERE run_id = ? ORDER BY seq DESC",
                (run_id,),
            ).fetchall()
        return [self._row_to_checkpoint(r) for r in rows]

    def get_latest(self, run_id: str) -> Checkpoint | None:
        """获取最新检查点"""
        row = self._conn.execute(
            "SELECT * FROM checkpoints WHERE run_id = ? ORDER BY seq DESC LIMIT 1",
            (run_id,),
        ).fetchone()
        if row is None:
            return None
        return self._row_to_checkpoint(row)

    def delete(self, run_id: str) -> None:
        """删除指定 run_id 的所有检查点"""
        self._conn.execute("DELETE FROM checkpoints WHERE run_id = ?", (run_id,))
        self._conn.commit()

    @staticmethod
    def _row_to_checkpoint(row: sqlite3.Row | tuple) -> Checkpoint:
        return Checkpoint(
            run_id=row[0],
            seq=row[1],
            fingerprint=row[2],
            provider=row[3],
            model=row[4],
            budget_ref=row[5],
            budget_remaining=row[6],
            context_fingerprint=row[7],
            status=row[8],
            created_at=row[9],
            meta=json.loads(row[10]) if isinstance(row[10], str) else row[10],
        )

    def close(self) -> None:
        self._conn.close()