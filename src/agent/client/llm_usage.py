"""LLM 用量埋点出口（client 层）

分层约定：client 层不依赖 core/workflows，故只暴露一个可注入 hook；
由上层（``agent.core.event_sourcing.llm_wiring``）注入转发回调，把每次
LLM 调用的 token 用量落到：

- ``<project>/.events/events.jsonl``（EventBus，事件类型 ``llm.usage``）
- ``<project>/.state/llmops/trace.jsonl``（LLMOps TraceStore，若已 set_tracer）

埋点位置：``gateway_adapter._GatewayModelProvider.complete()`` —— 所有
``create_gateway()`` 路径（writer/planner/质检/去AI味/摘要）的唯一收口。
"""

from __future__ import annotations

import threading
from typing import Any

_HOOK: Any = None
_LOCK = threading.Lock()


def set_llm_usage_hook(hook: Any) -> None:
    """注入用量事件回调（payload: dict，含 type/ok/provider/model/tokens_in/tokens_out）。

    重复调用仅覆盖，无副作用；传 None 可卸载。
    """
    global _HOOK
    with _LOCK:
        _HOOK = hook


def notify_llm_usage(payload: dict[str, Any]) -> None:
    """发出一次 LLM 用量事件（转发失败绝不阻断 LLM 调用）。"""
    with _LOCK:
        hook = _HOOK
    if hook is None:
        return
    try:
        hook(dict(payload))
    except Exception:  # noqa: BLE001 - 埋点失败不阻断调用
        pass


__all__ = ["set_llm_usage_hook", "notify_llm_usage"]
