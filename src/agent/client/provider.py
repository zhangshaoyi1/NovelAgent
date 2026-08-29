"""LLM Provider 具体实现（OpenAI / Ollama）

下沉说明（2026-08-29）：``LLMProvider`` 抽象基类 / ``register_provider`` / ``LLMConfig`` /
``LLMResponse`` / ``LLMError`` 已下沉至 ``agent.base.llm``（消除 ``client→core`` 反向依赖）。
本模块仅保留具体 Provider 实现，并从 base 导入协议类型；``embed()`` 经
``client/embedding_router`` 实现（embedding 能力随 client 层，不依赖 core）。
通过 PROVIDERS 注册表模式扩展，新增 Provider = 继承 LLMProvider 并注册。
"""

from __future__ import annotations

import json
from typing import Any
from urllib.error import URLError
from urllib.request import Request, urlopen

from agent.base.llm import (
    LLMConfig,
    LLMError,
    LLMProvider,
    LLMResponse,
    register_provider,
)


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
        """生成文本嵌入向量（经 client/embedding_router 路由）"""
        from agent.client.embedding_router import get_embedding_provider

        provider = get_embedding_provider(self.config)
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
        """生成文本嵌入向量（经 client/embedding_router 路由）"""
        from agent.client.embedding_router import get_embedding_provider

        provider = get_embedding_provider(self.config)
        return provider.embed(texts)
