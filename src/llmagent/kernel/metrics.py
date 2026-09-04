"""Metrics：打点门面（M1 完整版：8 指标自动计算 + Tagger + 持久化存储）"""

from __future__ import annotations

import json
import sqlite3
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .task import TaskRun, TaskStatus


@dataclass
class Span:
    """调用链 Span"""

    span_id: str
    run_id: str
    parent_span_id: str = ""
    started_at: float = 0.0
    finished_at: float = 0.0
    status: str = ""
    tags: dict[str, str] = field(default_factory=dict)


class SpanBuilder:
    """Span 构建器：TaskRun 树 == Span 树（天然对齐）"""

    def __init__(self) -> None:
        self._spans: dict[str, Span] = {}

    def start(self, run: TaskRun) -> Span:
        span = Span(
            span_id=run.run_id,
            run_id=run.run_id,
            parent_span_id=run.parent_run_id,
            started_at=time.monotonic(),
            status=run.status.value,
            tags={"spec_name": run.spec.name, "kind": run.spec.kind.value},
        )
        self._spans[run.run_id] = span
        return span

    def finish(self, span: Span, status: TaskStatus) -> None:
        span.finished_at = time.monotonic()
        span.status = status.value

    def get_span_tree(self, root_run_id: str) -> list[Span]:
        """获取指定根节点的 Span 树"""
        root = self._spans.get(root_run_id)
        if root is None:
            return []
        children = [
            s for s in self._spans.values() if s.parent_span_id == root_run_id
        ]
        return [root] + children


class Tagger:
    """标签合并器：业务 tags 与系统属性合并"""

    @staticmethod
    def merge(
        run: TaskRun,
        span: Span,
        extra_tags: dict[str, str] | None = None,
    ) -> dict[str, str]:
        """合并业务 tags 与系统属性"""
        tags = dict(span.tags)
        tags.update(run.spec.tags)
        if extra_tags:
            tags.update(extra_tags)
        tags["run_id"] = run.run_id
        tags["status"] = run.status.value
        tags["kind"] = run.spec.kind.value
        tags["spec_name"] = run.spec.name
        return tags


class MetricRegistry:
    """指标注册中心：M1 持久化存储 + 8 指标自动计算"""

    METRIC_NAMES = [
        "task_duration_seconds",
        "task_attempts",
        "task_first_attempt_success_rate",
        "task_quality_score",
        "task_validation_failure_total",
        "task_degraded_total",
        "task_cost_cents",
        "task_tokens_total",
    ]

    def __init__(self, db_path: str | Path = "") -> None:
        self._db_path = str(db_path) if db_path else ":memory:"
        self._conn = sqlite3.connect(self._db_path)
        self._init_db()
        # 内存缓存（快速查询）
        self._cache: dict[str, list[float]] = {}

    def _init_db(self) -> None:
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS metrics (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                run_id TEXT NOT NULL,
                name TEXT NOT NULL,
                value REAL NOT NULL,
                tags TEXT NOT NULL DEFAULT '{}',
                created_at TEXT NOT NULL
            )
            """
        )
        self._conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_metrics_name ON metrics(name)"
        )
        self._conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_metrics_run_id ON metrics(run_id)"
        )
        self._conn.commit()

    def record(self, run: TaskRun, name: str, value: float, tags: dict[str, str] | None = None) -> None:
        """记录指标（内存 + 持久化）"""
        if name not in self._cache:
            self._cache[name] = []
        self._cache[name].append(value)
        now = datetime.now(timezone.utc).isoformat()
        self._conn.execute(
            "INSERT INTO metrics (run_id, name, value, tags, created_at) VALUES (?, ?, ?, ?, ?)",
            (run.run_id, name, value, json.dumps(tags or {}, ensure_ascii=False), now),
        )
        self._conn.commit()

    def summary(self, name: str) -> dict[str, float]:
        vals = self._cache.get(name, [])
        if not vals:
            return {"count": 0, "sum": 0.0, "avg": 0.0, "min": 0.0, "max": 0.0}
        return {
            "count": len(vals),
            "sum": sum(vals),
            "avg": sum(vals) / len(vals),
            "min": min(vals),
            "max": max(vals),
        }

    def query_by_name(self, name: str, limit: int = 100) -> list[dict]:
        """按指标名查询持久化记录"""
        rows = self._conn.execute(
            "SELECT run_id, name, value, tags, created_at FROM metrics WHERE name = ? ORDER BY created_at DESC LIMIT ?",
            (name, limit),
        ).fetchall()
        return [
            {
                "run_id": r[0],
                "name": r[1],
                "value": r[2],
                "tags": json.loads(r[3]),
                "created_at": r[4],
            }
            for r in rows
        ]

    def close(self) -> None:
        self._conn.close()


class Metrics:
    """打点门面"""

    def __init__(
        self,
        span_builder: SpanBuilder | None = None,
        metric_registry: MetricRegistry | None = None,
        tagger: Tagger | None = None,
    ) -> None:
        self.span_builder = span_builder or SpanBuilder()
        self.metric_registry = metric_registry or MetricRegistry()
        self.tagger = tagger or Tagger()

    def track(self, run: TaskRun, event_type: str = "", extra_tags: dict[str, Any] | None = None) -> None:
        """Kernel 状态转移点统一调用；Task 零感知。"""
        tags: dict[str, str] = {"event_type": event_type}
        if extra_tags:
            tags.update(extra_tags)  # type: ignore[arg-type]

        if event_type == "started":
            span = self.span_builder.start(run)
            tags = self.tagger.merge(run, span)

        elif event_type in ("succeeded", "failed", "skipped", "timed_out"):
            span = self.span_builder._spans.get(run.run_id)
            if span:
                self.span_builder.finish(span, run.status)
                duration = span.finished_at - span.started_at
                tags = self.tagger.merge(run, span, {"event_type": event_type})

                # 8 指标自动计算
                self.metric_registry.record(run, "task_duration_seconds", duration, tags)
                self.metric_registry.record(run, "task_attempts", float(run.attempt), tags)

                if event_type == "succeeded":
                    self._record_success_metrics(run, tags)
                elif event_type == "failed":
                    self.metric_registry.record(run, "task_validation_failure_total", 1.0, tags)
                    self.metric_registry.record(run, "task_degraded_total", 1.0, tags)

        elif event_type == "llm.cost":
            cost = tags.get("cost_cents", 0.0)
            if isinstance(cost, str):
                cost = float(cost)
            tokens = tags.get("tokens", 0)
            if isinstance(tokens, str):
                tokens = int(tokens)
            self.metric_registry.record(run, "task_cost_cents", float(cost), tags)
            self.metric_registry.record(run, "task_tokens_total", float(tokens), tags)

    def _record_success_metrics(self, run: TaskRun, tags: dict[str, str]) -> None:
        """记录成功相关的指标"""
        is_first_attempt = run.attempt == 0
        self.metric_registry.record(
            run,
            "task_first_attempt_success_rate",
            1.0 if is_first_attempt else 0.0,
            tags,
        )
        self.metric_registry.record(run, "task_quality_score", 1.0, tags)