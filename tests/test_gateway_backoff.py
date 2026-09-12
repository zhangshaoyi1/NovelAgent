"""网关层瞬时故障指数退避测试（2026-09-08 收口点加固）。

背景：20:58 轮 autowrite 10 次 LLM 调用失败（单次延迟 34s），上层"立即重试一次"
在故障窗口内空转。修复：唯一收口点 _GatewayModelProvider.complete() 内做
指数退避（默认 3 次尝试，2s/4s），致命错误（配额/鉴权）立即熔断。
"""

from __future__ import annotations

from typing import Any

import pytest

import agent.client.gateway_adapter as gw
from agent.base.llm import FatalProviderError


class _FakeProvider:
    """最小 LLMProvider 替身：按脚本依次抛错/返回。"""

    def __init__(self, script: list[Any]) -> None:
        self.script = list(script)
        self.calls = 0
        self.config = type("C", (), {"timeout": 5, "enable_thinking": None})()

    def chat(self, **kwargs: Any) -> Any:
        self.calls += 1
        item = self.script.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


class _FakePacked:
    def __init__(self, messages: list, route: Any) -> None:
        self.messages = messages
        self.route = route
        self.temperature = None
        self.enable_thinking = None
        self.estimated_input_tokens = 10


class _FakeRoute:
    def __init__(self) -> None:
        self.model = "test-model"
        self.card = type("Card", (), {"temperature": 0.7})()


def _make_provider(script: list[Any]) -> tuple[gw._GatewayModelProvider, _FakeProvider]:
    fake = _FakeProvider(script)
    return gw._GatewayModelProvider("fake", fake), fake


@pytest.fixture(autouse=True)
def _no_real_sleep(monkeypatch: pytest.MonkeyPatch) -> list[float]:
    sleeps: list[float] = []
    monkeypatch.setattr(gw.time, "sleep", lambda s: sleeps.append(s))
    return sleeps


def _run(provider: gw._GatewayModelProvider) -> Any:
    packed = _FakePacked([{"role": "user", "content": "hi"}], _FakeRoute())
    return provider.complete(packed)


def test_transient_then_success_retries_with_backoff(
    _no_real_sleep: list[float],
) -> None:
    provider, fake = _make_provider(
        [
            ConnectionError("conn reset"),
            TimeoutError("read timed out"),
            type("R", (), {"text": "ok", "usage": {}})(),
        ]
    )
    resp = _run(provider)
    assert resp.text == "ok"
    assert fake.calls == 3
    # 指数退避：第 1 次失败等 2s，第 2 次等 4s
    assert _no_real_sleep == [2.0, 4.0]


def test_fatal_error_circuit_breaks_immediately(
    _no_real_sleep: list[float],
) -> None:
    provider, fake = _make_provider([RuntimeError("Error code: 403 - free quota exhausted")])
    with pytest.raises(FatalProviderError):
        _run(provider)
    assert fake.calls == 1
    assert _no_real_sleep == []


def test_non_transient_error_raises_without_retry(
    _no_real_sleep: list[float],
) -> None:
    provider, fake = _make_provider([ValueError("Error code: 400 - invalid request")])
    with pytest.raises(RuntimeError):
        _run(provider)
    assert fake.calls == 1
    assert _no_real_sleep == []


def test_transient_exhausted_raises_after_all_attempts(
    _no_real_sleep: list[float],
) -> None:
    provider, fake = _make_provider([TimeoutError("timed out")] * gw._TRANSIENT_MAX_ATTEMPTS)
    with pytest.raises(RuntimeError):
        _run(provider)
    assert fake.calls == gw._TRANSIENT_MAX_ATTEMPTS
    # n 次尝试 → n-1 次退避
    assert len(_no_real_sleep) == gw._TRANSIENT_MAX_ATTEMPTS - 1


def test_env_override_attempts(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(gw, "_TRANSIENT_MAX_ATTEMPTS", 2)
    provider, fake = _make_provider([TimeoutError("timed out")] * 5)
    with pytest.raises(RuntimeError):
        _run(provider)
    assert fake.calls == 2


def test_hard_deadline_hung_request_retries(monkeypatch: pytest.MonkeyPatch) -> None:
    """挂起式请求（永不返回）→ 硬死线触发 → 按瞬时故障退避重试。

    背景（2026-09-12 五灵破归档 ch190）：SDK timeout 对"连接保活但永不回包"
    无效，请求 4.5h 无返回也无异常，流水线卡死。
    """
    monkeypatch.setenv("LLM_HARD_DEADLINE_S", "0.2")
    real_sleep = gw.time.sleep  # 硬死线用真实 join，退避 sleep 仍走 fixture 拦截
    import threading

    calls = {"n": 0}

    def _hang(**kwargs: Any) -> Any:
        calls["n"] += 1
        if calls["n"] == 1:
            threading.Event().wait(30)  # 模拟挂起，长于 0.2s 死线
        return type("R", (), {"text": "ok", "usage": {}})()

    provider, fake = _make_provider([])
    provider._provider.chat = _hang  # type: ignore[method-assign]
    resp = _run(provider)
    assert resp.text == "ok"
    assert calls["n"] == 2


def test_hard_deadline_env_zero_uses_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LLM_HARD_DEADLINE_S", "0")
    assert gw._hard_deadline_s(300) == 1200.0
    monkeypatch.setenv("LLM_HARD_DEADLINE_S", "600")
    assert gw._hard_deadline_s(300) == 600.0
