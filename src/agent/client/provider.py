"""LLM Provider 抽象层

屏蔽不同 LLM 提供商（OpenAI、Ollama 等）的差异。
通过 PROVIDERS 注册表模式扩展，新增 Provider = 继承 LLMProvider 并注册。
"""

from __future__ import annotations

import json
import time
from abc import ABC, abstractmethod
from typing import Any, Callable, ClassVar
from urllib.error import URLError
from urllib.request import Request, urlopen

from agent.client.config import LLMConfig
from agent.client.types import LLMResponse
from agent.core.exceptions import LLMError


class LLMProvider(ABC):
    """LLM 提供商抽象接口

    Provider 注册表模式（DeepSeek Harness 风格）：
    - 新增 provider 只需继承 LLMProvider 并使用 @register_provider 装饰器
    - 装饰器自动注册到 PROVIDERS 注册表，无需手动修改中心字典
    - 对扩展开放，对修改关闭
    """

    PROVIDERS: ClassVar[dict[str, type["LLMProvider"]]] = {}

    def __init__(self, config: LLMConfig) -> None:
        self.config = config

    @abstractmethod
    def chat(
        self,
        messages: list[dict[str, str]],
        model: str,
        temperature: float,
        max_tokens: int | None,
        enable_thinking: bool | None,
        timeout: int,
        **kwargs: Any,
    ) -> LLMResponse:
        ...

    def embed(self, texts: list[str]) -> list[list[float]]:
        """生成文本嵌入向量

        基类统一实现：依据 provider 名选择 OpenAI 兼容或 Ollama 嵌入端点。
        不可达时返回空列表降级（绝不阻断写章）。
        """
        from agent.core.rag.embeddings import (
            OllamaEmbedding,
            OpenAICompatibleEmbedding,
        )

        if self.config.provider == "ollama":
            provider = OllamaEmbedding(
                model=self.config.embedding_model or self.config.model,
                base_url=self.config.base_url or "http://localhost:11434",
            )
        else:
            provider = OpenAICompatibleEmbedding(
                model=self.config.embedding_model or self.config.model,
                base_url=self.config.base_url,
                api_key=self.config.api_key,
            )
        return provider.embed(texts)

    @staticmethod
    def create(config: LLMConfig) -> "LLMProvider":
        """根据配置创建 Provider（经 PROVIDERS 注册表，无 if/else）"""
        provider_name = config.provider.lower() or "openai"
        try:
            provider_cls = LLMProvider.PROVIDERS[provider_name]
        except KeyError:
            available = ", ".join(sorted(LLMProvider.PROVIDERS)) or "(空)"
            raise LLMError(
                f"未知 LLM provider: {provider_name!r}。可用 provider: {available}"
            ) from None
        return provider_cls(config)


def register_provider(
    provider_name: str | None = None,
) -> Callable[[type[LLMProvider]], type[LLMProvider]]:
    """LLM Provider 注册装饰器

    用法::

        from agent.client.provider import register_provider

        @register_provider("openai")
        class OpenAIProvider(LLMProvider):
            ...

    通过装饰器自动注册到 LLMProvider.PROVIDERS 注册表，
    新增 Provider 无需修改现有代码。
    """

    def decorator(cls: type[LLMProvider]) -> type[LLMProvider]:
        name = provider_name or cls.__name__.removesuffix("Provider").lower()
        LLMProvider.PROVIDERS[name] = cls
        return cls

    return decorator


