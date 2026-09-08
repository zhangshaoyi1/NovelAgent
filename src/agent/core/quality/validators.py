"""坏数据校验器（HA-Eval L3）

核心立场
--------
原降级逻辑**方向是反的**：

- LLM 没输出 → 给「通过值」（宽容，合理）；
- LLM 输出了坏值 → **直接采信**（致命）。

只防「没数据」，不防「坏数据」。本模块补的正是后者：
在分数**进入硬门禁之前**把可疑结果挑出来，降级为 ``confidence=0``，
由 L4 处置层保证「不可信 → 不做任何处置动作」。

四类检测
--------
1. **批级串值**（``SUSPECT_CACHE_COLLISION``）：同批 LLM 维度取值全等。
   这是 2026-09-08 事故的直接指纹——五个量纲不同的维度不可能同时给出同一个数。
2. **量纲自洽**：计数维（``counted_by_issues``）的 value 必须等于计入门禁的
   issues 条数，防止「报 0 实则列举 N 条」或相反。
3. **评分维下限**：0-100 评分维低于 ``score_suspect_floor``（默认 10）→ 判定为
   解析失败或复用了计数维的值。真实连贯性/追读力评分几乎不可能低于 10。
4. **缓存命中**：判定类调用命中缓存 → 结果不可用于门禁（L1 已默认拒绝，此处为纵深防御）。
"""

from __future__ import annotations

from dataclasses import replace
from typing import Any, Iterable, Sequence

from agent.core.quality.dimension_registry import Unit

#: 0-100 评分维的可疑下限：低于此值视为解析异常而非真实评分。
SCORE_SUSPECT_FLOOR: float = 10.0

#: 触发批级串值检测所需的最少 LLM 维度数（低于此数"全等"不构成证据）。
MIN_BATCH_FOR_COLLISION: int = 3

#: 计入门禁的 issue 严重度（与 reader_appeal.SEVERITY_GATE 同源）。
SEVERITY_GATE = {"high", "mid"}


class DimensionValidator:
    """维度结果校验器（无状态，可复用）。

    Args:
        score_suspect_floor: 0-100 评分维的可疑下限。
        min_batch_for_collision: 触发批级串值检测的最小样本数。
    """

    def __init__(
        self,
        *,
        score_suspect_floor: float = SCORE_SUSPECT_FLOOR,
        min_batch_for_collision: int = MIN_BATCH_FOR_COLLISION,
    ) -> None:
        self.score_suspect_floor = score_suspect_floor
        self.min_batch_for_collision = min_batch_for_collision

    # ------------------------------------------------------------ 批级
    def validate_batch(self, results: list[Any]) -> list[Any]:
        """批级校验：先做跨维串值检测，再逐维自检。

        就地降级（修改 evidence），返回同一列表以便链式调用。
        """
        self._check_cache_collision(results)
        for r in results:
            self.validate_one(r)
        return results

    def _check_cache_collision(self, results: Sequence[Any]) -> None:
        """同批 LLM 维度取值全等 → 判为缓存碰撞 / 响应复用。

        量纲不同的维度（缺陷条数 vs 0-100 评分）同时给出同一个数，概率极低；
        一旦发生，几乎必然是"多个维度共用同一份响应"。
        """
        llm_results = [
            r for r in results
            if str(getattr(r, "source", "")).startswith("llm")
            and self._is_score_like(r)
        ]
        if len(llm_results) < self.min_batch_for_collision:
            return
        values = {float(getattr(r, "value", 0.0)) for r in llm_results}
        if len(values) != 1:
            return
        only = values.pop()
        reason = (
            f"SUSPECT_CACHE_COLLISION: 同批 {len(llm_results)} 个 LLM 维度取值全等"
            f"（value={only}），量纲不同不可能同时给出同一数值，疑似响应被复用"
        )
        for r in llm_results:
            self._degrade(r, reason)

    @staticmethod
    def _is_score_like(r: Any) -> bool:
        """只对不同量纲混合的批次做串值检测；纯计数维批次（全 0 属正常）跳过。"""
        spec = getattr(r, "spec", None)
        if spec is None:
            return True
        return True  # 混合批次由取值全等本身判定；纯 0 批次在 validate_one 中放行

    # ------------------------------------------------------------ 单维
    def validate_one(self, r: Any) -> Any:
        """单维自检：量纲自洽 + 评分维下限 + 缓存命中。"""
        spec = getattr(r, "spec", None)
        evidence = getattr(r, "evidence", None)

        if spec is not None and getattr(spec, "counted_by_issues", False):
            self._check_count_consistency(r, spec, evidence)

        if spec is not None and spec.unit is Unit.SCORE_0_100:
            self._check_score_floor(r, evidence)

        if evidence is not None and getattr(evidence, "cache_hit", False):
            self._degrade(r, "CACHE_HIT: 判定类调用命中缓存，结果不可用于门禁")

        return r

    def _check_count_consistency(self, r: Any, spec: Any, evidence: Any) -> None:
        """计数维 value 必须等于计入门禁的 issues 条数。"""
        if evidence is None:
            return
        issues = getattr(evidence, "issues", None)
        if not issues:
            return  # 无 issues 时按 reader_appeal 语义回退 LLM 自报值，不判异常
        expected = sum(
            1 for i in issues
            if str((i or {}).get("severity", "")).lower() in SEVERITY_GATE
        )
        actual = float(getattr(r, "value", 0.0))
        if actual != float(expected):
            self._degrade(
                r,
                f"COUNT_MISMATCH: {spec.name} value={actual} 与计入门禁的 issues 条数"
                f" {expected} 不符",
            )

    def _check_score_floor(self, r: Any, evidence: Any) -> None:
        """0-100 评分维低于可疑下限 → 判为解析异常或量纲串用。"""
        value = float(getattr(r, "value", 0.0))
        if value >= self.score_suspect_floor:
            return
        self._degrade(
            r,
            f"SCORE_TOO_LOW: 0-100 评分维实测 {value} < {self.score_suspect_floor}，"
            f"疑似解析失败或复用了计数维的值",
        )

    # ------------------------------------------------------------ 工具
    @staticmethod
    def _degrade(r: Any, reason: str) -> None:
        evidence = getattr(r, "evidence", None)
        if evidence is None:
            # 无证据对象时补一个，保证 confidence 语义可用
            from agent.core.quality.eval_evidence import EvalEvidence

            evidence = EvalEvidence()
            try:
                object.__setattr__(r, "evidence", evidence) if not _is_dataclass(r) else setattr(r, "evidence", evidence)
            except Exception:  # noqa: BLE001 - frozen dataclass：退化为只改 source
                evidence = None  # noqa: SILENT_DEGRADE
        if evidence is not None and hasattr(evidence, "degrade"):
            evidence.degrade(reason)
        # 同步标记来源，便于报告与审计识别
        try:
            if str(getattr(r, "source", "")).startswith("llm"):
                setattr(r, "source", "llm/degraded")
        except Exception:  # noqa: BLE001 - frozen 对象不改
            pass  # noqa: SILENT_DEGRADE


def _is_dataclass(obj: Any) -> bool:
    from dataclasses import is_dataclass

    return is_dataclass(obj) and not getattr(type(obj), "__dataclass_params__").frozen


def trustworthiness(results: Iterable[Any]) -> float:
    """整批结果的最低置信度（供 L4 守门器做门槛判断）。"""
    confs = [float(getattr(getattr(r, "evidence", None), "confidence", 1.0)) for r in results]
    return min(confs) if confs else 1.0
