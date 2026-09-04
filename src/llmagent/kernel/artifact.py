"""ArtifactStore：内容寻址存储（M1 完整版：保留策略 + TTL）"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any


class ArtifactRef:
    """内容寻址引用"""

    def __init__(self, sha256: str) -> None:
        self.sha256 = sha256

    def __str__(self) -> str:
        return self.sha256


class RetentionPolicy:
    """保留策略"""

    def __init__(self, ttl_days: float = 0, max_count: int = 0) -> None:
        """
        Args:
            ttl_days: 存活天数（0 = 永不过期）
            max_count: 最大条目数（0 = 不限制）
        """
        self.ttl_days = ttl_days
        self.max_count = max_count

    def should_evict(self, created_at: str, current_count: int) -> bool:
        """判断是否应淘汰"""
        if self.ttl_days > 0:
            created = datetime.fromisoformat(created_at)
            if (datetime.now(timezone.utc) - created) > timedelta(days=self.ttl_days):
                return True
        if self.max_count > 0 and current_count > self.max_count:
            return True
        return False


class ArtifactStore:
    """制品存储：只存外部世界不可重现物（LLM 原始响应/工具返回/上下文快照）

    M1 增强：
    - 保留策略（TTL + 最大条目数）
    - 按时间戳排序
    - 内容类型索引
    """

    def __init__(
        self,
        db_path: str | Path = "",
        retention_policy: RetentionPolicy | None = None,
    ) -> None:
        self._db_path = str(db_path) if db_path else ":memory:"
        self._conn = sqlite3.connect(self._db_path)
        self._retention = retention_policy or RetentionPolicy()
        self._init_db()

    def _init_db(self) -> None:
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS artifacts (
                sha256 TEXT PRIMARY KEY,
                content_type TEXT NOT NULL,
                data BLOB NOT NULL,
                created_at TEXT NOT NULL,
                size_bytes INTEGER NOT NULL DEFAULT 0
            )
            """
        )
        self._conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_artifacts_ct ON artifacts(content_type)"
        )
        self._conn.commit()

    def put(self, blob: bytes | dict | str, content_type: str = "application/json") -> ArtifactRef:
        """存数据，返回内容寻址 ref；同内容幂等。"""
        if isinstance(blob, dict):
            data = json.dumps(blob, ensure_ascii=False).encode("utf-8")
        elif isinstance(blob, str):
            data = blob.encode("utf-8")
        else:
            data = blob
        sha256 = hashlib.sha256(data).hexdigest()
        now = datetime.now(timezone.utc).isoformat()
        self._conn.execute(
            "INSERT OR IGNORE INTO artifacts (sha256, content_type, data, created_at, size_bytes) VALUES (?, ?, ?, ?, ?)",
            (sha256, content_type, data, now, len(data)),
        )
        self._conn.commit()
        self._enforce_retention()
        return ArtifactRef(sha256)

    def get(self, ref: ArtifactRef) -> bytes | None:
        """按 ref 获取数据"""
        row = self._conn.execute(
            "SELECT data FROM artifacts WHERE sha256 = ?",
            (ref.sha256,),
        ).fetchone()
        return row[0] if row else None

    def get_meta(self, ref: ArtifactRef) -> dict | None:
        """获取元信息"""
        row = self._conn.execute(
            "SELECT sha256, content_type, created_at, size_bytes FROM artifacts WHERE sha256 = ?",
            (ref.sha256,),
        ).fetchone()
        if row is None:
            return None
        return {
            "sha256": row[0],
            "content_type": row[1],
            "created_at": row[2],
            "size_bytes": row[3],
        }

    def list_by_type(self, content_type: str, limit: int = 50) -> list[dict]:
        """按内容类型列出"""
        rows = self._conn.execute(
            "SELECT sha256, content_type, created_at, size_bytes FROM artifacts WHERE content_type = ? ORDER BY created_at DESC LIMIT ?",
            (content_type, limit),
        ).fetchall()
        return [
            {
                "sha256": r[0],
                "content_type": r[1],
                "created_at": r[2],
                "size_bytes": r[3],
            }
            for r in rows
        ]

    def _enforce_retention(self) -> None:
        """执行保留策略"""
        if self._retention.ttl_days > 0:
            cutoff = (datetime.now(timezone.utc) - timedelta(days=self._retention.ttl_days)).isoformat()
            self._conn.execute(
                "DELETE FROM artifacts WHERE created_at < ?",
                (cutoff,),
            )
        if self._retention.max_count > 0:
            count = self._conn.execute("SELECT COUNT(*) FROM artifacts").fetchone()[0]
            if count > self._retention.max_count:
                excess = count - self._retention.max_count
                self._conn.execute(
                    f"DELETE FROM artifacts WHERE sha256 IN (SELECT sha256 FROM artifacts ORDER BY created_at ASC LIMIT {excess})"
                )
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()