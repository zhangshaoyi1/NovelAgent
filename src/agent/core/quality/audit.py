"""质量审计日志（HA-Eval L5 · 可观测层）

背景
----
2026-09-08 事故的定位耗时远大于修复耗时：串值、缓存复用、量纲错配这三类证据
全部散在 ``trace.jsonl``（只有 token/latency，无维度语义）与 ``conversation.jsonl``
（只有 eval 结论）里，复盘时只能靠人工比对 token 数、逐行读 LLM 响应才能拼出真相。

设计
----
把「每次体检的维度级快照」落成一条结构化审计记录
（``<project>/.state/quality_audit.jsonl``），并提供一个离线回放检测器
:func:`detect_anomalies`，把三类事故指纹变成可程序化点名：

1. **批级串值**（``cache_collision``）：同一轮内多个 LLM 维度 value 全等且量纲不同；
2. **跨轮恒值**（``stale_value``）：同一维度在多次体检中 value 恒等（>0）；
3. **缓存命中**（``cache_hit``）：判定类维度命中语义缓存（L1 已默认拒绝，此为纵深审计）。

落盘是**只追加、不阻断**：写失败静默降级（与项目「降级不阻断」一致）。

依赖方向：本模块仅依赖标准库 + ``dimension_registry``（领域层），
不 import ``agent.agents.*``，避免循环依赖。
"""

from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Iterable


# ============================================================
# 数据结构
# ============================================================
@dataclass
class AuditDimension:
    """一条维度级审计快照。"""

    name: str
    value: float
    source: str = ""
    unit: str = ""            # count / score / ratio / bool（""=未登记）
    confidence: float = 1.0
    cache_hit: bool = False
    prompt_hash: str = ""
    response_hash: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class AuditRecord:
    """一轮体检的审计快照。"""

    at: float
    overall_pass: bool
    dimensions: list[AuditDimension] = field(default_factory=list)
    score: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "at": self.at,
            "overall_pass": self.overall_pass,
            "score": round(self.score, 2),
            "dimensions": [d.to_dict() for d in self.dimensions],
        }


# ============================================================
# 存储（只追加）
# ============================================================
class QualityAuditStore:
    """质量审计日志的只追加存储。"""

    def __init__(self, project_dir: str | Path | None = None) -> None:
        self.project_dir = Path(project_dir) if project_dir else None
        self._file = (
            self.project_dir / ".state" / "quality_audit.jsonl"
            if self.project_dir is not None
            else None
        )

    def append(self, record: AuditRecord) -> None:
        """追加一条审计记录；失败静默降级（审计不得阻断写作）。"""
        if self._file is None:
            return
        try:
            self._file.parent.mkdir(parents=True, exist_ok=True)
            with self._file.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(record.to_dict(), ensure_ascii=False) + "\n")
        except OSError:  # noqa: BLE001 - 审计写失败不阻断
            return


def record_from_report(report: Any) -> AuditRecord:
    """从 ``NovelHealthReport``（或其任意 duck-typed 等价物）构造审计记录。

    对 ``DimensionResult`` 只读访问 ``name/value/source/evidence``，不依赖具体类，
    故可用在测试中构造最小对象。
    """
    dims: list[AuditDimension] = []
    for d in getattr(report, "dimensions", []) or []:
        evidence = getattr(d, "evidence", None)
        spec = getattr(d, "spec", None)
        dims.append(
            AuditDimension(
                name=str(getattr(d, "name", "?")),
                value=float(getattr(d, "value", 0.0)),
                source=str(getattr(d, "source", "")),
                unit=getattr(spec, "unit", None).value if spec is not None else "",
                confidence=float(getattr(evidence, "confidence", 1.0)),
                cache_hit=bool(getattr(evidence, "cache_hit", False)),
                prompt_hash=str(getattr(evidence, "prompt_hash", "")),
                response_hash=str(getattr(evidence, "response_hash", "")),
            )
        )
    return AuditRecord(
        at=time.time(),
        overall_pass=bool(getattr(report, "overall_pass", True)),
        score=float(getattr(report, "score", 0.0)),
        dimensions=dims,
    )


