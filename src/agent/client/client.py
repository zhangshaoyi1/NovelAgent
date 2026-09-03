"""统一 LLM 客户端入口

这是所有 LLM 调用的唯一入口，屏蔽 Provider 细节，支持：
- 多模型分工（创作/校验）
- 多 Provider 回退（E1.4 无网络回退）
- 指数退避重试
- 结构化输出约束
- 嵌入向量生成

对外使用示例：
    from agent.client import LLMClient

    client = LLMClient()
    response = client.chat_creative([{"role": "user", "content": "..."})
    print(response.text)
"""

from __future__ import annotations

import asyncio
import json
import os
import time
from typing import Any, Optional
from urllib.error import URLError

from dotenv import load_dotenv
from pydantic import BaseModel

from agent.base.llm import LLMConfig, LLMError, LLMProvider, LLMResponse
# D-M（2026-08-29）：结构化输出自 base 引用（原 core.base → client→core 反向依赖）
from agent.base.structured_output import (
    StructuredOutputError,
    extract_json,
    pydantic_to_json_schema,
)
# 通用结果校验（§6）：声明式 ValidationSpec，在唯一出口统一校验每个 LLM 返回
from agent.base.validation import (
    DEFAULT_MAX_RETRIES,
    ValidationEngine,
    ValidationError,
    ValidationSpec,
)

# ---- LLM 调用事件埋点 -------------------------------------------------------
# 可注入 hook（默认 None 零开销）。client 层只持有一个可调用对象的引用，
# 不 import 任何上层模块，保持 "client 只依赖 base" 的分层约定；
# 由上层（workflows/service）调用 set_llm_event_hook 注入转发到 EventBus 的回调，
# 使每次 LLM chat / API(embed) 调用都落到 <project>/.events/events.jsonl。
_LLM_EVENT_HOOK: Any = None


def set_llm_event_hook(hook: Any) -> None:
    """注入 LLM 调用事件回调（payload: dict，含 type/ok/provider/model/use/latency_ms）。"""
    global _LLM_EVENT_HOOK
    _LLM_EVENT_HOOK = hook


def _notify_llm_event(payload: dict[str, Any]) -> None:
    hook = _LLM_EVENT_HOOK
    if hook is not None:
        try:
            hook(payload)
        except Exception:  # noqa: BLE001 - 事件转发失败绝不阻断 LLM 调用
            pass


