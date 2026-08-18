"""LLMOps · 可追踪 LLM 客户端（Phase 3）

轻量包装 ``LLMClient``（或任意鸭子类型 LLM），在每次调用前后记录 ``TraceSpan``
（模型 / 用途 / token / 延迟 / 成本 / 成败）到全局 Tracer。不修改原 ``LLMClient``，
零侵入；未注入 Tracer 时为 ``NullTracer``，零开销。

用法：
    from agent.core.llmops.trace import TraceStore, set_tracer
    from agent.core.llmops.traced_llm import TracedLLMClient
    set_tracer(TraceStore(project_dir))
    llm = TracedLLMClient(LLMClient(), model="creative-strong")
    llm.chat_structured(messages, Schema, use="creative")  # 自动记录 span
"""

from __future__ import annotations

import time
from typing import Any

from agent.core.llmops.trace import TraceSpan, get_tracer


def _extract_tokens(resp: Any) -> tuple[int, int]:
    """从响应对象尽力提取 token（支持 OpenAI 风格 usage；否则按字符粗估）。"""
    usage = getattr(resp, "usage", None)
    if isinstance(usage, dict):
        return int(usage.get("prompt_tokens", 0)), int(usage.get("completion_tokens", 0))
    # 对象风格
    pt = getattr(usage, "prompt_tokens", None)
    ct = getattr(usage, "completion_tokens", None)
    if pt is not None and ct is not None:
        return int(pt), int(ct)
    text = getattr(resp, "text", "") or ""
    est = max(1, len(str(text)) // 2)
    return est // 3, est // 3  # 粗估：输入略少、输出略多


class TracedLLMClient:
    """可追踪 LLM 包装。

    Args:
        llm: 被包装的 LLM（需有 chat_structured / chat_utility / chat 等方法）。
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

    def _wrap(self, use: str, fn_name: str, *args: Any, **kwargs: Any) -> Any:
        tracer = self._tracer or get_tracer()
        t0 = time.time()
        ok = True
        err = ""
        resp = None
        try:
            resp = getattr(self._llm, fn_name)(*args, **kwargs)
        except Exception as e:  # noqa: BLE001
            ok = False
            err = str(e)
            resp = None
        dt_ms = (time.time() - t0) * 1000.0
        tin, tout = (0, 0)
        if resp is not None:
            tin, tout = _extract_tokens(resp)
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
            )
        )
        if not ok:
            raise  # 原样抛出，保持调用方语义
        return resp

    # 镜像 LLMClient 常用方法
    def chat_structured(self, messages: Any, schema: Any = None, *, use: str = "utility",
                        **kwargs: Any) -> Any:
        return self._wrap(use, "chat_structured", messages, schema, **kwargs)

    def chat_utility(self, messages: Any, **kwargs: Any) -> Any:
        return self._wrap("utility", "chat_utility", messages, **kwargs)

    def chat(self, messages: Any, **kwargs: Any) -> Any:
        return self._wrap(kwargs.pop("use", "creative"), "chat", messages, **kwargs)
