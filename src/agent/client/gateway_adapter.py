"""Gateway 原生工厂与辅助函数

提供 llmagent Gateway 的创建工厂和常用 LLM 调用辅助函数。
业务代码推荐使用 ``create_gateway()`` + ``chat_creative()`` / ``chat_utility()``。
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from agent.base.llm import LLMConfig, LLMProvider
from agent.base.structured_output import (
    StructuredOutputError,
    extract_json,
    pydantic_to_json_schema,
)
# 用量埋点出口（同层模块，无循环依赖；失败不阻断调用）
from agent.client.llm_usage import notify_llm_usage


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

        # 温度：优先用调用方 hint 指定的按次温度；缺省回退 0.8
        temperature = getattr(packed, "temperature", None)
        if temperature is None:
            temperature = (
                route.card.temperature if hasattr(route.card, "temperature") else 0.8
            )
        # 思考开关：调用方按次显式指定优先，否则取 Provider 配置
        # （.env 或 Web 端模型档案）；此前硬编码 None/120s 会导致
        # LLM_ENABLE_THINKING 与 LLM_TIMEOUT 失效
        enable_thinking = getattr(packed, "enable_thinking", None)
        if enable_thinking is None:
            enable_thinking = getattr(self._provider.config, "enable_thinking", None)
        timeout = int(getattr(self._provider.config, "timeout", 0) or 120)

        try:
            resp = self._provider.chat(
                messages=messages,
                model=route.model,
                temperature=temperature,
                max_tokens=None,
                enable_thinking=enable_thinking,
                timeout=timeout,
            )
            elapsed = (time.monotonic() - t0) * 1000.0
            # usage 可能缺省（部分 provider 不返回）→ None.get() 会崩并连带
            # 上层去AI味等 LLM 步骤被静默跳过，这里统一兜底为空字典。
            # 键名兼容两套口径：LLMProvider 返回 OpenAI 兼容的
            # prompt_tokens/completion_tokens；llmagent 原生 RawResponse 用
            # input_tokens/output_tokens。都不在时回退 packer 估算。
            usage = resp.usage or {}
            tokens_in = int(
                usage.get("input_tokens")
                or usage.get("prompt_tokens")
                or packed.estimated_input_tokens
                or 0
            )
            tokens_out = int(
                usage.get("output_tokens") or usage.get("completion_tokens") or 0
            )
            # 缓存命中 token（provider 返回 prompt_tokens_details.cached_tokens；
            # 键名兼容 input_tokens / prompt_tokens 两种口径，缺失为 0）
            tokens_cached = int(
                usage.get("cached_tokens", 0)
                or usage.get("input_tokens_cached", 0)
                or 0
            )
            # LLMOps 用量埋点：所有 create_gateway() 调用的唯一收口（失败不阻断）
            notify_llm_usage({
                "type": "llm.usage",
                "ok": True,
                "provider": self.name,
                "model": route.model,
                "tokens_in": tokens_in,
                "tokens_out": tokens_out,
                "tokens_cached": tokens_cached,
                "latency_ms": round(elapsed, 2),
            })
            return RawResponse(
                text=resp.text,
                provider=self.name,
                model=route.model,
                usage_input=tokens_in,
                usage_output=tokens_out,
                elapsed_ms=elapsed,
            )
        except Exception as e:
            elapsed = (time.monotonic() - t0) * 1000.0
            notify_llm_usage({
                "type": "llm.usage",
                "ok": False,
                "provider": self.name,
                "model": route.model,
                "tokens_in": 0,
                "tokens_out": 0,
                "latency_ms": round(elapsed, 2),
                "error": str(e)[:300],
            })
            # 延迟导入：agent.core.__init__ 会反向 import 本模块，
            # 顶层导入会造成导入顺序相关的循环依赖
            from agent.core.base.exceptions import (
                FatalProviderError,
                is_fatal_provider_error,
            )

            # 配额耗尽/欠费/鉴权失败 → 抛 FatalProviderError，让上层立即熔断
            # 而非按瞬时故障退避重试（403 重试必然再次 403）
            if is_fatal_provider_error(e):
                raise FatalProviderError(f"Provider {self.name} 调用失败: {e}") from e
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

    返回 (gateway, llm_config)，供 create_gateway 使用。
    """
    from llmagent.gateway import Gateway
    from llmagent.gateway.models import ModelCard, TaskHint
    from llmagent.gateway.providers import ProviderRegistry
    from llmagent.gateway.request_gate import RequestGate
    from llmagent.gateway.router import ComplexityRouter, HintCalibrator
    from llmagent.gateway.packer import Packer
    from llmagent.gateway.response_gate import ResponseGate, MetricsSink
    from llmagent.gateway.rate_limiter import RateLimiter, SemanticCache

    # 加载配置
    from agent.base.config import ConfigLoader

    config = ConfigLoader.get_llm_config(env_file)

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

    # O4：hint 校准器（历史真实用量落盘到 ~/.novel-agent/hint_calibration.json，
    # 跨进程学习 Task 自评 simple 却大产出的标签）
    try:
        calib_path = Path(
            os.environ.get(
                "LLMAGENT_HINT_CALIBRATION",
                str(Path.home() / ".novel-agent" / "hint_calibration.json"),
            )
        )
        calibrator: Any = HintCalibrator(persist_path=calib_path)
    except Exception:  # noqa: BLE001 - 校准器不可用时退化为不校准
        calibrator = None

    # 创建 Gateway
    gateway = Gateway(
        request_gate=RequestGate(),
        router=ComplexityRouter(calibrator=calibrator),
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
    """使用原生 Gateway 执行创作型 LLM 调用

    返回纯文本响应。
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
    req = ChatRequest(messages=messages, hint=hint, extra=extra or {})
    resp = gateway.chat(req)
    return resp.text


def chat_utility(
    gateway: Any,
    messages: list[dict[str, str]],
    *,
    temperature: float = 0.3,
    max_tokens: int | None = None,
    model: str | None = None,
    enable_thinking: bool | None = None,
) -> str:
    """使用原生 Gateway 执行实用型 LLM 调用

    返回纯文本响应。
    """
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
    if enable_thinking is not None:
        extra["enable_thinking"] = enable_thinking
    req = ChatRequest(messages=messages, hint=hint, extra=extra or {})
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
    enable_thinking: bool | None = None,
) -> BaseModel:
    """使用原生 Gateway 执行结构化输出 LLM 调用

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
    if enable_thinking is not None:
        extra["enable_thinking"] = enable_thinking
    req = ChatRequest(messages=enhanced, hint=hint, extra=extra or {})
    resp = gateway.chat(req)

    # 解析 JSON 到 Pydantic
    parsed = extract_json(resp.text)
    return schema.model_validate(parsed)


def _load_config_from_env(env_file: str | None = None) -> LLMConfig:
    """从环境变量加载 LLMConfig（委托给 ConfigLoader）

    .. deprecated::
        请直接使用 ``ConfigLoader.get_llm_config(env_file)``。
    """
    from agent.base.config import ConfigLoader

    return ConfigLoader.get_llm_config(env_file)