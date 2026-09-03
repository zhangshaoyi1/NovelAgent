"""GatewayAdapter：将 llmagent Gateway 包装为 LLMClient 兼容接口

Phase 1 适配器，提供与 LLMClient 相同的公共 API，内部调用 Gateway.chat()。
"""

from __future__ import annotations

import asyncio
import json
import os
import time
from typing import Any

from dotenv import load_dotenv
from pydantic import BaseModel

from agent.base.llm import LLMConfig, LLMError, LLMProvider, LLMResponse
from agent.base.structured_output import (
    StructuredOutputError,
    extract_json,
    pydantic_to_json_schema,
)
from agent.base.validation import (
    DEFAULT_MAX_RETRIES,
    ValidationEngine,
    ValidationError,
    ValidationSpec,
)
from agent.client.provider import OpenAIProvider, OllamaProvider


# ===== 将 agent LLMProvider 包装为 Gateway ModelProvider =====


class _GatewayModelProvider:
    """将 agent 的 LLMProvider 包装为 Gateway 的 ModelProvider 协议"""

    def __init__(self, name: str, provider: LLMProvider) -> None:
        self.name = name
        self._provider = provider

    def complete(self, packed: Any) -> Any:
        """Gateway ModelProvider.complete() 实现"""
        from llmagent.gateway.models import RawResponse

        messages = packed.messages
        route = packed.route
        t0 = time.monotonic()

        try:
            resp = self._provider.chat(
                messages=messages,
                model=route.model,
                temperature=route.card.temperature if hasattr(route.card, 'temperature') else 0.8,
                max_tokens=None,
                enable_thinking=None,
                timeout=120,
            )
            elapsed = (time.monotonic() - t0) * 1000.0
            return RawResponse(
                text=resp.text,
                provider=self.name,
                model=route.model,
                usage_input=resp.usage.get("input_tokens", 0) or packed.estimated_input_tokens,
                usage_output=resp.usage.get("output_tokens", 0),
                elapsed_ms=elapsed,
            )
        except Exception as e:
            elapsed = (time.monotonic() - t0) * 1000.0
            raise RuntimeError(f"Provider {self.name} 调用失败: {e}") from e

    def count_tokens(self, text: str) -> int:
        return len(text) // 4  # 简单估算

    def model_card(self) -> Any:
        from llmagent.gateway.models import ModelCard

        return ModelCard(
            provider=self.name,
            model=self._provider.config.model,
            cost_per_1k_input_cents=0.0,
            cost_per_1k_output_cents=0.0,
            context_window=128000,
        )


# ===== 工厂函数 =====


def _build_gateway_inner(env_file: str | None = None, console: Any = None) -> tuple[Any, LLMConfig]:
    """创建 Gateway 实例 + LLMConfig（共享内部逻辑）

    返回 (gateway, llm_config)，供 create_gateway_adapter / create_gateway_from_config 使用。
    """
    from llmagent.gateway import Gateway
    from llmagent.gateway.models import ModelCard, TaskHint
    from llmagent.gateway.providers import ProviderRegistry
    from llmagent.gateway.request_gate import RequestGate
    from llmagent.gateway.router import ComplexityRouter
    from llmagent.gateway.packer import Packer
    from llmagent.gateway.response_gate import ResponseGate, MetricsSink
    from llmagent.gateway.rate_limiter import RateLimiter, SemanticCache

    # 加载配置
    config = _load_config_from_env(env_file)

    # 创建 LLMProvider
    primary = LLMProvider.create(config)

    # 创建 ProviderRegistry 并注册
    registry = ProviderRegistry()
    registry.register(config.provider, _GatewayModelProvider(config.provider, primary))

    # 注册 fallback providers
    for fb_name in config.fallback_providers:
        fb_name = fb_name.strip().lower()
        if not fb_name or fb_name == config.provider:
            continue
        try:
            fb_config = LLMConfig(
                provider=fb_name,
                api_key=config.api_key,
                base_url=config.base_url,
                model=config.fallback_model or config.model,
                timeout=config.timeout,
                max_retries=1,
            )
            fb_provider = LLMProvider.create(fb_config)
            registry.register(fb_name, _GatewayModelProvider(fb_name, fb_provider))
        except Exception:
            if console:
                console.print(f"[yellow]⚠ 注册 fallback provider {fb_name} 失败[/yellow]")

    # 创建 Gateway
    gateway = Gateway(
        request_gate=RequestGate(),
        router=ComplexityRouter(),
        packer=Packer(),
        registry=registry,
        response_gate=ResponseGate(),
        metrics_sink=MetricsSink(),
        rate_limiter=RateLimiter(),
        semantic_cache=SemanticCache(),
    )

    return gateway, config


