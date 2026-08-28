"""T-7 LLMProvider 注册表单元测试

覆盖：
- 注册新 provider 后 create 返回其实例（单一注册点，禁 if/else）
- 未知 provider 抛 LLMError 且给出可用 provider 列表
- LLMConfig.fallback_providers 列表归一化（字符串/旧字段兼容）
- fallback_providers 回退链：遍历列表、跳过不可用项、chat 自动回退
"""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from agent.core.base.exceptions import LLMError
from agent.client import (
    LLMClient,
    LLMConfig,
    LLMProvider,
    OllamaProvider,
    OpenAIProvider,
)


class _FakeProvider(LLMProvider):
    """测试用假 Provider（仅用于注册表验证）"""

    def __init__(self, config: LLMConfig) -> None:
        self.config = config

    def chat(self, messages, model, temperature, max_tokens, enable_thinking, timeout, **kwargs):  # noqa: ANN001
        raise NotImplementedError


class _PrimaryFake(LLMProvider):
    """可指定抛错的主 Provider"""

    def __init__(self, config: LLMConfig, raise_exc: Exception | None = None) -> None:
        self.config = config
        self.raise_exc = raise_exc
        self.calls = 0

    def chat(self, messages, model, temperature, max_tokens, enable_thinking, timeout, **kwargs):  # noqa: ANN001
        self.calls += 1
        if self.raise_exc is not None:
            raise self.raise_exc
        from agent.client import LLMResponse

        return LLMResponse(text="primary-ok", model=model, raw={})


class _FallbackFake(LLMProvider):
    """可指定返回文本的备用 Provider"""

    def __init__(self, config: LLMConfig, response_text: str = "fallback-ok") -> None:
        self.config = config
        self.response_text = response_text
        self.calls = 0

    def chat(self, messages, model, temperature, max_tokens, enable_thinking, timeout, **kwargs):  # noqa: ANN001
        self.calls += 1
        from agent.client import LLMResponse

        return LLMResponse(text=self.response_text, model=model, raw={})


def test_registry_create_returns_instance() -> None:
    """注册新 provider 后 create 返回其实例"""
    LLMProvider.PROVIDERS["fakep"] = _FakeProvider
    try:
        provider = LLMProvider.create(LLMConfig(provider="fakep"))
        assert isinstance(provider, _FakeProvider)
    finally:
        LLMProvider.PROVIDERS.pop("fakep", None)


def test_create_builtins() -> None:
    """内置 provider 经注册表正常创建"""
    assert isinstance(LLMProvider.create(LLMConfig(provider="openai")), OpenAIProvider)
    assert isinstance(LLMProvider.create(LLMConfig(provider="ollama")), OllamaProvider)


def test_create_unknown_raises_llmerror() -> None:
    """未知 provider 抛 LLMError 且给出可用列表"""
    with pytest.raises(LLMError) as exc:
        LLMProvider.create(LLMConfig(provider="nope"))
    assert "nope" in str(exc.value)
    assert "openai" in str(exc.value)


def test_fallback_providers_string_normalized() -> None:
    """fallback_providers 接受逗号分隔字符串，归一化为列表"""
    cfg = LLMConfig(provider="openai", fallback_providers="ollama, other")
    assert cfg.fallback_providers == ["ollama", "other"]


def test_fallback_provider_legacy_string_compat() -> None:
    """旧字段 fallback_provider（字符串）兼容，自动并入 fallback_providers"""
    cfg = LLMConfig(provider="openai", fallback_provider="ollama")
    assert cfg.fallback_providers == ["ollama"]


def test_fallback_providers_chain_skips_unavailable() -> None:
    """fallback_providers 列表中不可用项被跳过，返回首个可用项"""
    client = LLMClient(
        config=LLMConfig(provider="openai", fallback_providers=["unknown_p", "ollama"]),
    )
    fb = client._get_fallback_provider()
    assert isinstance(fb, OllamaProvider)


def test_fallback_chain_used_in_chat() -> None:
    """主 Provider 网络错误 + 配置回退链 → 自动回退成功"""
    primary = _PrimaryFake(LLMConfig(provider="openai"), raise_exc=ConnectionError("refused"))
    fallback = _FallbackFake(LLMConfig(provider="ollama"), response_text="from-fallback")
    client = LLMClient(
        config=LLMConfig(provider="openai", fallback_providers=["ollama"]),
        primary_provider=primary,
        fallback_provider=fallback,
    )
    resp = client.chat([{"role": "user", "content": "hi"}])
    assert resp.text == "from-fallback"
    assert primary.calls == 1
    assert fallback.calls == 1


def test_preflight_reports_fallback_list() -> None:
    """preflight 同时报告 fallback_provider（兼容）与 fallback_providers（列表）"""
    primary = _PrimaryFake(LLMConfig(provider="openai"))
    fallback = _FallbackFake(LLMConfig(provider="ollama"))
    client = LLMClient(
        config=LLMConfig(provider="openai", fallback_provider="ollama"),
        primary_provider=primary,
        fallback_provider=fallback,
    )
    info = client.preflight()
    assert info["fallback_provider"] == "ollama"
    assert info["fallback_providers"] == ["ollama"]
    assert info["has_fallback"] is True
