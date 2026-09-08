"""HA-Eval L5 · 可观测层（审计日志 + 回放检测）回归测试

锁死的核心不变式：
    三类事故指纹（批级串值 / 跨轮恒值 / 缓存命中）必须能被 ``detect_anomalies``
    程序化点名，把「靠人工比对 token 才能定位」变成「5 分钟内被点名」。
"""

from __future__ import annotations

from agent.core.quality.audit import (
    Anomaly,
    AuditDimension,
    AuditRecord,
    QualityAuditStore,
    detect_anomalies,
    load_records,
    record_from_report,
)


class _FakeDim:
    def __init__(self, name, value, source, unit="", confidence=1.0,
                 cache_hit=False, phash="", rhash=""):
        self.name = name
        self.value = value
        self.source = source
        self.unit = unit
        self.confidence = confidence
        self.cache_hit = cache_hit
        self.prompt_hash = phash
        self.response_hash = rhash


class _FakeSpec:
    def __init__(self, unit):
        class _U:
            value = unit
        self.unit = _U()


class _FakeReport:
    def __init__(self, dims, overall_pass=False, score=0.0):
        self.dimensions = dims
        self.overall_pass = overall_pass
        self.score = score


class _FakeEvidence:
    def __init__(self, confidence=1.0, cache_hit=False, phash="", rhash=""):
        self.confidence = confidence
        self.cache_hit = cache_hit
        self.prompt_hash = phash
        self.response_hash = rhash


def _llm_dim(name, value, unit="count"):
    d = _FakeDim(name, value, "llm/default", unit=unit)
    d.spec = _FakeSpec(unit)
    return d


class TestDetectAnomalies:
    def test_cache_collision_when_llm_values_identical_across_units(self):
        rec = AuditRecord(
            at=0.0, overall_pass=False,
            dimensions=[
                AuditDimension("character_stability_high", 3.0, "llm/default", "count"),
                AuditDimension("setting_consistency_high", 3.0, "llm/default", "count"),
                AuditDimension("logic_holes", 3.0, "llm/default", "count"),
                AuditDimension("coherence", 3.0, "llm/default", "score"),
                AuditDimension("readability", 3.0, "llm/default", "score"),
            ],
        )
        anomalies = detect_anomalies([rec])
        kinds = [a.kind for a in anomalies]
        assert "cache_collision" in kinds

    def test_no_collision_when_values_differ(self):
        rec = AuditRecord(
            at=0.0, overall_pass=True,
            dimensions=[
                AuditDimension("character_stability_high", 0.0, "llm/default", "count"),
                AuditDimension("coherence", 88.0, "llm/default", "score"),
                AuditDimension("readability", 82.0, "llm/default", "score"),
            ],
        )
        anomalies = detect_anomalies([rec])
        assert not any(a.kind == "cache_collision" for a in anomalies)

    def test_cache_hit_flagged(self):
        rec = AuditRecord(
            at=0.0, overall_pass=True,
            dimensions=[
                AuditDimension("coherence", 85.0, "llm/default", "score", cache_hit=True),
            ],
        )
        anomalies = detect_anomalies([rec])
        assert any(a.kind == "cache_hit" for a in anomalies)

    def test_stale_value_flagged_across_rounds(self):
        recs = [
            AuditRecord(at=0.0, overall_pass=False,
                        dimensions=[AuditDimension("logic_holes", 3.0, "llm/default", "count")]),
            AuditRecord(at=1.0, overall_pass=False,
                        dimensions=[AuditDimension("logic_holes", 3.0, "llm/default", "count")]),
        ]
        anomalies = detect_anomalies(recs)
        assert any(a.kind == "stale_value" for a in anomalies)

    def test_zero_value_not_flagged_as_stale(self):
        # 全 0 是硬门禁的正常通过值，不应误报恒值
        recs = [
            AuditRecord(at=0.0, overall_pass=True,
                        dimensions=[AuditDimension("logic_holes", 0.0, "llm/default", "count")]),
            AuditRecord(at=1.0, overall_pass=True,
                        dimensions=[AuditDimension("logic_holes", 0.0, "llm/default", "count")]),
        ]
        anomalies = detect_anomalies(recs)
        assert not any(a.kind == "stale_value" for a in anomalies)


class TestStore:
    def test_append_and_load_roundtrip(self, tmp_path):
        store = QualityAuditStore(tmp_path)
        store.append(AuditRecord(
            at=1.0, overall_pass=True,
            dimensions=[AuditDimension("coherence", 90.0, "llm/default", "score")],
        ))
        recs = load_records(tmp_path)
        assert len(recs) == 1
        assert recs[0].dimensions[0].name == "coherence"
        assert recs[0].dimensions[0].value == 90.0

    def test_missing_file_returns_empty(self, tmp_path):
        assert load_records(tmp_path / "nonexistent") == []


class TestRecordFromReport:
    def test_extracts_dimensions_and_evidence(self):
        d = _llm_dim("coherence", 88.0, unit="score")
        d.evidence = _FakeEvidence(cache_hit=True)
        report = _FakeReport([d], overall_pass=True, score=95.0)
        rec = record_from_report(report)
        assert rec.overall_pass is True
        assert rec.dimensions[0].name == "coherence"
        assert rec.dimensions[0].cache_hit is True
        assert rec.dimensions[0].unit == "score"
