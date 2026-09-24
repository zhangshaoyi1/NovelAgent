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
    # 拼接未超 10000 → 主路径（拼接评一次）；首评 59 贴线 → 复采样 65 → 逐维均值 62
    llm = _FakeLLM([_dims(_appeal(59)), _dims(_appeal(65))])
    scorer = _make_scorer(monkeypatch, llm)
    _patch_read_chapters(monkeypatch, tmp_path, texts)
    report = gate_first_chapters(scorer, tmp_path, 3, threshold=60)
    assert report.llm_used is True
    assert "复核" in report.one_liner
    assert not llm.responses, "贴线必须触发第二次采样"
    # 逐维均值：(59+65)/2 = 62
    assert report.total_score == 62
    assert set(report.dimensions) == set(APPEAL_DIMENSIONS)


def test_gate_first_chapters_fallback_uses_per_chapter_mean(monkeypatch, tmp_path) -> None:
    """超长回退：逐章须先取**多次采样均值**再参与取最差（消除单样本 min 的系统性低估）。

    旧实现直接对单次采样取 min，判定噪声 σ≈8-10 ⇒ min-of-3 低估 7-10 分，
    达标书被误熔断（灵荒工坊实证：单次 min 综合 57/爽点 38 vs 逐章均值 min ≈68/42）。
    """
    texts = ["第一章内容" * 700, "第二章内容" * 700, "第三章内容" * 700]  # 拼接 > 10000 → 回退
    responses = [
        _dims(_appeal(70)), _dims(_appeal(90)),   # 第 1 章：单次 70 会低估，均值 80
        _dims(_appeal(90)), _dims(_appeal(90)),
        _dims(_appeal(90)), _dims(_appeal(90)),
    ]
    llm = _FakeLLM(responses)
    scorer = _make_scorer(monkeypatch, llm)
    _patch_read_chapters(monkeypatch, tmp_path, texts)
    report = gate_first_chapters(scorer, tmp_path, 3, threshold=60)
    assert report.fallback is True
    assert not llm.responses, "每章应采 GOLDEN_FALLBACK_SAMPLES 次"
    assert report.dimensions["hook_strength"] == 80, report.dimensions
    assert report.total_score == 80, report.total_score


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


# ============================================================
# ★ 2026-09-25：单维触底线也要复核（此前只看综合分）
# ============================================================
def _appeal_with_payoff(total: int, payoff: int) -> str:
    """构造报告 JSON：除爽点外各维取 total，便于精确控制触底维。"""
    dims = {k: total for k in APPEAL_DIMENSIONS}
    dims["payoff_density"] = payoff
    return _dims({"dimensions": dims, "one_liner": "t", "suggestions": []})


def test_gate_first_chapters_dim_floor_triggers_second_sample(monkeypatch, tmp_path) -> None:
    """综合分远离贴线带，但**单维触底**时同样必须复核。

    旧实现只看综合分 ⇒ 单维触底（爽点 38 < 触底线 40）永不复核；而单维得分方差
    远大于总分（灵荒工坊 ch002 实测同一内容爽点 35↔58）——「1 分之差」误熔断，
    与本函数存在的理由同型（灵荒薪传 59/60）。
    """
    texts = ["第一章内容" * 200]
    llm = _FakeLLM([_appeal_with_payoff(80, 38), _appeal_with_payoff(80, 52)])
    scorer = _make_scorer(monkeypatch, llm)
    _patch_read_chapters(monkeypatch, tmp_path, texts)
    report = gate_first_chapters(scorer, tmp_path, 1, threshold=60)
    assert not llm.responses, "单维触底必须触发第二次采样（responses 应被耗尽）"
    assert "复核" in report.one_liner
    assert report.dimensions["payoff_density"] == 45, report.dimensions  # (38+52)/2


def test_gate_first_chapters_dim_far_from_floor_single_sample(monkeypatch, tmp_path) -> None:
    """各维都远离触底线（且总分远离贴线带）→ 维持单次采样，不做无谓复核。"""
    texts = ["第一章内容" * 200]
    llm = _FakeLLM([_appeal_with_payoff(80, 70)])
    scorer = _make_scorer(monkeypatch, llm)
    _patch_read_chapters(monkeypatch, tmp_path, texts)
    report = gate_first_chapters(scorer, tmp_path, 1, threshold=60)
    assert not llm.responses, "应只采一次"
    assert "复核" not in report.one_liner