@register_provider("openai")
class OpenAIProvider(LLMProvider):
    """OpenAI 兼容协议 Provider（默认）"""

    def __init__(self, config: LLMConfig) -> None:
        super().__init__(config)
        self._client: Any = None

    def _get_client(self) -> Any:
        if self._client is None:
            if not self.config.api_key:
                raise LLMError("LLM_API_KEY 未配置，请检查 .env")
            try:
                from openai import OpenAI
            except ImportError as e:
                raise LLMError("openai 包未安装，请运行 pip install openai") from e
            self._client = OpenAI(
                api_key=self.config.api_key,
                base_url=self.config.base_url or None,
                timeout=self.config.timeout,
            )
        return self._client

    def chat(
        self,
        messages: list[dict[str, str]],
        model: str,
        temperature: float,
        max_tokens: int | None,
        enable_thinking: bool | None,
        timeout: int,
        **kwargs: Any,
    ) -> LLMResponse:
        client = self._get_client()

        if enable_thinking is not None:
            kwargs.setdefault("extra_body", {})["enable_thinking"] = enable_thinking

        resp = client.chat.completions.create(
            model=model,
            messages=messages,
            temperature=temperature,
            max_tokens=max_tokens,
            timeout=timeout,
            **kwargs,
        )
        return self._parse_response(resp, model)

    @staticmethod
    def _parse_response(resp: Any, model: str) -> LLMResponse:
        choice = resp.choices[0]
        text = choice.message.content or ""
        usage = {}
        if resp.usage is not None:
            usage = {
                "prompt_tokens": getattr(resp.usage, "prompt_tokens", 0),
                "completion_tokens": getattr(resp.usage, "completion_tokens", 0),
                "total_tokens": getattr(resp.usage, "total_tokens", 0),
            }
        return LLMResponse(text=text, usage=usage, model=model, raw=resp)

    def embed(self, texts: list[str]) -> list[list[float]]:
        """委托到 OpenAICompatibleEmbedding"""
        from agent.core.rag.embeddings import OpenAICompatibleEmbedding

        provider = OpenAICompatibleEmbedding(
            model=self.config.embedding_model or self.config.model,
            base_url=self.config.embedding_base_url or self.config.base_url,
            api_key=self.config.embedding_api_key or self.config.api_key,
        )
        return provider.embed(texts)


@register_provider("ollama")
class OllamaProvider(LLMProvider):
    """Ollama 本地部署 Provider（无需鉴权）"""

    def __init__(self, config: LLMConfig) -> None:
        super().__init__(config)
        self.base_url = config.base_url or "http://localhost:11434"

    def chat(
        self,
        messages: list[dict[str, str]],
        model: str,
        temperature: float,
        max_tokens: int | None,
        enable_thinking: bool | None,
        timeout: int,
        **kwargs: Any,
    ) -> LLMResponse:
        import urllib.error
        import urllib.request

        url = f"{self.base_url.rstrip('/')}/api/chat"

        payload: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "stream": False,
            "options": {"temperature": temperature},
        }
        if max_tokens:
            payload["options"]["num_predict"] = max_tokens

        data = json.dumps(payload).encode("utf-8")
        req = Request(
            url,
            data=data,
            headers={"Content-Type": "application/json"},
            method="POST",
        )

        try:
            resp = urlopen(req, timeout=timeout)
            body = resp.read().decode("utf-8")
            result = json.loads(body)
        except URLError as e:
            raise LLMError(
                f"Ollama 连接失败（{url}）：{e}。请确认 Ollama 已启动（ollama serve）"
            ) from e
        except urllib.error.HTTPError as e:
            raise LLMError(
                f"Ollama HTTP 错误（{e.code}）：{e.read().decode('utf-8', errors='replace')}"
            ) from e
        except json.JSONDecodeError as e:
            raise LLMError(f"Ollama 响应解析失败：{e}") from e

        text = result.get("message", {}).get("content", "")
        usage = {}
        if "usage" in result:
            u = result["usage"]
            usage = {
                "prompt_tokens": u.get("prompt_eval_count", 0),
                "completion_tokens": u.get("eval_count", 0),
                "total_tokens": u.get("prompt_eval_count", 0) + u.get("eval_count", 0),
            }
        raw = {"choices": [{"message": {"content": text}}], "usage": result.get("usage")}
        return LLMResponse(text=text, usage=usage, model=model, raw=raw)

    def embed(self, texts: list[str]) -> list[list[float]]:
        """委托到 OllamaEmbedding"""
        from agent.core.rag.embeddings import OllamaEmbedding

        provider = OllamaEmbedding(
            model=self.config.embedding_model or self.config.model,
            base_url=self.config.base_url or "http://localhost:11434",
        )
        return provider.embed(texts)
