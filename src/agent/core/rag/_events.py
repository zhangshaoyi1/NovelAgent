"""RAG 召回/索引事件可注入 hook（对齐 client 的 set_llm_event_hook 模式）

设计要点：
- core.rag 只持有可调用对象引用，**不 import 上层 event_sourcing**，保持
  core.rag 不依赖 event_sourcing（依赖方向：event_sourcing 是基础设施，仅由
  core.event_sourcing.llm_wiring 注 direction）。
- 由 ``agent.core.event_sourcing.llm_wiring.wire_llm_event_hook`` 注入转发到
  EventBus 的回调，使每次 RAG ``recall / index`` 调用都落到
  ``<project>/.events/events.jsonl``。
- 默认 None 零开销；事件转发失败绝不阻断检索/索引。
"""

from __future__ import annotations

from typing import Any

_RAG_EVENT_HOOK: Any = None


def set_rag_event_hook(hook: Any) -> None:
    """注入 RAG 调用事件回调（payload: dict，含 type/ok/query/hits/latency_ms 等）。"""
    global _RAG_EVENT_HOOK
    _RAG_EVENT_HOOK = hook


def notify_rag_event(payload: dict[str, Any]) -> None:
    hook = _RAG_EVENT_HOOK
    if hook is not None:
        try:
            hook(payload)
        except Exception:  # noqa: BLE001 - 事件转发失败不阻断检索/索引
            pass