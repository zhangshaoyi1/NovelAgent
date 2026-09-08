"""HA-Eval L3 · 坏数据校验器回归测试

原降级逻辑**方向是反的**：LLM 没输出 → 给通过值（合理）；LLM 输出坏值 → 直接采信
（致命）。本文件锁死四类坏数据检测，确保可疑分数在进入硬门禁前被降级为
``confidence=0``，从而被 L4 处置层拒绝触发任何动作。

其中 ``test_five_dims_all_equal_3`` 是 2026-09-08 事故的**精确复现**：
五个量纲不同的维度全部取值 3.0。
"""

from __future__ import annotations

import pytest

from agent.agents.evaluator_types import DimensionResult
from agent.core.quality.eval_evidence import EvalEvidence, build_evidence
from agent.core.quality.validators import DimensionValidator, trustworthiness


def _dim(
    name: str,
    value: float,
    *,
    threshold: float = 0.0,
    direction: str = "<=",
    required: bool = True,
    source: str = "llm/default",
    evidence: EvalEvidence | None = None,
) -> DimensionResult:
    return DimensionResult(
        name, name, value, threshold, direction, required, source, evidence=evidence
    )


# ============================================================
# 1. 批级串值（本次事故的直接指纹）
# ============================================================
class TestCacheCollision:
    def test_five_dims_all_equal_3(self):
        """事故复现：人设/设定/逻辑（计数维）与连贯/追读（0-100 维）全部取 3.0。"""
        dims = [
            _dim("character_stability_high", 3.0, evidence=EvalEvidence()),
            _dim("setting_consistency_high", 3.0, evidence=EvalEvidence()),
            _dim("logic_holes", 3.0, evidence=EvalEvidence()),
            _dim("coherence", 3.0, direction=">=", required=False,
                 threshold=85.0, evidence=EvalEvidence()),
            _dim("readability", 3.0, direction=">=", required=False,
                 threshold=80.0, evidence=EvalEvidence()),
        ]
        DimensionValidator().validate_batch(dims)
        for d in dims:
            assert d.confidence == 0.0, f"{d.name} 应被判为不可信"
            assert any("SUSPECT_CACHE_COLLISION" in v for v in d.evidence.validation)
        assert trustworthiness(dims) == 0.0

    def test_different_values_not_flagged(self):
        """正常差异化评分不得误伤。"""
        dims = [
            _dim("character_stability_high", 0.0, evidence=EvalEvidence()),
            _dim("setting_consistency_high", 1.0, evidence=EvalEvidence()),
            _dim("coherence", 88.0, direction=">=", required=False,
                 threshold=85.0, evidence=EvalEvidence()),
        ]
        DimensionValidator().validate_batch(dims)
        assert trustworthiness(dims) == 1.0

    def test_all_zero_count_dims_not_flagged_by_batch(self):
        """纯计数维全 0 属正常（无缺陷），批级检测不介入（样本 <3 时跳过）。"""
        dims = [
            _dim("character_stability_high", 0.0, evidence=EvalEvidence()),
            _dim("setting_consistency_high", 0.0, evidence=EvalEvidence()),
        ]
        DimensionValidator().validate_batch(dims)
        assert trustworthiness(dims) == 1.0

    def test_requires_min_batch_size(self):
        """不足 3 个 LLM 维度时不构成证据。"""
        dims = [
            _dim("coherence", 60.0, direction=">=", required=False,
                 threshold=85.0, evidence=EvalEvidence()),
            _dim("readability", 60.0, direction=">=", required=False,
                 threshold=80.0, evidence=EvalEvidence()),
        ]
        DimensionValidator().validate_batch(dims)
        assert trustworthiness(dims) == 1.0


