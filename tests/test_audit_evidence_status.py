"""A1 红线：审计「无证据 ≠ 通过」（纪律 #1 / D1 同范式）。

起因（真实事故）：上游网关间歇 404 ⇒ 多维 ``confidence=0``，而
``.state/quality_audit.jsonl`` 仍写出 ``overall_pass=True score=100``
——**审计记录本身误导**（gate 有 ``recheck`` 兜住，但读数是假的）。
代码级根因：``record_from_report`` 曾用 ``getattr(report, "overall_pass", True)``，
**缺省即通过**。

锁死的不变式：
    ① 证据不可信（``no_data``）或报告未给 ⇒ ``overall_pass is None``（未知）；
    ② ``evidence_status == "no_data"`` ⇔ ``overall_pass is None``（写入与载入同构）；
    ③ 报告明确给 ``True`` 且有可信证据 ⇒ **保持 True**（不制造假失败）；
    ④ ``to_dict`` 只增不删。
"""

from __future__ import annotations

import json
from pathlib import Path

from agent.core.quality.audit import (
    EVIDENCE_NO_DATA,
    EVIDENCE_OK,
    AuditDimension,
    AuditRecord,
    QualityAuditStore,
    derive_evidence_status,
    load_records,
    record_from_report,
)


class _FakeDim:
    def __init__(self, name, value, source="llm/default", unit="score",
                 confidence=1.0, evidence=None):
        self.name = name
        self.value = value
        self.source = source
        self.unit = unit
        self.confidence = confidence
        self.evidence = evidence

        class _U:
            value = unit

        class _Spec:
            pass

        spec = _Spec()
        spec.unit = _U()
        self.spec = spec


class _FakeEvidence:
    def __init__(self, confidence=1.0):
        self.confidence = confidence
        self.cache_hit = False
        self.prompt_hash = ""
        self.response_hash = ""


class _FakeReport:
    def __init__(self, dims, score=0.0, overall_pass=True, with_pass=True):
        self.dimensions = dims
        self.score = score
        if with_pass:
            self.overall_pass = overall_pass


class TestDeriveEvidenceStatus:
    def test_empty_dims_is_no_data(self) -> None:
        assert derive_evidence_status([]) == EVIDENCE_NO_DATA

    def test_all_zero_confidence_is_no_data(self) -> None:
        dims = [AuditDimension("a", 0.0, confidence=0.0),
                AuditDimension("b", 100.0, confidence=0.0)]
        assert derive_evidence_status(dims) == EVIDENCE_NO_DATA

    def test_one_trustworthy_dim_is_ok(self) -> None:
        dims = [AuditDimension("a", 0.0, confidence=0.0),
                AuditDimension("b", 80.0, confidence=1.0)]
        assert derive_evidence_status(dims) == EVIDENCE_OK


class TestRecordFromReport:
    def test_no_evidence_never_records_pass(self) -> None:
        """R1：全网关 404 形态 ⇒ 不得写成通过。"""
        dims = [
            _FakeDim("coherence", 100.0, confidence=0.0, evidence=_FakeEvidence(0.0)),
            _FakeDim("pacing", 100.0, confidence=0.0, evidence=_FakeEvidence(0.0)),
        ]
        rec = record_from_report(_FakeReport(dims, score=100.0, overall_pass=True))
        assert rec.overall_pass is None, "无证据却记成了明确结论"
        assert rec.evidence_status == EVIDENCE_NO_DATA

    def test_missing_overall_pass_is_unknown_not_true(self) -> None:
        """R2：报告没有 overall_pass 属性 ⇒ None，绝不默认 True。"""
        dims = [_FakeDim("coherence", 88.0)]
        rec = record_from_report(_FakeReport(dims, with_pass=False))
        assert rec.overall_pass is None

    def test_trustworthy_pass_is_preserved(self) -> None:
        """R3：有证据 + 报告给 True ⇒ 保持 True（不制造假失败）。"""
        dims = [_FakeDim("coherence", 88.0, confidence=1.0,
                         evidence=_FakeEvidence(1.0))]
        rec = record_from_report(_FakeReport(dims, score=95.0, overall_pass=True))
        assert rec.overall_pass is True
        assert rec.evidence_status == EVIDENCE_OK

    def test_trustworthy_fail_is_preserved(self) -> None:
        dims = [_FakeDim("coherence", 40.0, confidence=1.0,
                         evidence=_FakeEvidence(1.0))]
        rec = record_from_report(_FakeReport(dims, overall_pass=False))
        assert rec.overall_pass is False


class TestPersistence:
    def test_no_data_row_persists_null_and_status(self, tmp_path: Path) -> None:
        """R4：落盘的 no_data 行必须是 ``overall_pass: null`` + 带状态字段。"""
        dims = [_FakeDim("coherence", 100.0, confidence=0.0,
                         evidence=_FakeEvidence(0.0))]
        QualityAuditStore(tmp_path).append(
            record_from_report(_FakeReport(dims, score=100.0, overall_pass=True))
        )
        raw = json.loads(
            (tmp_path / ".state" / "quality_audit.jsonl").read_text("utf-8").strip()
        )
        assert raw["overall_pass"] is None
        assert raw["evidence_status"] == EVIDENCE_NO_DATA

    def test_legacy_row_without_status_is_recomputed(self, tmp_path: Path) -> None:
        """R5：旧行（无 evidence_status、confidence=0）载入后不得读成 ok/True。"""
        f = tmp_path / ".state" / "quality_audit.jsonl"
        f.parent.mkdir(parents=True)
        f.write_text(json.dumps({
            "at": 1.0,
            "overall_pass": True,
            "score": 100.0,
            "dimensions": [
                {"name": "coherence", "value": 100.0, "source": "llm/default",
                 "unit": "score", "confidence": 0.0},
            ],
        }, ensure_ascii=False) + "\n", encoding="utf-8")
        recs = load_records(tmp_path)
        assert len(recs) == 1
        assert recs[0].evidence_status == EVIDENCE_NO_DATA
        assert recs[0].overall_pass is None

    def test_legacy_row_without_pass_key_is_unknown(self, tmp_path: Path) -> None:
        f = tmp_path / ".state" / "quality_audit.jsonl"
        f.parent.mkdir(parents=True)
        f.write_text(json.dumps({
            "at": 1.0, "score": 90.0,
            "dimensions": [{"name": "x", "value": 90.0, "source": "llm/default",
                            "unit": "score", "confidence": 1.0}],
        }, ensure_ascii=False) + "\n", encoding="utf-8")
        recs = load_records(tmp_path)
        assert recs[0].overall_pass is None
        assert recs[0].evidence_status == EVIDENCE_OK


class TestToDictContract:
    def test_keys_only_added(self) -> None:
        """R6：to_dict 只增不删 —— 历史键一个不少，新增 evidence_status。"""
        rec = AuditRecord(at=1.0, overall_pass=True, dimensions=[],
                          evidence_status=EVIDENCE_OK)
        d = rec.to_dict()
        for legacy in ("at", "overall_pass", "score", "dimensions"):
            assert legacy in d, f"历史键 {legacy} 被删"
        assert d["evidence_status"] == EVIDENCE_OK

    def test_roundtrip_preserves_status(self, tmp_path: Path) -> None:
        rec = AuditRecord(at=2.0, overall_pass=False,
                          dimensions=[AuditDimension("x", 10.0, confidence=1.0)],
                          evidence_status=EVIDENCE_OK)
        QualityAuditStore(tmp_path).append(rec)
        got = load_records(tmp_path)[0]
        assert got.overall_pass is False
        assert got.evidence_status == EVIDENCE_OK