def create_gateway(
    env_file: str | None = None,
    console: Any = None,
) -> Any:
    """直接从环境变量创建原生 llmagent Gateway 实例

    返回 Gateway 实例，可直接调用 gateway.chat(ChatRequest(...))。
    这是重构后业务代码的推荐入口，不经过 GatewayAdapter 包装。
    """
    return _build_gateway_inner(env_file=env_file, console=console)[0]


def chat_creative(
    gateway: Any,
    messages: list[dict[str, str]],
    *,
    temperature: float = 0.8,
    max_tokens: int | None = None,
    model: str | None = None,
    enable_thinking: bool | None = None,
) -> str:
    """使用原生 Gateway 执行创作型 LLM 调用（chat_creative 语义等价）

    参数语义与旧 LLMClient.chat_creative 一致，内部构造 ChatRequest 调用 Gateway。
    """
    from llmagent.gateway.models import ChatRequest, HintComplexity, TaskHint

    hint = TaskHint(
        complexity=HintComplexity.complex,
        quality_critical=True,
        max_tokens=max_tokens,
        temperature=temperature,
    )
    extra: dict[str, Any] = {}
    if model:
        extra["model"] = model
    if enable_thinking is not None:
        extra["enable_thinking"] = enable_thinking
    req = ChatRequest(messages=messages, hint=hint, extra=extra or None)
    resp = gateway.chat(req)
    return resp.text


def chat_utility(
    gateway: Any,
    messages: list[dict[str, str]],
    *,
    temperature: float = 0.3,
    max_tokens: int | None = None,
    model: str | None = None,
) -> str:
    """使用原生 Gateway 执行实用型 LLM 调用（chat_utility 语义等价）"""
    from llmagent.gateway.models import ChatRequest, HintComplexity, TaskHint

    hint = TaskHint(
        complexity=HintComplexity.simple,
        quality_critical=False,
        max_tokens=max_tokens,
        temperature=temperature,
    )
    extra: dict[str, Any] = {}
    if model:
        extra["model"] = model
    req = ChatRequest(messages=messages, hint=hint, extra=extra or None)
    resp = gateway.chat(req)
    return resp.text


def chat_structured(
    gateway: Any,
    messages: list[dict[str, str]],
    schema: type[BaseModel],
    *,
    temperature: float = 0.8,
    max_tokens: int | None = None,
    model: str | None = None,
    use: str = "creative",
) -> BaseModel:
    """使用原生 Gateway 执行结构化输出 LLM 调用（chat_structured 语义等价）

    返回解析后的 Pydantic 模型实例。
    """
    # 在 system prompt 中嵌入 schema 约束
    schema_json = pydantic_to_json_schema(schema)
    schema_text = json.dumps(schema_json, ensure_ascii=False, indent=2)
    from llmagent.gateway.models import ChatRequest, HintComplexity, TaskHint

    system_msg = f"请严格按照以下 JSON Schema 输出结构化数据：\n{schema_text}"
    enhanced = [{"role": "system", "content": system_msg}] + messages

    hint = TaskHint(
        complexity=HintComplexity.complex if use == "creative" else HintComplexity.simple,
        quality_critical=True,
        max_tokens=max_tokens,
        temperature=temperature,
    )
    extra: dict[str, Any] = {}
    if model:
        extra["model"] = model
    req = ChatRequest(messages=enhanced, hint=hint, extra=extra or None)
    resp = gateway.chat(req)

    # 解析 JSON 到 Pydantic
    parsed = extract_json(resp.text)
    return schema.model_validate(parsed)


def create_gateway_adapter(
    console: Any = None,
    env_file: str | None = None,
) -> "GatewayAdapter":
    """便捷工厂：直接从环境变量创建 GatewayAdapter

    返回直接可用的 GatewayAdapter 实例，API 与 LLMClient 完全兼容。
    """
    gateway, config = _build_gateway_inner(env_file=env_file, console=console)
    return GatewayAdapter(
        gateway, config,
        console=console,
    )


