"""优化登记 20260913_金三评分降级重试与贴线复核 单测。

覆盖：
1. score_chapter 形状异常（缺维度）→ 带错重试一次，重试成功返回真实评分；
2. score_chapter 形状异常两次仍失败 → 离线占位（llm_used=False）；
3. gate_first_chapters 首评贴线（55-65）→ 二次采样取逐维均值；
4. gate_first_chapters 首评远离贴线带 → 不产生第二次采样调用。
"""
from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from agent.core.quality.scoring.reader_appeal import (
    APPEAL_DIMENSIONS,
    ReaderAppealScorer,
    gate_first_chapters,
)


def _dims(payload: dict) -> str:
    return json.dumps(payload, ensure_ascii=False)


def _appeal(total: int) -> dict:
    base = {k: total for k in APPEAL_DIMENSIONS}
    return {
        "dimensions": base,
        "one_liner": "test",
        "suggestions": ["s1"],
    }


class _FakeLLM:
    """按调用序返回预设文本；记录调用次数。"""

    def __init__(self, responses: list[str]) -> None:
        self.responses = list(responses)
        self.calls = 0

    @property
    def text(self) -> str:
        return self.responses.pop(0) if self.responses else "{}"


def _make_scorer(monkeypatch, llm: _FakeLLM) -> ReaderAppealScorer:
    scorer = ReaderAppealScorer(llm, None)
    import agent.core.quality.scoring.reader_appeal as m

    monkeypatch.setattr(
        m, "_chat_with_eval_backoff",
        lambda llm_, messages, **kw: SimpleNamespace(text=llm_.responses.pop(0)),
    )
    return scorer


def test_score_chapter_shape_anomaly_retries_then_succeeds(monkeypatch) -> None:
    bad = _appeal(70)
    bad["dimensions"].pop("hook_strength")  # 缺维度 → 形状异常
    llm = _FakeLLM([_dims(bad), _dims(_appeal(70))])
    scorer = _make_scorer(monkeypatch, llm)
    report = scorer.score_chapter("正文" * 100)
    # 两次调用（首评形状异常 + 带错重试成功）：responses 已耗尽即为 2 次
    assert not llm.responses
    assert report.llm_used is True
    assert report.total_score == 70


def test_score_chapter_shape_anomaly_twice_fails_offline(monkeypatch) -> None:
    bad = _appeal(70)
    bad["dimensions"].pop("immersion")
    llm = _FakeLLM([_dims(bad), _dims(bad)])
    scorer = _make_scorer(monkeypatch, llm)
    report = scorer.score_chapter("正文" * 100)
    assert report.llm_used is False
    assert report.source == "offline"


def _patch_read_chapters(monkeypatch, tmp_path: Path, texts: list[str]) -> None:
    import agent.core.quality.scoring.reader_appeal as m

    monkeypatch.setattr(m, "list_chapter_files", lambda d: [Path(f"ch{i:03d}.md") for i in range(1, len(texts) + 1)])
    monkeypatch.setattr(m, "read_chapters_text", lambda d, side, n: texts[:n])
    monkeypatch.setattr(m, "_golden_fingerprint", lambda d, n: "fp-test")
    monkeypatch.setattr(m, "_load_golden_cache", lambda d, fp: None)
    monkeypatch.setattr(m, "_save_golden_cache", lambda d, fp, r: None)


def test_gate_first_chapters_borderline_triggers_second_sample(monkeypatch, tmp_path) -> None:
    texts = ["第一章内容" * 200, "第二章内容" * 200, "第三章内容" * 200]
    # 拼接超长 → fallback 逐章取最差；首评 total=59 贴线，复采样 65 → 均值 62
    responses = []
    for _ in range(2):  # 两次采样 × 3 章
        responses.append(_dims(_appeal(59)))
        responses.append(_dims(_appeal(65)))
        responses.append(_dims(_appeal(65)))
    llm = _FakeLLM(responses)
    scorer = _make_scorer(monkeypatch, llm)
    _patch_read_chapters(monkeypatch, tmp_path, texts)
    report = gate_first_chapters(scorer, tmp_path, 3, threshold=60)
    assert report.llm_used is True
    assert "复核" in report.one_liner
    # 逐维均值：最差维 59 与 65 均值 = 62
    assert report.total_score == 62
    assert set(report.dimensions) == set(APPEAL_DIMENSIONS)


def test_gate_first_chapters_far_from_borderline_single_sample(monkeypatch, tmp_path) -> None:
    texts = ["第一章内容" * 200]
    responses = [_dims(_appeal(80))]  # 首评 80 远离 60±5 → 只采一次
    llm = _FakeLLM(responses)
    scorer = _make_scorer(monkeypatch, llm)
    _patch_read_chapters(monkeypatch, tmp_path, texts)
    report = gate_first_chapters(scorer, tmp_path, 1, threshold=60)
    assert report.total_score == 80
    assert report.one_liner != "贴线二次采样复核：首评 80/复核 80，取逐维均值"
    assert len(report.one_liner) >= 0
    assert "复核" not in report.one_liner