def load_records(project_dir: str | Path) -> list[AuditRecord]:
    """读取审计日志；不存在 / 解析失败返回空列表。"""
    f = Path(project_dir) / ".state" / "quality_audit.jsonl"
    if not f.exists():
        return []
    out: list[AuditRecord] = []
    try:
        for line in f.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            raw = json.loads(line)
            out.append(
                AuditRecord(
                    at=float(raw.get("at", 0.0)),
                    overall_pass=bool(raw.get("overall_pass", True)),
                    score=float(raw.get("score", 0.0)),
                    dimensions=[
                        AuditDimension(
                            name=str(x.get("name", "?")),
                            value=float(x.get("value", 0.0)),
                            source=str(x.get("source", "")),
                            unit=str(x.get("unit", "")),
                            confidence=float(x.get("confidence", 1.0)),
                            cache_hit=bool(x.get("cache_hit", False)),
                            prompt_hash=str(x.get("prompt_hash", "")),
                            response_hash=str(x.get("response_hash", "")),
                        )
                        for x in raw.get("dimensions", [])
                    ],
                )
            )
    except (json.JSONDecodeError, OSError):  # noqa: BLE001
        return []  # noqa: SILENT_DEGRADE
    return out


# ============================================================
# 回放检测
# ============================================================
@dataclass
class Anomaly:
    """一条异常发现。"""

    kind: str          # cache_collision | stale_value | cache_hit
    detail: str
    record_index: int = 0
    dimension_names: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "detail": self.detail,
            "record_index": self.record_index,
            "dimension_names": list(self.dimension_names),
        }


def detect_anomalies(
    records: Iterable[AuditRecord] | None = None,
    *,
    project_dir: str | Path | None = None,
) -> list[Anomaly]:
    """离线回放检测三类事故指纹。

    Args:
        records: 直接传入审计记录（优先）。
        project_dir: 无 records 时从 ``<project>/.state/quality_audit.jsonl`` 读取。

    Returns:
        ``Anomaly`` 列表（可能为空）。检测本身绝不抛异常（诊断失败返回空）。
    """
    try:
        recs = list(records) if records is not None else (
            load_records(project_dir) if project_dir else []
        )
    except Exception:  # noqa: BLE001
        return []
    out: list[Anomaly] = []

    # 跨轮恒值检测需要的累计：dim_name -> [(record_index, value)]
    seen: dict[str, list[tuple[int, float]]] = {}

    for idx, rec in enumerate(recs):
        # ---- ① 批级串值：同轮多个 LLM 维度 value 全等且量纲不同 ----
        llm_dims = [d for d in rec.dimensions if d.source.startswith("llm")]
        # 只有量纲混合的批次才做串值检测（全 count 维 value=0 属正常）
        units = {d.unit for d in llm_dims if d.unit}
        if len(llm_dims) >= 3 and len(units) >= 2:
            vals = {d.value for d in llm_dims}
            if len(vals) == 1:
                out.append(
                    Anomaly(
                        kind="cache_collision",
                        detail=(
                            f"第 {idx} 轮：{len(llm_dims)} 个 LLM 维度 value 全等"
                            f"（={next(iter(vals))}）且量纲混合（{sorted(units)}），"
                            f"疑似缓存碰撞/响应复用"
                        ),
                        record_index=idx,
                        dimension_names=[d.name for d in llm_dims],
                    )
                )

        # ---- ② 缓存命中：判定类维度命中语义缓存 ----
        for d in rec.dimensions:
            if d.cache_hit:
                out.append(
                    Anomaly(
                        kind="cache_hit",
                        detail=(
                            f"第 {idx} 轮：维度 {d.name} 命中语义缓存"
                            f"（prompt_hash={d.prompt_hash[:8]}），判定结果不可复用"
                        ),
                        record_index=idx,
                        dimension_names=[d.name],
                    )
                )
            # ---- ③ 跨轮恒值（仅 LLM 维、value>0）----
            if d.source.startswith("llm") and d.value > 0:
                seen.setdefault(d.name, []).append((idx, d.value))

    # 恒值判定：同一维度 ≥2 轮、值完全相同（>0），且记录轮次不同
    for name, entries in seen.items():
        if len(entries) < 2:
            continue
        values = [v for _idx, v in entries]
        if len(set(values)) == 1:
            out.append(
                Anomaly(
                    kind="stale_value",
                    detail=(
                        f"维度 {name} 在 {len(entries)} 轮体检中 value 恒等"
                        f"（={values[0]}），可能为串值/复用/未真正重算"
                    ),
                    record_index=entries[-1][0],
                    dimension_names=[name],
                )
            )

    return out


def audit_report(project_dir: str | Path) -> dict[str, Any]:
    """一键回放并聚合为 dict（供 doctor eval-audit 消费）。"""
    anomalies = detect_anomalies(project_dir=project_dir)
    by_kind: dict[str, int] = {}
    for a in anomalies:
        by_kind[a.kind] = by_kind.get(a.kind, 0) + 1
    return {
        "records": len(load_records(project_dir)),
        "anomalies": [a.to_dict() for a in anomalies],
        "summary": by_kind,
        "clean": not anomalies,
    }
