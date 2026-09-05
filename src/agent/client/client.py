"""统一 LLM 客户端入口（兼容门面，唯一后端为 llmagent Gateway）

2026-09-05 收敛债③：旧直连 Provider 路径（重试/回退链）已删除，本类是
Gateway 的薄门面，对外 API 不变；新代码请直接使用 ``agent.client.gateway_adapter``。

旧用法（已废弃）：
    from agent.client import LLMClient

    client = LLMClient()
    response = client.chat_creative([{"role": "user", "content": "..."})
    print(response.text)

新用法（推荐）：
    from agent.client.gateway_adapter import create_gateway, chat_creative

    gateway = create_gateway()
    text = chat_creative(gateway, [{"role": "user", "content": "..."}])
"""

from __future__ import annotations

import asyncio
from typing import Any

from pydantic import BaseModel
from rich.console import Console

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
    作为后端，对外 API 完全一致，调用方零改动。
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
        """初始化 LLMClient（已废弃，请使用 gateway_adapter）

        ⚠️  DEPRECATED：此类已废弃，请使用 ``agent.client.gateway_adapter``
            的 ``create_gateway()`` + ``chat_creative()`` / ``chat_utility()``。
            保留仅用于测试兼容。

        Args:
            config: LLM 配置（None 从环境变量加载）
            console: Rich Console
            primary_provider: 主 Provider
            fallback_provider: 备用 Provider
            env_file: .env 文件路径
            llm_use_gateway: 是否使用 Gateway 后端（None 时检查 LLM_USE_GATEWAY 环境变量）
        """
        import warnings
        warnings.warn(
            "LLMClient 已废弃，请使用 agent.client.gateway_adapter 的辅助函数",
            DeprecationWarning, stacklevel=2,
        )

        # 2026-09-05 收敛债③：Gateway 成为唯一后端，旧直连 Provider 路径已删除。
        # primary_provider / fallback_provider / llm_use_gateway 参数仅为签名兼容保留（忽略）。
        _ = primary_provider, fallback_provider, llm_use_gateway
        self._init_gateway(config, console, env_file)

    def _init_gateway(
        self,
        config: LLMConfig | None,
        console: "Console | None",
        env_file: str | None,
    ) -> None:
        """使用原生 llmagent Gateway 初始化"""
        from agent.client.gateway_adapter import create_gateway

        self._gateway = create_gateway(env_file=env_file, console=console)
        self.config = _load_config_from_env(env_file)
        self.console = console


    def _build_chat_request(
        self,
        messages: list[dict[str, str]],
        temperature: float = 0.8,
        max_tokens: int | None = None,
        use: str = "creative",
        model: str | None = None,
        enable_thinking: bool | None = None,
        **kwargs: Any,
    ) -> Any:
        """构造 llmagent ChatRequest"""
        from llmagent.gateway.models import ChatRequest, HintComplexity, TaskHint

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
        extra.update(kwargs)
        return ChatRequest(messages=messages, hint=hint, extra=extra or None)

    @property
    def provider_name(self) -> str:
        cards = self._gateway.registry.available()
        return cards[0].provider if cards else "unknown"

    @property
    def is_local(self) -> bool:
        cards = self._gateway.registry.available()
        return "ollama" in cards[0].provider.lower() if cards else False

    def preflight(self) -> dict[str, Any]:
        if self._gateway is not None:
            cards = self._gateway.registry.available()
            return {
                "provider": cards[0].provider if cards else "unknown",
                "model": cards[0].model if cards else "",
                "model_utility": self.config.model_utility if hasattr(self, 'config') and self.config else "",
                "is_local": self.is_local,
                "available_providers": [c.provider for c in cards],
                "available_models": [c.model for c in cards],
                "gateway": True,
            }

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
        # Gateway 后端委托（唯一路径）
        req = self._build_chat_request(
            messages, temperature=temperature, max_tokens=max_tokens,
            use=use, model=model, enable_thinking=enable_thinking, **kwargs,
        )
        try:
            resp = self._gateway.chat(req)
        except LLMError:
            raise
        except Exception as e:
            raise LLMError(f"Gateway 调用失败: {e}") from e
        llm_resp = LLMResponse(
            text=resp.text,
            usage={"input_tokens": resp.usage_input, "output_tokens": resp.usage_output},
            model=resp.model,
        )
        # 结果校验（validators 非空时生效；空则原样返回）
        if validators:
            return self._validate_and_return(
                llm_resp, messages, validators, validation_attempt,
                model, temperature, max_tokens, use, enable_thinking, **kwargs,
            )
        return llm_resp

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
            # 使用 embedding_router 进行嵌入
            try:
                from agent.client.embedding_router import get_embedding_provider

                provider = get_embedding_provider(self.config if hasattr(self, 'config') else None)
                if provider is not None:
                    return provider.embed(texts)
            except Exception:
                pass
            return []



def _load_config_from_env(env_file: str | None = None) -> LLMConfig:
    """从环境变量加载 LLMConfig（委托给 ConfigLoader）"""
    from agent.base.config import ConfigLoader

    return ConfigLoader.get_llm_config(env_file)