def create_gateway_from_config(
    env_file: str | None = None,
    console: Any = None,
) -> tuple[Any, LLMConfig]:
    """从环境变量创建 Gateway 实例 + LLMConfig

    返回 (gateway, llm_config)，供 GatewayAdapter 使用。
    """
    return _build_gateway_inner(env_file=env_file, console=console)


def _load_config_from_env(env_file: str | None = None) -> LLMConfig:
    """从环境变量加载 LLMConfig（与 LLMClient._load_from_env 一致）"""
    if env_file:
        load_dotenv(env_file)
    else:
        load_dotenv()
        try:
            import agent as _agent_pkg

            _pkg_dir = os.path.dirname(_agent_pkg.__file__)
            for _cand in (
                os.path.join(_pkg_dir, ".env"),
                os.path.join(os.path.dirname(_pkg_dir), ".env"),
                os.path.join(os.path.dirname(os.path.dirname(_pkg_dir)), ".env"),
            ):
                if os.path.exists(_cand):
                    load_dotenv(_cand)
                    break
        except Exception:
            pass

    model = os.getenv("LLM_MODEL_ID", "glm-5.2")
    model_utility = os.getenv("LLM_MODEL_UTILITY", "") or model
    embedding_model = os.getenv("EMBEDDING_MODEL_ID", "") or model
    provider = os.getenv("LLM_PROVIDER", "openai").lower()
    embedding_base_url = os.getenv("EMBEDDING_BASE_URL", "")
    embedding_api_key = os.getenv("EMBEDDING_API_KEY", "")
    embedding_provider = os.getenv("EMBEDDING_PROVIDER", "").lower()

    _et_raw = os.getenv("LLM_ENABLE_THINKING", "").strip().lower()
    enable_thinking: bool | None = None
    if _et_raw in ("false", "0", "no", "off"):
        enable_thinking = False
    elif _et_raw in ("true", "1", "yes", "on"):
        enable_thinking = True

    return LLMConfig(
        provider=provider,
        api_key=os.getenv("LLM_API_KEY", ""),
        base_url=os.getenv("LLM_BASE_URL", ""),
        model=model,
        model_utility=model_utility,
        embedding_model=embedding_model,
        embedding_base_url=embedding_base_url,
        embedding_api_key=embedding_api_key,
        embedding_provider=embedding_provider,
        timeout=int(os.getenv("LLM_TIMEOUT", "120")),
        max_retries=int(os.getenv("LLM_MAX_RETRIES", "3")),
        retry_base_delay=float(os.getenv("LLM_RETRY_BASE_DELAY", "1.0")),
        fallback_providers=[
            p.strip()
            for p in os.getenv("LLM_FALLBACK_PROVIDER", "").split(",")
            if p.strip()
        ],
        fallback_model=os.getenv("LLM_FALLBACK_MODEL", ""),
        enable_thinking=enable_thinking,
    )


# ===== GatewayAdapter =====