def test_recheck_borderline_dim_floor_triggers(monkeypatch) -> None:
    """写时金三门禁同口径：综合分决定性（80），但单维触底（爽点 38）→ 仍复核。"""
    from agent.core.quality.scoring.reader_appeal import (
        ReaderAppealReport,
        recheck_borderline,
    )

    dims = {k: 80 for k in APPEAL_DIMENSIONS}
    dims["payoff_density"] = 38
    base = ReaderAppealReport(
        dimensions=dims, total_score=80, one_liner="t", suggestions=[],
        llm_used=True, source="llm",
    )
    llm = _FakeLLM([_appeal_with_payoff(80, 52)])
    scorer = _make_scorer(monkeypatch, llm)
    out = recheck_borderline(scorer, "正文" * 100, base)
    assert out is not None, "单维触底必须触发写时复核"
    assert out.dimensions["payoff_density"] == 45, out.dimensions


def test_gate_first_chapters_cached_borderline_gets_recheck(monkeypatch, tmp_path) -> None:
    """缓存命中且贴线（59）→ 追加一次采样取均值并刷新缓存（灵荒薪传 59/60 缓存复用二次熔断实证）。"""
    texts = ["第一章内容" * 200]
    cached_report = {
        "dimensions": {k: 59 for k in APPEAL_DIMENSIONS},
        "total_score": 59,
        "one_liner": "首评",
        "suggestions": [],
        "chapters_scored": 1,
        "fallback": False,
        "summary_lines": [],
        "llm_used": True,
        "source": "llm",
    }
    import agent.core.quality.scoring.reader_appeal as m

    saved: dict = {}

    monkeypatch.setattr(m, "list_chapter_files", lambda d: [Path("ch001.md")])
    monkeypatch.setattr(m, "read_chapters_text", lambda d, side, n: texts)
    monkeypatch.setattr(m, "_golden_fingerprint", lambda d, n: "fp-test")
    monkeypatch.setattr(m, "_load_golden_cache", lambda d, fp: m.ReaderAppealReport(**{
        "dimensions": cached_report["dimensions"], "total_score": 59,
        "one_liner": "首评", "suggestions": [], "llm_used": True,
        "source": "llm", "chapters_scored": 1, "fallback": False,
    }))
    monkeypatch.setattr(m, "_save_golden_cache",
                        lambda d, fp, r: saved.update(total=r.total_score))
    # 复核采样返回 65 → 均值 (59+65)/2 = 62
    llm = _FakeLLM([_dims(_appeal(65))])
    scorer = _make_scorer(monkeypatch, llm)
    report = gate_first_chapters(scorer, tmp_path, 1, threshold=60)
    assert report.total_score == 62
    assert "复核" in report.one_liner
    assert saved.get("total") == 62, "刷新后的缓存应写回复核均值"


def test_gate_first_chapters_cached_decisive_kept(monkeypatch, tmp_path) -> None:
    """缓存命中且远离贴线带（80）→ 维持缓存零成本，不追加采样。"""
    texts = ["第一章内容" * 200]
    import agent.core.quality.scoring.reader_appeal as m

    monkeypatch.setattr(m, "list_chapter_files", lambda d: [Path("ch001.md")])
    monkeypatch.setattr(m, "read_chapters_text", lambda d, side, n: texts)
    monkeypatch.setattr(m, "_golden_fingerprint", lambda d, n: "fp-test")
    monkeypatch.setattr(m, "_load_golden_cache", lambda d, fp: m.ReaderAppealReport(
        dimensions={k: 80 for k in APPEAL_DIMENSIONS}, total_score=80,
        one_liner="首评", suggestions=[], llm_used=True, source="llm",
        chapters_scored=1, fallback=False,
    ))
    llm = _FakeLLM([])  # 若触发采样会因 responses 为空解析失败 → 用于断言未发生
    scorer = _make_scorer(monkeypatch, llm)
    report = gate_first_chapters(scorer, tmp_path, 1, threshold=60)
    assert report.total_score == 80
    assert "复核" not in report.one_liner
