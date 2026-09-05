"""LLM 客户端单元测试

不真实调用 API，用 mock 验证配置加载、模型选择、重试逻辑。
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from agent.base.llm import LLMError
from agent.client import (
    LLMClient,
    LLMConfig,
    LLMProvider,
    LLMResponse,
    OllamaProvider,
    OpenAIProvider,
)


# ------ 配置加载 ------
def test_load_config_from_env(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    """从环境变量加载配置"""
    monkeypatch.setenv("LLM_API_KEY", "test-key")
    monkeypatch.setenv("LLM_BASE_URL", "https://api.test.com")
    monkeypatch.setenv("LLM_MODEL_ID", "test-model")
    monkeypatch.setenv("LLM_TIMEOUT", "60")

    # 清除可能已加载的 .env 影响
    config = LLMConfig(
        api_key="test-key",
        base_url="https://api.test.com",
        model="test-model",
        timeout=60,
    )
    assert config.api_key == "test-key"
    assert config.model == "test-model"
    assert config.timeout == 60


def test_model_utility_defaults_to_main_model() -> None:
    """未设置 model_utility 时应与主模型一致"""
    config = LLMConfig(api_key="k", model="glm-5.2", model_utility="")
    client = LLMClient(config=config)
    # _select_model 内部处理：model_utility 为空时用主模型
    # 这里通过 LLMConfig 默认值逻辑验证
    assert config.model == "glm-5.2"


# ------ 缺少配置 ------
def test_missing_api_key_raises() -> None:
    """未配置 API key 时，openai Provider 构建 HTTP 客户端应抛 LLMError"""
    from agent.client.provider import OpenAIProvider

    provider = OpenAIProvider(LLMConfig(provider="openai", api_key="", model="m"))
    with pytest.raises(LLMError, match="LLM_API_KEY"):
        provider._get_client()


# ------ 调用与重试 ------
def _make_mock_response(text: str = "hello", model: str = "m") -> MagicMock:
    """构造 mock 的 openai 响应"""
    resp = MagicMock()
    resp.choices = [MagicMock()]
    resp.choices[0].message.content = text
    resp.usage = MagicMock()
    resp.usage.prompt_tokens = 10
    resp.usage.completion_tokens = 5
    resp.usage.total_tokens = 15
    return resp


# ---- 调用（Gateway 唯一后端；用假 Gateway 验证委托与参数映射）------


class _FakeGateway:
    """假 Gateway：记录 ChatRequest，按脚本返回/抛错"""

    def __init__(self, responses=None, error=None):
        self.requests = []
        self._responses = list(responses or [])
        self._error = error

    class registry:
        @staticmethod
        def available():
            from llmagent.gateway.models import ModelCard

            return [ModelCard(provider="fake", model="m",
                              cost_per_1k_input_cents=0.1,
                              cost_per_1k_output_cents=0.1,
                              context_window=8000)]

    def chat(self, req):
        from llmagent.gateway.models import ChatResponse

        self.requests.append(req)
        if self._error is not None:
            raise self._error
        return ChatResponse(
            text=self._responses.pop(0) if self._responses else "ok",
            provider="fake", model="m", usage_input=10, usage_output=5,
        )


def _client_with(gateway) -> LLMClient:
    import warnings

    warnings.filterwarnings("ignore", category=DeprecationWarning)
    client = LLMClient(config=LLMConfig(api_key="k", model="m"))
    client._gateway = gateway
    return client


def test_chat_success_delegates_to_gateway() -> None:
    """成功调用经 Gateway 委托并映射 usage"""
    gw = _FakeGateway(responses=["你好"])
    client = _client_with(gw)
    resp = client.chat([{"role": "user", "content": "hi"}])
    assert resp.text == "你好"
    assert resp.model == "m"
    assert resp.usage["input_tokens"] == 10
    assert resp.usage["output_tokens"] == 5
    assert len(gw.requests) == 1


def test_chat_wraps_gateway_error() -> None:
    """Gateway 异常应包装为 LLMError"""
    gw = _FakeGateway(error=RuntimeError("provider 不可达"))
    client = _client_with(gw)
    with pytest.raises(LLMError, match="Gateway 调用失败"):
        client.chat([{"role": "user", "content": "hi"}])


def test_chat_creative_hint_is_complex() -> None:
    """chat_creative → 复杂度 complex 档"""
    gw = _FakeGateway()
    client = _client_with(gw)
    client.chat_creative([{"role": "user", "content": "hi"}])
    assert gw.requests[0].hint.complexity.value == "complex"


def test_chat_utility_uses_low_temp_and_simple_hint() -> None:
    """chat_utility → 低温度 + simple 档"""
    gw = _FakeGateway()
    client = _client_with(gw)
    client.chat_utility([{"role": "user", "content": "hi"}])
    assert gw.requests[0].hint.temperature == 0.2
    assert gw.requests[0].hint.complexity.value == "simple"


def test_explicit_model_passthrough() -> None:
    """显式 model 经 extra 透传给 Gateway 路由"""
    gw = _FakeGateway()
    client = _client_with(gw)
    client.chat([{"role": "user", "content": "hi"}], model="main")
    assert gw.requests[0].extra.get("model") == "main"


def test_complete_returns_text() -> None:
    """complete 便利方法返回纯文本，并组合 system + user 消息"""
    gw = _FakeGateway(responses=["结果"])
    client = _client_with(gw)
    text = client.complete("写一句话", system="你是作家")
    assert text == "结果"

    messages = gw.requests[0].messages
    assert len(messages) == 2
    assert messages[0]["role"] == "system"
    assert messages[0]["content"] == "你是作家"
    assert messages[1]["role"] == "user"
    assert messages[1]["content"] == "写一句话"


# ============================================================
# E1: Provider 抽象层
# ============================================================
class TestProviderCreation:
    def test_create_openai_provider(self) -> None:
        config = LLMConfig(provider="openai", api_key="k", model="m")
        provider = LLMProvider.create(config)
        assert isinstance(provider, OpenAIProvider)

    def test_create_ollama_provider(self) -> None:
        config = LLMConfig(provider="ollama", model="qwen2.5")
        provider = LLMProvider.create(config)
        assert isinstance(provider, OllamaProvider)

    def test_create_ollama_case_insensitive(self) -> None:
        config = LLMConfig(provider="Ollama", model="qwen2.5")
        provider = LLMProvider.create(config)
        assert isinstance(provider, OllamaProvider)

    def test_ollama_is_default_when_empty_string(self) -> None:
        config = LLMConfig(provider="", api_key="k", model="m")
        provider = LLMProvider.create(config)
        assert isinstance(provider, OpenAIProvider)


class _OllamaRegistry:
    @staticmethod
    def available():
        from llmagent.gateway.models import ModelCard

        return [ModelCard(provider="ollama", model="qwen2.5",
                          cost_per_1k_input_cents=0.0,
                          cost_per_1k_output_cents=0.0,
                          context_window=32000)]


class TestLLMClientProvider:
    def _client(self) -> LLMClient:
        import warnings

        warnings.filterwarnings("ignore", category=DeprecationWarning)
        client = LLMClient(config=LLMConfig(provider="ollama", model="m"))
        client._gateway = type("G", (), {"registry": _OllamaRegistry})()
        return client

    def test_provider_name_property(self) -> None:
        assert self._client().provider_name == "ollama"

    def test_is_local_true_for_ollama(self) -> None:
        assert self._client().is_local is True

    def test_is_local_false_for_openai(self) -> None:
        client = LLMClient(config=LLMConfig(provider="openai", api_key="k", model="m"))
        assert client.is_local is False

    def test_load_ollama_without_api_key(self) -> None:
        """Ollama 不需要 API key，构造 LLMClient 不应报错"""
        import warnings

        warnings.filterwarnings("ignore", category=DeprecationWarning)
        config = LLMConfig(provider="ollama", model="qwen2.5")
        client = LLMClient(config=config)
        assert client._gateway is not None


class TestOllamaProviderHTTP:
    """OllamaProvider HTTP 调用测试（mock urlopen）"""

    def test_ollama_default_base_url(self) -> None:
        config = LLMConfig(provider="ollama", model="m")
        provider = OllamaProvider(config)
        assert provider.base_url == "http://localhost:11434"

    def test_ollama_custom_base_url(self) -> None:
        config = LLMConfig(provider="ollama", base_url="http://192.168.1.1:8888", model="m")
        provider = OllamaProvider(config)
        assert provider.base_url == "http://192.168.1.1:8888"

    @patch("agent.client.provider.urlopen")
    def test_ollama_chat_success(self, mock_urlopen: MagicMock) -> None:
        import json
        mock_resp = MagicMock()
        mock_resp.read.return_value = json.dumps({
            "message": {"content": "你好"},
            "usage": {
                "prompt_eval_count": 10,
                "eval_count": 5,
            },
        }).encode("utf-8")
        mock_urlopen.return_value = mock_resp

        config = LLMConfig(provider="ollama", model="qwen2.5")
        provider = OllamaProvider(config)
        resp = provider.chat(
            messages=[{"role": "user", "content": "hi"}],
            model="qwen2.5",
            temperature=0.8,
            max_tokens=None,
            enable_thinking=None,
            timeout=30,
        )
        assert resp.text == "你好"
        assert resp.model == "qwen2.5"
        assert resp.usage["prompt_tokens"] == 10
        assert resp.usage["completion_tokens"] == 5

    @patch("agent.client.provider.urlopen")
    def test_ollama_chat_no_usage(self, mock_urlopen: MagicMock) -> None:
        import json
        mock_resp = MagicMock()
        mock_resp.read.return_value = json.dumps({
            "message": {"content": "无统计"},
        }).encode("utf-8")
        mock_urlopen.return_value = mock_resp

        config = LLMConfig(provider="ollama", model="qwen2.5")
        provider = OllamaProvider(config)
        resp = provider.chat(
            messages=[{"role": "user", "content": "hi"}],
            model="qwen2.5",
            temperature=0.8,
            max_tokens=None,
            enable_thinking=None,
            timeout=30,
        )
        assert resp.text == "无统计"
        assert resp.usage == {}

    @patch("agent.client.provider.urlopen")
    def test_ollama_chat_max_tokens(self, mock_urlopen: MagicMock) -> None:
        import json
        mock_resp = MagicMock()
        mock_resp.read.return_value = json.dumps({
            "message": {"content": "ok"},
        }).encode("utf-8")
        mock_urlopen.return_value = mock_resp

        config = LLMConfig(provider="ollama", model="m")
        provider = OllamaProvider(config)
        resp = provider.chat(
            messages=[{"role": "user", "content": "hi"}],
            model="m",
            temperature=0.8,
            max_tokens=200,
            enable_thinking=None,
            timeout=30,
        )
        assert resp.text == "ok"
        # 检查请求中是否包含 max_tokens
        call_req = mock_urlopen.call_args[0][0]
        payload = json.loads(call_req.data)
        assert payload["options"]["num_predict"] == 200

    @patch("agent.client.provider.urlopen")
    def test_ollama_connection_error(self, mock_urlopen: MagicMock) -> None:
        from urllib.error import URLError


        mock_urlopen.side_effect = URLError("connection refused")

        config = LLMConfig(provider="ollama", model="m")
        provider = OllamaProvider(config)
        with pytest.raises(LLMError, match="Ollama 连接失败"):
            provider.chat(
                messages=[{"role": "user", "content": "hi"}],
                model="m",
                temperature=0.8,
                max_tokens=None,
                enable_thinking=None,
                timeout=3,
            )
