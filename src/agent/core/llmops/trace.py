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
from typing import Any, Optional, Sequence


@dataclass
class TraceSpan:
    """单次 LLM 调用记录。"""

    model: str
    use: str
    tokens_in: int = 0
    tokens_out: int = 0
    tokens_cached: int = 0
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
            "tokens_cached": self.tokens_cached,
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
            tokens_cached=int(d.get("tokens_cached", 0) or 0),
            latency_ms=float(d.get("latency_ms", 0.0)),
            cost=float(d.get("cost", 0.0)),
            ok=bool(d.get("ok", True)),
            error=str(d.get("error", "")),
            id=str(d.get("id", "")),
            at=float(d.get("at", 0.0)),
            meta=dict(d.get("meta", {}) or {}),
        )


#: 双记认定的时间窗（秒）。见 :func:`find_duplicate_pairs` 的实测依据。
DEDUPE_WINDOW_S = 2.0


def _span_field(span: Any, name: str, default: Any = 0) -> Any:
    """兼容 ``TraceSpan`` 对象与 dict（分析脚本常直接吃 jsonl 行）。"""
    if isinstance(span, dict):
        return span.get(name, default)
    return getattr(span, name, default)


def find_duplicate_pairs(spans: Sequence[Any]) -> list[tuple[int, int]]:
    """找出「同一次物理调用被记两次」的下标对（**纯函数**：不改数据、不聚合）。

    判定：``(tokens_in, tokens_out)`` 完全相同 **且** ``at`` 相差 ≤
    :data:`DEDUPE_WINDOW_S`。用 token 元组做键的理由：双记的两条来自同一次
    物理调用，token 逐字相同是事故的**直接指纹**（2026-09-18 双收口事故
    即"虚增恰好 100%"）。

    ⚠ 旧口径 ``round(at, 1)`` **实测失效**（9 项目 8513 条 span 复核）：
    两条 span 的 ``at`` 相差 **≈89ms** —— 唯一收口写于 provider 返回时、
    包装层补记写于包装层返回时，差值即包装层开销 —— 于是
    ``…469.399`` 与 ``…469.488`` 会 round 到不同值而**折叠不掉**。
    故本口径用**时间窗**而非取整。

    为什么需要它：熔断读 :meth:`TraceStore.totals` —— 若该读数被双记虚增，
    阈值就成了"摧毁扳机"（纪律 #16：读数驱动动作的链条须先有唯一性红线）。
    本函数把这个唯一性缺口变成**可观测**，而**不静默修改数据**
    （静默去重会让"虚增"与"漏记"两种相反失真都不可见）。
    """
    order = sorted(
        range(len(spans)), key=lambda i: float(_span_field(spans[i], "at", 0.0))
    )
    used = [False] * len(spans)
    pairs: list[tuple[int, int]] = []
    for pos, i in enumerate(order):
        if used[i]:
            continue
        key = (
            int(_span_field(spans[i], "tokens_in", 0)),
            int(_span_field(spans[i], "tokens_out", 0)),
        )
        t_i = float(_span_field(spans[i], "at", 0.0))
        for j in order[pos + 1:]:
            if used[j]:
                continue
            if float(_span_field(spans[j], "at", 0.0)) - t_i > DEDUPE_WINDOW_S:
                break  # order 已按 at 升序 ⇒ 后续只会更远
            if (
                int(_span_field(spans[j], "tokens_in", 0)),
                int(_span_field(spans[j], "tokens_out", 0)),
            ) == key:
                used[j] = True
                pairs.append((i, j))
                break
    return pairs


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
            self._spans = []  # noqa: SILENT_DEGRADE

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
            tcached = sum(s.tokens_cached for s in self._spans)
            cost = sum(s.cost for s in self._spans)
            fails = sum(1 for s in self._spans if not s.ok)
            lat = [s.latency_ms for s in self._spans if s.latency_ms > 0]
            avg_lat = (sum(lat) / len(lat)) if lat else 0.0
            return {
                "calls": len(self._spans),
                "tokens_in": tin,
                "tokens_out": tout,
                "tokens_total": tin + tout,
                "tokens_cached": tcached,
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

    def by_provider(self) -> dict[str, dict[str, Any]]:
        """按 provider 聚合（2026-09-20 新增）。

        为什么需要：跨 provider 判据稳健性（阈值/分位表是否可跨 provider 复用）
        与 P0-1 类成本优化（写章侧前缀缓存是否生效）**都以 provider 为分组维度**，
        而此前只有 ``by_use``。provider 取自 ``span.meta["provider"]``
        （唯一收口写真实 provider；补记路径 2026-09-20 起亦写，见 ``traced_llm``）。
        缺失时归入 ``"<unknown>"`` —— **不编造**，且该桶非空即是缺口信号。
        """
        return self._group_by(lambda s: str((s.meta or {}).get("provider") or "<unknown>"))

    def by_model(self) -> dict[str, dict[str, Any]]:
        """按模型名聚合（同上；模型名直接取 span.model）。"""
        return self._group_by(lambda s: str(s.model or "<unknown>"))

    def _group_by(self, key_of: Any) -> dict[str, dict[str, Any]]:
        out: dict[str, dict[str, Any]] = {}
        for s in self.spans():
            key = key_of(s)
            d = out.setdefault(
                key,
                {"calls": 0, "tokens_in": 0, "tokens_out": 0,
                 "tokens_cached": 0, "tokens_total": 0, "failures": 0, "cost": 0.0},
            )
            d["calls"] += 1
            d["tokens_in"] += s.tokens_in
            d["tokens_out"] += s.tokens_out
            d["tokens_cached"] += s.tokens_cached
            d["tokens_total"] += s.tokens_in + s.tokens_out
            d["cost"] += s.cost
            if not s.ok:
                d["failures"] += 1
        return out

    def duplicate_pairs(self) -> list[tuple[int, int]]:
        """本次 trace 中被**记了两次**的物理调用（观测面，不改数据）。"""
        return find_duplicate_pairs(self.spans())

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