# ============================================================
# 2. 量纲自洽：计数维 value 必须等于 issues 条数
# ============================================================
class TestCountConsistency:
    def test_mismatch_degrades(self):
        ev = EvalEvidence(issues=[
            {"severity": "high", "desc": "a"},
            {"severity": "mid", "desc": "b"},
            {"severity": "low", "desc": "c"},
        ])
        d = _dim("logic_holes", 0.0, evidence=ev)
        DimensionValidator().validate_one(d)
        assert d.confidence == 0.0
        assert any("COUNT_MISMATCH" in v for v in ev.validation)

    def test_match_passes(self):
        ev = EvalEvidence(issues=[
            {"severity": "high", "desc": "a"},
            {"severity": "mid", "desc": "b"},
            {"severity": "low", "desc": "c"},  # low 不计入门禁
        ])
        d = _dim("logic_holes", 2.0, evidence=ev)
        DimensionValidator().validate_one(d)
        assert d.confidence == 1.0

    def test_no_issues_skipped(self):
        """无 issues 时按语义回退 LLM 自报值，不判异常。"""
        d = _dim("logic_holes", 0.0, evidence=EvalEvidence(issues=[]))
        DimensionValidator().validate_one(d)
        assert d.confidence == 1.0


# ============================================================
# 3. 评分维下限
# ============================================================
class TestScoreFloor:
    def test_abnormally_low_score_degrades(self):
        d = _dim("coherence", 3.0, direction=">=", required=False,
                 threshold=85.0, evidence=EvalEvidence())
        DimensionValidator().validate_one(d)
        assert d.confidence == 0.0
        assert any("SCORE_TOO_LOW" in v for v in d.evidence.validation)

    def test_reasonable_score_passes(self):
        d = _dim("coherence", 72.0, direction=">=", required=False,
                 threshold=85.0, evidence=EvalEvidence())
        DimensionValidator().validate_one(d)
        assert d.confidence == 1.0


# ============================================================
# 4. 缓存命中 → 不可信
# ============================================================
class TestCacheHit:
    def test_cache_hit_degrades(self):
        d = _dim("coherence", 90.0, direction=">=", required=False,
                 threshold=85.0, evidence=EvalEvidence(cache_hit=True))
        DimensionValidator().validate_one(d)
        assert d.confidence == 0.0
        assert any("CACHE_HIT" in v for v in d.evidence.validation)


# ============================================================
# 5. 证据对象
# ============================================================
class TestEvalEvidence:
    def test_prompt_hash_differs_by_label(self):
        """维度标签不同 → prompt 指纹不同（事故中五维标签不同却被判同一请求）。"""
        body = "正文" * 100
        a = build_evidence(messages=[{"role": "user", "content": f"人设稳定\n\n{body}"}])
        b = build_evidence(messages=[{"role": "user", "content": f"连贯性\n\n{body}"}])
        assert a.prompt_hash != b.prompt_hash

    def test_response_hash_same_for_same_text(self):
        a = build_evidence(raw_response='{"value": 3}')
        b = build_evidence(raw_response='{"value": 3}')
        assert a.response_hash == b.response_hash

    def test_degrade_is_idempotent(self):
        ev = EvalEvidence()
        ev.degrade("x")
        ev.degrade("x")
        assert ev.validation == ["x"]
        assert ev.degraded is True

    def test_source_marked_degraded(self):
        d = _dim("coherence", 3.0, direction=">=", required=False,
                 threshold=85.0, evidence=EvalEvidence())
        DimensionValidator().validate_one(d)
        assert d.source == "llm/degraded"


# ============================================================
# 6. 守门契约：不可信维度不得触发处置（供 L4 消费）
# ============================================================
class TestGateContract:
    def test_untrusted_dim_exposes_zero_confidence(self):
        dims = [
            _dim("logic_holes", 3.0, evidence=EvalEvidence()),
            _dim("coherence", 3.0, direction=">=", required=False,
                 threshold=85.0, evidence=EvalEvidence()),
            _dim("readability", 3.0, direction=">=", required=False,
                 threshold=80.0, evidence=EvalEvidence()),
        ]
        DimensionValidator().validate_batch(dims)
        failed = [d for d in dims if not d.passed]
        assert failed, "这些维度确实未达标（否则无从谈起误触发）"
        assert all(d.confidence == 0.0 for d in failed), \
            "未达标且不可信 → L4 必须走 RETRY_EVAL 而非 ROLLBACK_REWRITE"

    def test_computed_dims_always_trusted(self):
        """确定性计算维无 LLM 依赖，恒可信。"""
        d = _dim("pacing_abnormal", 0.5, direction="<=",
                 required=False, source="computed")
        assert d.confidence == 1.0
