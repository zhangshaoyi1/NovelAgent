"""LLMOps · 可追踪 LLM 客户端

轻量包装 ``llmagent.gateway.Gateway``，提供 ``chat_creative`` / ``chat_utility`` /
``chat_structured`` 语义化入口与 Gateway 兼容代理，**并声明调用用途**供唯一
收口写出正确的 ``TraceSpan.use``。

记账归属（2026-09-18 记账双收口事故后确立）：
**TraceStore 的唯一写入收口是 ``client/gateway_adapter`` → ``llm_wiring`` 的
用量 hook**（provider 级，覆盖 100% 的 ``create_gateway()`` 调用）。本包装层
**不再无条件写 span**，只在调用期间未触发该收口时**兜底补记**（底层非 Gateway
的测试替身 / 未接线 / 语义缓存命中）。否则同一物理调用被记两次 ⇒ ``totals()``
虚增 100% ⇒ 假熔断 ⇒ 自动降档 ⇒ boost 层评审维度被削。

用法：
    from agent.core.llmops.trace import TraceStore, set_tracer
    from agent.core.llmops.traced_llm import TracedLLMClient

    set_tracer(TraceStore(project_dir))
    llm = TracedLLMClient(create_gateway(), model="creative-strong")
    llm.chat_creative(messages)  # span 由唯一收口记录（若底层非 Gateway 则此处兜底）
"""

from __future__ import annotations

import time
from typing import Any

from llmagent.gateway.models import ChatRequest, HintComplexity, TaskHint

# 用量记账收口（client 层，无循环依赖）：epoch 判定 + 调用用途声明
from agent.client.llm_usage import llm_use, usage_epoch
from agent.core.llmops.trace import TraceSpan, get_tracer