class GatewayAdapter:
    """将 llmagent Gateway 包装为 LLMClient 兼容接口（已废弃，请使用原生 Gateway）

    提供与 LLMClient 相同的公共 API（chat, chat_creative, chat_utility,
    complete, chat_structured, embed, preflight 等），内部调用 Gateway.chat()。

    此适配器仅为向后兼容保留，新代码请直接使用 ``create_gateway()``
    返回的原生 Gateway 实例 + ``chat_creative()`` / ``chat_utility()`` 辅助函数。

    使用方式:
        gateway, config = create_gateway_from_config()
        adapter = GatewayAdapter(gateway, config)
        resp = adapter.chat_creative([{"role": "user", "content": "..."}])
    """

    def __init__(
        self,
        gateway: Any,
        llm_config: LLMConfig | None = None,
        console: Any = None,
    ) -> None:
        self._gateway = gateway
        self._config = llm_config
        self.console = console

        # 缓存原始 provider 用于 embed
        self._embed_provider: LLMProvider | None = None
        self.config = llm_config
        if llm_config:
            try:
                self._embed_provider = LLMProvider.create(llm_config)
            except Exception:
                pass

    @property
    def gateway(self) -> Any:
        return self._gateway

    @property
    def provider_name(self) -> str:
        """返回主 provider 名称"""
        cards = self._gateway.registry.available()
        if cards:
            return cards[0].provider
        return self._config.provider if self._config else "unknown"

    @property
    def is_local(self) -> bool:
        return self._config is not None and self._config.provider == "ollama"

    def preflight(self) -> dict[str, Any]:
        """返回预检信息"""
        cards = self._gateway.registry.available()
        return {
            "provider": self.provider_name,
            "model": cards[0].model if cards else (self._config.model if self._config else ""),
            "model_utility": self._config.model_utility if self._config else "",
            "is_local": self.is_local,
            "available_providers": [c.provider for c in cards],
            "available_models": [c.model for c in cards],
            "gateway": True,
        }

    def chat(
        self,
        messages: list[dict[str, str]],
        model: str | None = None,
        temperature: float = 0.8,
        max_tokens: int | None = None,
        use: str = "creative",
        enable_thinking: bool | None = None,
        validators: list[ValidationSpec] | None = None,
        validation_attempt: int = 0,
        **kwargs: Any,
    ) -> LLMResponse:
        """通用 chat 接口（与 LLMClient.chat 签名一致）"""
        from llmagent.gateway.models import ChatRequest, HintComplexity, TaskHint

        # 构造 ChatRequest
        hint = TaskHint(
            complexity=HintComplexity.simple if use == "utility" else HintComplexity.complex,
            quality_critical=(use != "utility"),
            max_tokens=max_tokens,
            temperature=temperature,
        )
        extra: dict[str, Any] = {}
        if model:
            extra["model"] = model
        if enable_thinking is not None:
            extra["enable_thinking"] = enable_thinking
        # 透传 kwargs 中的额外参数（如 response_format）
        extra.update(kwargs)

        req = ChatRequest(
            messages=messages,
            hint=hint,
            extra=extra,
        )

        # 调用 Gateway
        t0 = time.monotonic()
        try:
            resp = self._gateway.chat(req)
        except Exception as e:
            # 兜底：直接调用 LLMProvider
            if self._embed_provider:
                fallback_resp = self._fallback_chat(messages, model, temperature, max_tokens, use, enable_thinking, **kwargs)
                if fallback_resp is not None:
                    return fallback_resp
            raise LLMError(f"Gateway 调用失败: {e}") from e

        elapsed = time.monotonic() - t0
        llm_resp = LLMResponse(
            text=resp.text,
            usage={"input_tokens": resp.usage_input, "output_tokens": resp.usage_output},
            model=resp.model,
        )

        # 结果校验
        if validators:
            return self._validate_and_return(
                llm_resp, messages, validators, validation_attempt,
                model, temperature, max_tokens, use, enable_thinking, **kwargs,
            )
        return llm_resp

    def _fallback_chat(
        self,
        messages: list[dict[str, str]],
        model: str | None,
        temperature: float,
        max_tokens: int | None,
        use: str,
        enable_thinking: bool | None,
        **kwargs: Any,
    ) -> LLMResponse | None:
        """Gateway 失败时兜底：直接调用 LLMProvider"""
        if self._embed_provider is None:
            return None
        target_model = model or (self._config.model_utility or self._config.model if use == "utility" else self._config.model)
        try:
            return self._embed_provider.chat(
                messages=messages,
                model=target_model,
                temperature=temperature,
                max_tokens=max_tokens,
                enable_thinking=enable_thinking,
                timeout=120,
                **kwargs,
            )
        except Exception:
            return None

    def _validate_and_return(
        self,
        resp: LLMResponse,
        messages: list[dict[str, str]],
        validators: list[ValidationSpec] | None,
        validation_attempt: int,
        model: str | None,
        temperature: float,
        max_tokens: int | None,
        use: str,
        enable_thinking: bool | None,
        **kwargs: Any,
    ) -> LLMResponse:
        """结果校验收口（与 LLMClient._validate_and_return 一致）"""
        if not validators:
            return resp
        vr = ValidationEngine.run(resp, validators)
        if vr["p1"]:
            resp.warnings.extend(vr["p1"])
        if not vr["p0"]:
            return resp
        if validation_attempt < DEFAULT_MAX_RETRIES:
            if self.console is not None:
                self.console.print(
                    f"[yellow]⚠ LLM 输出未通过结果校验（第 {validation_attempt + 1} 次），"
                    f"重试修正：{vr['p0']}[/yellow]"
                )
            corrective = (
                "⚠️ 你的上一次输出未通过结果校验："
                + "；".join(vr["p0"])
                + "。请严格按原始要求重新生成，确保满足上述约束。"
            )
            corrected = messages + [{"role": "user", "content": corrective}]
            return self.chat(
                corrected,
                model=model,
                temperature=temperature,
                max_tokens=max_tokens,
                use=use,
                enable_thinking=enable_thinking,
                validators=validators,
                validation_attempt=validation_attempt + 1,
                **kwargs,
            )
        raise ValidationError("；".join(vr["p0"]))

    def chat_creative(
        self, messages: list[dict[str, str]], **kwargs: Any
    ) -> LLMResponse:
        """创作类请求（高温度，用主模型）"""
        return self.chat(messages, use="creative", **kwargs)

    def chat_utility(
        self, messages: list[dict[str, str]], **kwargs: Any
    ) -> LLMResponse:
        """校验/摘要类请求（低温度，用轻量模型）"""
        kwargs.setdefault("temperature", 0.2)
        return self.chat(messages, use="utility", **kwargs)

    def complete(
        self,
        prompt: str,
        system: str | None = None,
        use: str = "creative",
        **kwargs: Any,
    ) -> str:
        """快捷单次 completion，返回纯文本"""
        msgs: list[dict[str, str]] = []
        if system:
            msgs.append({"role": "system", "content": system})
        msgs.append({"role": "user", "content": prompt})
        resp = self.chat(msgs, use=use, **kwargs)
        return resp.text

    def chat_structured(
        self,
        messages: list[dict[str, str]],
        schema: type[BaseModel] | dict[str, Any],
        *,
        model: str | None = None,
        temperature: float = 0.2,
        max_tokens: int | None = None,
        use: str = "utility",
        name: str = "structured_output",
        strict: bool = False,
        enable_thinking: bool | None = None,
        **kwargs: Any,
    ) -> dict[str, Any]:
        """结构化输出，约束到给定 JSON Schema"""
        json_schema = (
            pydantic_to_json_schema(schema)
            if not isinstance(schema, dict)
            else schema
        )
        response_format = {
            "type": "json_schema",
            "json_schema": {"name": name, "schema": json_schema, "strict": strict},
        }
        try:
            resp = self.chat(
                messages,
                model=model,
                temperature=temperature,
                max_tokens=max_tokens,
                use=use,
                enable_thinking=enable_thinking,
                response_format=response_format,
                **kwargs,
            )
            return extract_json(resp.text)
        except (LLMError, StructuredOutputError, ValueError) as e:
            # 回退：json_object
            try:
                resp2 = self.chat(
                    messages,
                    model=model,
                    temperature=temperature,
                    max_tokens=max_tokens,
                    use=use,
                    enable_thinking=enable_thinking,
                    response_format={"type": "json_object"},
                    **kwargs,
                )
                return extract_json(resp2.text)
            except Exception as e2:
                raise StructuredOutputError(
                    f"结构化输出失败（含回退）: {e} | {e2}"
                ) from e2

    async def chat_structured_async(
        self,
        messages: list[dict[str, str]],
        schema: type[BaseModel] | dict[str, Any],
        *,
        model: str | None = None,
        temperature: float = 0.2,
        max_tokens: int | None = None,
        use: str = "utility",
        name: str = "structured_output",
        strict: bool = False,
        enable_thinking: bool | None = None,
        **kwargs: Any,
    ) -> dict[str, Any]:
        """chat_structured 的异步包装"""
        return await asyncio.to_thread(
            self.chat_structured,
            messages,
            schema,
            model=model,
            temperature=temperature,
            max_tokens=max_tokens,
            use=use,
            name=name,
            strict=strict,
            enable_thinking=enable_thinking,
            **kwargs,
        )

    def embed(self, texts: list[str]) -> list[list[float]]:
        """生成文本嵌入向量

        失败返回空列表，调用方降级，绝不阻断写作。
        """
        if self._embed_provider is None:
            return []
        t0 = time.monotonic()
        try:
            return self._embed_provider.embed(texts)
        except Exception as e:
            if self.console is not None:
                self.console.print(
                    f"[yellow]⚠ embed 失败，降级为无向量召回：{e}[/yellow]"
                )
            return []