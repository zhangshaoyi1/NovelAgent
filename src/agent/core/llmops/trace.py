"""LLMOps · 调用追踪（Phase 3）

记录每一次 LLM 调用的结构化工单（span）：模型、用途（creative/utility）、
输入输出 token、延迟、估算成本、是否成功、错误。供成本看板与回归分析消费。

设计（与项目"降级不阻断"一致）：
- 全局可插拔 ``Tracer``：默认 ``NullTracer``（零开销）；Service / TracedLLMClient
  注入 ``FileTraceStore`` 才会落盘。
- 持久化：``<project>/.state/llmops/trace.jsonl``（每行一个 span）。
- 纯离线、零依赖、零网络。
"""

from __future__ import annotations

import json
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional


@dataclass
class TraceSpan:
    """单次 LLM 调用记录。"""

    model: str
    use: str
    tokens_in: int = 0
    tokens_out: int = 0
    latency_ms: float = 0.0
    cost: float = 0.0
    ok: bool = True
    error: str = ""
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    at: float = field(default_factory=lambda: time.time())
    meta: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "model": self.model,
            "use": self.use,
            "tokens_in": self.tokens_in,
            "tokens_out": self.tokens_out,
            "latency_ms": self.latency_ms,
            "cost": self.cost,
            "ok": self.ok,
            "error": self.error,
            "at": self.at,
            "meta": self.meta,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "TraceSpan":
        return cls(
            model=str(d.get("model", "")),
            use=str(d.get("use", "")),
            tokens_in=int(d.get("tokens_in", 0)),
            tokens_out=int(d.get("tokens_out", 0)),
            latency_ms=float(d.get("latency_ms", 0.0)),
            cost=float(d.get("cost", 0.0)),
            ok=bool(d.get("ok", True)),
            error=str(d.get("error", "")),
            id=str(d.get("id", "")),
            at=float(d.get("at", 0.0)),
            meta=dict(d.get("meta", {}) or {}),
        )


class TraceStore:
    """调用追踪存储（文件持久化 + 聚合查询）。"""

    def __init__(self, project_dir: str | Path | None = None) -> None:
        self.project_dir = Path(project_dir) if project_dir else None
        self._lock = threading.RLock()
        self._spans: list[TraceSpan] = []
        if self.project_dir is not None:
            self._file = self.project_dir / ".state" / "llmops" / "trace.jsonl"
            self._load()
        else:
            self._file = None

    def _load(self) -> None:
        if self._file is None or not self._file.exists():
            return
        try:
            for line in self._file.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if line:
                    self._spans.append(TraceSpan.from_dict(json.loads(line)))
        except (json.JSONDecodeError, OSError):
            self._spans = []

    def _persist(self) -> None:
        if self._file is None:
            return
        self._file.parent.mkdir(parents=True, exist_ok=True)
        tmp = self._file.with_suffix(".tmp")
        lines = [json.dumps(s.to_dict(), ensure_ascii=False) for s in self._spans]
        tmp.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")
        tmp.replace(self._file)

    def record(self, span: TraceSpan) -> None:
        with self._lock:
            self._spans.append(span)
            self._persist()

    def spans(self) -> list[TraceSpan]:
        with self._lock:
            return list(self._spans)

    def totals(self) -> dict[str, Any]:
        """聚合统计。"""
        with self._lock:
            tin = sum(s.tokens_in for s in self._spans)
            tout = sum(s.tokens_out for s in self._spans)
            cost = sum(s.cost for s in self._spans)
            fails = sum(1 for s in self._spans if not s.ok)
            lat = [s.latency_ms for s in self._spans if s.latency_ms > 0]
            avg_lat = (sum(lat) / len(lat)) if lat else 0.0
            return {
                "calls": len(self._spans),
                "tokens_in": tin,
                "tokens_out": tout,
                "tokens_total": tin + tout,
                "cost": round(cost, 4),
                "failures": fails,
                "avg_latency_ms": round(avg_lat, 2),
            }

    def by_use(self) -> dict[str, dict[str, Any]]:
        out: dict[str, dict[str, Any]] = {}
        for s in self.spans():
            d = out.setdefault(s.use, {"calls": 0, "tokens_total": 0, "cost": 0.0})
            d["calls"] += 1
            d["tokens_total"] += s.tokens_in + s.tokens_out
            d["cost"] += s.cost
        return out

    def clear(self) -> None:
        with self._lock:
            self._spans = []
            self._persist()


# ---------------------------------------------------------------- 全局 Tracer
class NullTracer:
    """默认无操作 Tracer（零开销）。"""

    def record(self, span: TraceSpan) -> None:  # noqa: D401
        return None


_global_tracer: Any = NullTracer()


def get_tracer() -> Any:
    return _global_tracer


def set_tracer(tracer: Any) -> None:
    global _global_tracer
    _global_tracer = tracer


def usage_snapshot() -> dict[str, int]:
    """当前全局 tracer 的累计用量快照（NullTracer / 异常时全 0）。

    供章级用量统计做「窗口差值」：写章前取一次、写章后取一次，
    两者相减即得本章 tokens_in / tokens_out / 调用次数。
    """
    try:
        tr = get_tracer()
        if isinstance(tr, NullTracer):
            return {"calls": 0, "tokens_in": 0, "tokens_out": 0}
        t = tr.totals()
        return {
            "calls": int(t.get("calls", 0) or 0),
            "tokens_in": int(t.get("tokens_in", 0) or 0),
            "tokens_out": int(t.get("tokens_out", 0) or 0),
        }
    except Exception:  # noqa: BLE001 - 统计失败不影响写作
        return {"calls": 0, "tokens_in": 0, "tokens_out": 0}