def _extract_tokens(resp: Any) -> tuple[int, int]:
    """从响应对象尽力提取 token（支持 RawResponse 风格；否则按字符粗估）。"""
    usage_input = getattr(resp, "usage_input", None)
    usage_output = getattr(resp, "usage_output", None)
    if usage_input is not None and usage_output is not None:
        return int(usage_input), int(usage_output)
    text = getattr(resp, "text", "") or ""
    est = max(1, len(str(text)) // 2)
    return est // 3, est // 3


class TracedLLMClient:
    """可追踪 LLM 包装。

    实现完整的 Gateway 兼容接口，同时提供 ``chat_creative`` / ``chat_utility`` / ``chat_structured``
    便捷方法。可同时作为 Gateway 的透明代理（插拔式替换）和追踪装饰器使用。

    Args:
        llm: 原生 llmagent Gateway 实例。
        model: 记录用的模型名。
        cost_per_call: 单次估算成本（USD），默认 0（看板仅统计 token）。
        tracer: 覆盖全局 Tracer。
    """

    def __init__(
        self,
        llm: Any,
        model: str = "creative-strong",
        cost_per_call: float = 0.0,
        tracer: Any = None,
    ) -> None:
        self._llm = llm
        self.model = model
        self.cost_per_call = cost_per_call
        self._tracer = tracer

    def _build_hint(self, use: str, **kwargs: Any) -> TaskHint:
        return TaskHint(
            complexity=HintComplexity.simple if use == "utility" else HintComplexity.complex,
            quality_critical=(use != "utility"),
            max_tokens=kwargs.get("max_tokens"),
            temperature=kwargs.get("temperature", 0.2 if use == "utility" else 0.8),
        )

    def _record(self, use: str, req: ChatRequest, extra: dict[str, Any] | None = None) -> Any:
        """发起调用；**仅在唯一收口未记账时**兜底补写一个 TraceSpan。

        判定依据：调用前后 ``usage_epoch()`` 是否增长（provider 级收口每次投递
        通知都会 +1）。增长 ⇒ 收口已写 span ⇒ 本层静默（避免虚增 100%）；
        未增长 ⇒ 底层不是 Gateway / 未接线 / 语义缓存命中 ⇒ 本层补记，
        保证「有调用必有 span」「一调用恰一 span」。
        """
        tracer = self._tracer or get_tracer()
        t0 = time.time()
        ok = True
        err = ""
        resp = None
        # ---- HA-Eval L1：缓存命中留痕 ----
        # 旧实现中"缓存命中"与"真实调用"在 trace 里完全同形（latency≈0 且 token 数相同），
        # 只能靠人工比对推断。这里把 cache_hit / cache_key 写进 meta，使串值事故可被直接观测。
        cache_hit = False
        cache_key = ""
        epoch0 = usage_epoch()
        try:
            if self._llm is None:
                raise RuntimeError("TracedLLMClient: 底层 LLM (Gateway) 未初始化")
            # 声明调用用途：provider 层不可见 creative/utility，靠此经收口落到 span.use
            with llm_use(use):
                resp = self._llm.chat(req)
            cache_hit = bool(getattr(resp, "cache_hit", False))
            cache_key = str(getattr(resp, "cache_key", "") or "")
        except Exception as e:  # noqa: BLE001
            ok = False
            err = str(e)
            dt_ms = (time.time() - t0) * 1000.0
            if usage_epoch() <= epoch0:
                tracer.record(
                    TraceSpan(
                        model=self.model,
                        use=use,
                        tokens_in=0,
                        tokens_out=0,
                        latency_ms=dt_ms,
                        cost=self.cost_per_call,
                        ok=ok,
                        error=err,
                        meta={"cache_hit": cache_hit, "cache_key": cache_key},
                    )
                )
            raise
        dt_ms = (time.time() - t0) * 1000.0
        tin, tout = (0, 0)
        if resp is not None:
            tin, tout = _extract_tokens(resp)
        if usage_epoch() <= epoch0:
            tracer.record(
                TraceSpan(
                    model=self.model,
                    use=use,
                    tokens_in=tin,
                    tokens_out=tout,
                    latency_ms=dt_ms,
                    cost=self.cost_per_call,
                    ok=ok,
                    error=err,
                    meta={"cache_hit": cache_hit, "cache_key": cache_key},
                )
            )
        return resp

    def chat_creative(
        self,
        messages: list[dict[str, str]],
        **kwargs: Any,
    ) -> Any:
        """创作型 LLM 调用"""
        hint = self._build_hint("creative", **kwargs)
        extra: dict[str, Any] = {}
        if kwargs.get("model"):
            extra["model"] = kwargs["model"]
        if kwargs.get("enable_thinking") is not None:
            extra["enable_thinking"] = kwargs["enable_thinking"]
        req = ChatRequest(messages=messages, hint=hint, extra=extra or None)
        return self._record("creative", req)

    def chat_utility(
        self,
        messages: list[dict[str, str]],
        **kwargs: Any,
    ) -> Any:
        """校验/摘要型 LLM 调用"""
        kwargs.setdefault("temperature", 0.2)
        hint = self._build_hint("utility", **kwargs)
        extra: dict[str, Any] = {}
        if kwargs.get("model"):
            extra["model"] = kwargs["model"]
        if kwargs.get("enable_thinking") is not None:
            extra["enable_thinking"] = kwargs["enable_thinking"]
        req = ChatRequest(messages=messages, hint=hint, extra=extra or None)
        return self._record("utility", req)

    def chat_structured(
        self,
        messages: list[dict[str, str]],
        schema: Any = None,
        *,
        use: str = "utility",
        **kwargs: Any,
    ) -> Any:
        """结构化输出 LLM 调用"""
        kwargs.setdefault("temperature", 0.2)
        hint = self._build_hint(use, **kwargs)
        extra: dict[str, Any] = {}
        if kwargs.get("model"):
            extra["model"] = kwargs["model"]
        if kwargs.get("enable_thinking") is not None:
            extra["enable_thinking"] = kwargs["enable_thinking"]
        if schema is not None:
            extra["response_format"] = {"type": "json_object"}
        req = ChatRequest(messages=messages, hint=hint, extra=extra or None)
        return self._record(use, req)

    def chat(self, req: ChatRequest) -> Any:
        """Gateway 兼容接口：接受 ChatRequest 对象，记录 trace 后交由底层 Gateway

        这是 ``Gateway.chat(ChatRequest)`` 的透明代理，支持插拔式替换。
        """
        return self._record("creative", req)

    def chat_messages(self, messages: list[dict[str, str]], **kwargs: Any) -> Any:
        """消息列表接口：接受 messages 列表，自动构建 ChatRequest

        等价于 ``TracedLLMClient.chat_creative(messages, **kwargs)``，
        为调用方提供无需手动构造 ChatRequest 的便捷入口。
        """
        use = kwargs.pop("use", "creative")
        if use == "utility":
            return self.chat_utility(messages, **kwargs)
        return self.chat_creative(messages, **kwargs)