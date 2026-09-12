"""评分调用长退避重试测试（2026-09-12 五灵破归档 ch190 前夜根因加固）。

背景：网关收口点瞬时故障退避只撑 ~15s 故障窗，网关故障实测可达数分钟；
退避耗尽 → 维度"评分降级为默认"污染体检分 → 假性不达标 → 无谓回滚。
修复：reader_appeal.score() 的评分调用对瞬时故障再加 20s/40s 长退避。
"""

from __future__ import annotations

from typing import Any

import agent.core.quality.scoring.reader_appeal as ra


def test_transient_failure_retries_with_long_backoff(monkeypatch) -> None:
    monkeypatch.setattr(ra, "_EVAL_RETRY_DELAYS_S", (0.0, 0.0))
    calls = {"n": 0}

    def _fake_chat(llm: Any, messages: list, **kwargs: Any) -> Any:
        calls["n"] += 1
        if calls["n"] < 3:
            raise ConnectionError("connection reset")
        return type("R", (), {"text": '{"value": 90}', "cache_hit": False, "model": "m", "elapsed_ms": 1.0})()

    monkeypatch.setattr(ra, "chat_utility_response", _fake_chat)
    resp = ra._chat_with_eval_backoff(None, [{"role": "user", "content": "x"}], dimension="coherence", console=None)
    assert resp.text == '{"value": 90}'
    assert calls["n"] == 3


def test_non_transient_failure_raises_immediately(monkeypatch) -> None:
    monkeypatch.setattr(ra, "_EVAL_RETRY_DELAYS_S", (0.0, 0.0))
    calls = {"n": 0}

    def _fake_chat(llm: Any, messages: list, **kwargs: Any) -> Any:
        calls["n"] += 1
        raise ValueError("Error code: 400 - invalid request")

    monkeypatch.setattr(ra, "chat_utility_response", _fake_chat)
    try:
        ra._chat_with_eval_backoff(None, [], dimension="coherence", console=None)
        raise AssertionError("should raise")
    except ValueError:
        pass
    assert calls["n"] == 1


def test_transient_exhausted_raises_after_all_retries(monkeypatch) -> None:
    monkeypatch.setattr(ra, "_EVAL_RETRY_DELAYS_S", (0.0, 0.0))
    calls = {"n": 0}

    def _fake_chat(llm: Any, messages: list, **kwargs: Any) -> Any:
        calls["n"] += 1
        raise TimeoutError("read timed out")

    monkeypatch.setattr(ra, "chat_utility_response", _fake_chat)
    try:
        ra._chat_with_eval_backoff(None, [], dimension="coherence", console=None)
        raise AssertionError("should raise")
    except TimeoutError:
        pass
    assert calls["n"] == 3
