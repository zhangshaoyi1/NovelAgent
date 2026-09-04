"""E1 本地模型支持 - 无网络回退（F-E1.4）单元测试

覆盖：
- 主 Provider 网络错误 + 已配置备用 Provider → 自动回退成功
- 主 Provider 网络错误 + 未配置备用 → 抛 LLMError
- 主 Provider 非网络错误 → 不触发回退
- 主 Provider 正常 → 直接返回，不触碰备用
- 备用 Provider 也失败 → 抛 LLMError（last_exc 为回退异常）
- _is_network_error 对 openai 风格 *ConnectionError 名称匹配
"""

from __future__ import annotations

import pytest
pytestmark = pytest.mark.skip(reason="LLMClient has been deprecated, use gateway_adapter instead")

from unittest.mock import MagicMock

from agent.base.llm import LLMError
from agent.client import (
    LLMClient,
    LLMConfig,
    LLMProvider,
    LLMResponse,
    URLError,
)


class _FakeProvider(LLMProvider):
    """测试用假 Provider，可指定抛错或返回文本"""

    def __init__(self, raise_exc: Exception | None = None, response_text: str = "ok") -> None:
        self.config = LLMConfig(provider="fake", model="fake-model")
        self.raise_exc = raise_exc
        self.response_text = response_text
        self.calls = 0

    def chat(self, messages, model, temperature, max_tokens, enable_thinking, timeout, **kwargs):  # noqa: ANN001
        self.calls += 1
        if self.raise_exc is not None:
            raise self.raise_exc
        return LLMResponse(text=self.response_text, model=model, raw={})


class _OpenAIStyleConnectionError(Exception):
    """模拟 openai.APIConnectionError（类名含 Connection，但不继承内置异常）"""



def _client(primary: _FakeProvider, fallback: _FakeProvider | None = None) -> LLMClient:
    return LLMClient(
        config=LLMConfig(provider="openai", fallback_provider="ollama" if fallback else ""),
        primary_provider=primary,
        fallback_provider=fallback,
    )


def test_fallback_on_network_error() -> None:
    primary = _FakeProvider(raise_exc=ConnectionError("refused"))
    fallback = _FakeProvider(response_text="from-ollama")
    client = _client(primary, fallback)

    resp = client.chat([{"role": "user", "content": "hi"}])

    assert resp.text == "from-ollama"
    assert primary.calls == 1
    assert fallback.calls == 1
    assert len(client.fallback_log) == 1


def test_urllib_error_triggers_fallback() -> None:
    primary = _FakeProvider(raise_exc=URLError("unreachable"))
    fallback = _FakeProvider(response_text="recovered")
    client = _client(primary, fallback)

    resp = client.chat([{"role": "user", "content": "x"}])

    assert resp.text == "recovered"


def test_no_fallback_configured_raises() -> None:
    primary = _FakeProvider(raise_exc=ConnectionError("down"))
    client = _client(primary, fallback=None)

    try:
        client.chat([{"role": "user", "content": "x"}])
        assert False, "expected LLMError"
    except LLMError:
        pass
    assert primary.calls == 1


def test_non_network_error_does_not_fallback() -> None:
    # ValueError 不是网络错误，不应回退，也不应被重试耗尽掩盖
    primary = _FakeProvider(raise_exc=ValueError("bad json"))
    fallback = _FakeProvider(response_text="should-not-be-used")
    client = _client(primary, fallback)

    try:
        client.chat([{"role": "user", "content": "x"}])
        assert False, "expected LLMError"
    except LLMError as e:
        assert "bad json" in str(e)

    assert fallback.calls == 0
    assert len(client.fallback_log) == 0


def test_primary_success_no_fallback() -> None:
    primary = _FakeProvider(response_text="primary-ok")
    fallback = _FakeProvider(response_text="unused")
    client = _client(primary, fallback)

    resp = client.chat([{"role": "user", "content": "x"}])

    assert resp.text == "primary-ok"
    assert fallback.calls == 0


def test_fallback_also_fails_raises() -> None:
    primary = _FakeProvider(raise_exc=ConnectionError("down"))
    fallback = _FakeProvider(raise_exc=ConnectionError("also-down"))
    client = _client(primary, fallback)

    try:
        client.chat([{"role": "user", "content": "x"}])
        assert False, "expected LLMError"
    except LLMError:
        pass
    assert fallback.calls == 1


def test_is_network_error_openai_style() -> None:
    assert LLMClient._is_network_error(_OpenAIStyleConnectionError()) is True
    assert LLMClient._is_network_error(TimeoutError("t")) is True
    assert LLMClient._is_network_error(ValueError("x")) is False
    assert LLMClient._is_network_error(RuntimeError("x")) is False


def test_fallback_log_recorded_with_console() -> None:
    console = MagicMock()
    primary = _FakeProvider(raise_exc=ConnectionError("down"))
    fallback = _FakeProvider(response_text="recovered")
    fallback.config = LLMConfig(provider="ollama", model="ollama-model")
    client = LLMClient(
        config=LLMConfig(provider="openai", fallback_provider="ollama"),
        console=console,
        primary_provider=primary,
        fallback_provider=fallback,
    )
    client.chat([{"role": "user", "content": "x"}])
    console.print.assert_called_once()
    assert "ollama" in client.fallback_log[0]


def test_preflight_reports_fallback() -> None:
    primary = _FakeProvider()
    fallback = _FakeProvider()
    client = _client(primary, fallback)
    info = client.preflight()
    assert info["provider"] == "openai"
    assert info["fallback_provider"] == "ollama"
    assert info["has_fallback"] is True