class LLMClient:
    """LLM 客户端（统一入口）

    支持多种 Provider，多模型分工，网络错误自动回退。

    当环境变量 ``LLM_USE_GATEWAY=true`` 时，内部使用 llmagent Gateway
    作为后端（Phase 1+ 迁移），对外 API 完全一致，调用方零改动。
    """

    def __init__(
        self,
        config: LLMConfig | None = None,
        console: "Console | None" = None,
        primary_provider: LLMProvider | None = None,
        fallback_provider: LLMProvider | None = None,
        env_file: str | None = None,
        llm_use_gateway: bool | None = None,
    ) -> None:
        """初始化 LLMClient

        Args:
            config: LLM 配置（None 从环境变量加载）
            console: Rich Console
            primary_provider: 主 Provider
            fallback_provider: 备用 Provider
            env_file: .env 文件路径
            llm_use_gateway: 是否使用 Gateway 后端（None 时检查 LLM_USE_GATEWAY 环境变量）
        """
        # 检查是否使用 Gateway 后端
        if llm_use_gateway is None:
            _raw = os.environ.get("LLM_USE_GATEWAY", "").strip().lower()
            llm_use_gateway = _raw in ("true", "1", "yes", "on")

        if llm_use_gateway:
            self._init_gateway(config, console, env_file)
            return

        # ---- 原有初始化逻辑（未启用 Gateway 时） ----
        if config is None:
            resolved_env = env_file or os.environ.get("NOVEL_AGENT_DOTENV")
            self.config = self._load_from_env(env_file=resolved_env)
        else:
            self.config = config
        self.console = console
        self._provider = primary_provider or LLMProvider.create(self.config)
        self._fallback_provider = fallback_provider
        self.fallback_log: list[str] = []
        self._gateway: Any = None  # 标记未使用 Gateway

    def _init_gateway(
        self,
        config: LLMConfig | None,
        console: "Console | None",
        env_file: str | None,
    ) -> None:
        """使用 Gateway 后端初始化"""
        from agent.client.gateway_adapter import GatewayAdapter, _GatewayModelProvider, _load_config_from_env
        from llmagent.gateway import Gateway
        from llmagent.gateway.models import ModelCard, TaskHint
        from llmagent.gateway.providers import ProviderRegistry
        from llmagent.gateway.request_gate import RequestGate
        from llmagent.gateway.router import ComplexityRouter
        from llmagent.gateway.packer import Packer
        from llmagent.gateway.response_gate import ResponseGate, MetricsSink
        from llmagent.gateway.rate_limiter import RateLimiter, SemanticCache

        if config is None:
            config = _load_config_from_env(env_file)
        # 创建 LLMProvider
        primary = LLMProvider.create(config)
        # 创建 ProviderRegistry
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
        self._gateway = GatewayAdapter(gateway, config, console=console)
        # 保持兼容属性
        self.config = self._gateway.config
        self.console = console
        self.fallback_log: list[str] = []
        self._provider = None  # type: ignore[assignment]
        self._fallback_provider = None

    @staticmethod
    def _load_from_env(env_file: str | None = None) -> LLMConfig:
        """从环境变量加载配置"""
        if env_file:
            load_dotenv(env_file)
        else:
            load_dotenv()
            # 回退：与代码同目录的 agent/.env
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

    @property
    def provider_name(self) -> str:
        if self._gateway is not None:
            return self._gateway.provider_name
        return self.config.provider

    @property
    def is_local(self) -> bool:
        if self._gateway is not None:
            return self._gateway.is_local
        return self.config.provider == "ollama"

    def preflight(self) -> dict[str, Any]:
        if self._gateway is not None:
            return self._gateway.preflight()
        return {
            "provider": self.config.provider,
            "model": self.config.model,
            "model_utility": self.config.model_utility or self.config.model,
            "is_local": self.is_local,
            "fallback_provider": self.config.fallback_provider or None,
            "fallback_providers": list(self.config.fallback_providers),
            "fallback_model": self.config.fallback_model or None,
            "has_fallback": self._get_fallback_provider() is not None,
        }

    @staticmethod
    def _is_network_error(exc: Exception) -> bool:
        """判断是否为网络不可达错误"""
        if isinstance(exc, (URLError, ConnectionError, TimeoutError)):
            return True
        name = type(exc).__name__
        return "Connection" in name or "Connect" in name or "Timeout" in name

    def _get_fallback_provider(self) -> LLMProvider | None:
        """按回退链构建备用 Provider"""
        if self._fallback_provider is not None:
            return self._fallback_provider
        for fb_name in self.config.fallback_providers:
            fb_name = fb_name.strip().lower()
            if not fb_name or fb_name == self.config.provider:
                continue
            try:
                self._fallback_provider = LLMProvider.create(
                    LLMConfig(
                        provider=fb_name,
                        api_key=self.config.api_key,
                        base_url=self.config.base_url,
                        model=self.config.fallback_model or self.config.model,
                        model_utility=(
                            self.config.fallback_model
                            or self.config.model_utility
                            or self.config.model
                        ),
                        timeout=self.config.timeout,
                        max_retries=1,
                    )
                )
                return self._fallback_provider
            except LLMError:
                continue
        return self._fallback_provider

    def _warn_fallback(self, exc: Exception) -> None:
        fb_name = self.config.fallback_providers[0] if self.config.fallback_providers else ""
        if self._fallback_provider is not None and self._fallback_provider.config.provider:
            fb_name = self._fallback_provider.config.provider
        msg = (
            f"主 Provider({self.config.provider}) 不可达：{exc}。"
            f"已回退到备用 Provider({fb_name})"
        )
        self.fallback_log.append(msg)
        if self.console is not None:
            self.console.print(f"[yellow]⚠ {msg}[/yellow]")

    def _select_model(self, model: str | None, use: str) -> str:
        if model:
            return model
        if use == "utility":
            return self.config.model_utility or self.config.model
        return self.config.model

    def chat(
        self,
        messages: list[dict[str, str]],
        model: str | None = None,
        temperature: float = 0.8,
        max_tokens: int | None = None,
        use: str = "creative",
        enable_thinking: bool | None = None,
        validators: "list[ValidationSpec] | None" = None,
        validation_attempt: int = 0,
        **kwargs: Any,
    ) -> LLMResponse:
        """通用 chat 接口

        Args:
            messages: 对话消息列表
            model: 模型名（None 使用默认）
            temperature: 温度
            max_tokens: 最大生成 token 数
            use: 用途（creative 创作 / utility 校验），影响默认模型选择
            enable_thinking: 思考开关（覆盖配置）
            validators: 声明式结果校验规格（§6）。为空则不校验（零回归）。
                P0 失败自动附修正提示重试，耗尽抛 ``ValidationError``；
                P1 仅把问题写入 ``resp.warnings``。
            validation_attempt: 内部递归重试计数，防无限循环，调用方勿传。
        """
        # Gateway 后端委托
        if self._gateway is not None:
            return self._gateway.chat(
                messages, model=model, temperature=temperature,
                max_tokens=max_tokens, use=use,
                enable_thinking=enable_thinking, validators=validators,
                validation_attempt=validation_attempt, **kwargs,
            )
        target_model = self._select_model(model, use)
        if enable_thinking is None:
            enable_thinking = self.config.enable_thinking

        last_exc: Exception | None = None
        req_t0 = time.monotonic()  # 单次 provider 调用计时起点（每次尝试/回退时重置）

        for attempt in range(self.config.max_retries + 1):
            req_t0 = time.monotonic()
            try:
                resp = self._provider.chat(
                    messages=messages,
                    model=target_model,
                    temperature=temperature,
                    max_tokens=max_tokens,
                    enable_thinking=enable_thinking,
                    timeout=self.config.timeout,
                    **kwargs,
                )
                _notify_llm_event({
                    "type": "llm.chat",
                    "ok": True,
                    "provider": self.config.provider,
                    "model": target_model,
                    "use": use,
                    "latency_ms": round((time.monotonic() - req_t0) * 1000.0, 2),
                })
                return self._validate_and_return(
                    resp, messages, validators, validation_attempt,
                    model, temperature, max_tokens, use, enable_thinking, **kwargs,
                )
            except Exception as e:
                last_exc = e
                if self._is_network_error(e):
                    break
                if attempt < self.config.max_retries:
                    delay = self.config.retry_base_delay * (2 ** attempt)
                    time.sleep(delay)
                    continue
                break

        # 无网络回退：主 Provider 网络错误 → 切到备用 Provider
        fb = self._get_fallback_provider()
        if fb is not None and last_exc is not None and self._is_network_error(last_exc):
            self._warn_fallback(last_exc)
            fb_model = fb.config.model_utility or fb.config.model if use == "utility" else fb.config.model
            if not fb_model:
                fb_model = target_model
            req_t0 = time.monotonic()
            try:
                fb_resp = fb.chat(
                    messages=messages,
                    model=fb_model,
                    temperature=temperature,
                    max_tokens=max_tokens,
                    enable_thinking=enable_thinking,
                    timeout=self.config.timeout,
                    **kwargs,
                )
                _notify_llm_event({
                    "type": "llm.chat",
                    "ok": True,
                    "provider": fb.config.provider,
                    "model": fb_model,
                    "use": use,
                    "latency_ms": round((time.monotonic() - req_t0) * 1000.0, 2),
                })
                return self._validate_and_return(
                    fb_resp, messages, validators, validation_attempt,
                    model, temperature, max_tokens, use, enable_thinking, **kwargs,
                )
            except Exception as e2:
                last_exc = e2

        _notify_llm_event({
            "type": "llm.error",
            "ok": False,
            "provider": self.config.provider,
            "model": target_model,
            "use": use,
            "error": str(last_exc),
            "latency_ms": round((time.monotonic() - req_t0) * 1000.0, 2),
        })
        raise LLMError(
            f"LLM 调用失败（重试 {self.config.max_retries} 次后仍报错）: {last_exc}"
        ) from last_exc

    def _validate_and_return(
        self,
        resp: LLMResponse,
        messages: list[dict[str, str]],
        validators: "list[ValidationSpec] | None",
        validation_attempt: int,
        model: str | None,
        temperature: float,
        max_tokens: int | None,
        use: str,
        enable_thinking: bool | None,
        **kwargs: Any,
    ) -> LLMResponse:
        """结果校验收口：P0 失败带修正提示重试（耗尽抛 ValidationError），P1 仅告警。

        仅在 ``validators`` 非空时生效；调用方不传则原样返回（零回归）。
        """
        if not validators:
            return resp
        vr = ValidationEngine.run(resp, validators)
        if vr["p1"]:
            resp.warnings.extend(vr["p1"])
        if not vr["p0"]:
            return resp
        # P0 失败：带修正提示重试（防无限递归，受 DEFAULT_MAX_RETRIES 限制）
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
        messages: list[dict[str, str]] = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})
        resp = self.chat(messages, use=use, **kwargs)
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
        """结构化输出，约束到给定 JSON Schema

        首选 OpenAI response_format，不支持时回退文本解析。
        """
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
            # 回退：改用 json_object 再次强约束（部分网关忽略 json_schema 时，模型会吐散文，
            # 导致提取失败；json_object 能强制其输出合法 JSON，显著提升结构稳定）。
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
        """chat_structured 的异步包装（线程卸载，不阻塞事件循环）"""
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

        如果启用了 Gateway 后端，走 Gateway 的 embed 路由。
        失败返回空列表，调用方降级，绝不阻断写作。
        """
        if self._gateway is not None:
            return self._gateway.embed(texts)

        t0 = time.monotonic()
        try:
            r = self._provider.embed(texts)
            _notify_llm_event({
                "type": "api.call",
                "ok": True,
                "provider": self.config.provider,
                "latency_ms": round((time.monotonic() - t0) * 1000.0, 2),
            })
            return r
        except Exception as e:
            _notify_llm_event({
                "type": "api.call",
                "ok": False,
                "provider": self.config.provider,
                "error": str(e),
                "latency_ms": round((time.monotonic() - t0) * 1000.0, 2),
            })
            if self.console is not None:
                self.console.print(
                    f"[yellow]⚠ embed 失败，降级为无向量召回：{e}[/yellow]"
                )
            return []
