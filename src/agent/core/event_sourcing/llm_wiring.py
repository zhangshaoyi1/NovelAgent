"""LLM 调用事件统一接线（供所有会调用 LLM 的独立入口复用）

把 client 层的 LLM 调用事件 hook 转发到统一 ``EventBus``，使每次
``LLMClient.chat / embed`` 调用都落到 ``<project>/.events/events.jsonl``。

依赖方向：本模块位于 core.event_sourcing，可依赖 client 与 event_sourcing，
但 **client 层不得 import 本模块**（client 只暴露 ``set_llm_event_hook``）。
hook 的转发实现必须放在 core / workflows 层，绝不放入 agent/client/*。

用法（service / cli 等独立入口在入口处调用一次）::

    from agent.core.event_sourcing.llm_wiring import wire_llm_event_hook
    wire_llm_event_hook(project_dir)

与 ``agentic_pipeline`` 里手写的 hook 等价；重复调用仅覆盖同名 hook，无副作用。
"""

from __future__ import annotations

from agent.core.event_sourcing.event_bus import EventBus


def wire_llm_event_hook(project_dir: str) -> None:
    """接线：配置 EventBus 指向 project_dir，并注入转发到 EventBus 的 LLM hook。

    Args:
        project_dir: 小说项目目录，事件落盘到 <project_dir>/.events/events.jsonl。
    """
    # client 层不依赖 core，故在此延迟导入 set_llm_event_hook，避免循环依赖。
    from agent.client import set_llm_event_hook
    # core.rag 不依赖 event_sourcing，故在此延迟导入 set_rag_event_hook。
    from agent.core.rag._events import set_rag_event_hook
    # 用量埋点出口（client 层，无循环依赖）。
    from agent.client.llm_usage import set_llm_usage_hook

    EventBus.get_instance().configure(project_dir)

    def _hook_factory(origin: str):
        def _hook(payload: dict) -> None:
            try:
                EventBus.get_instance().emit_event(
                    str(payload.get("type", "llm.chat")),
                    correlation_id="",
                    payload=dict(payload),
                    context={"origin": origin},
                )
            except Exception:  # noqa: BLE001 - 事件转发失败不阻断调用
                pass
        return _hook

    set_llm_event_hook(_hook_factory("LLMClient"))
    set_rag_event_hook(_hook_factory("RAG"))
    # LLMOps 用量埋点：gateway_adapter 唯一收口 → EventBus + TraceStore
    set_llm_usage_hook(_usage_hook_factory())


def _usage_hook_factory():
    """用量 hook：llm.usage 事件落 EventBus + TraceSpan 落全局 TraceStore。"""

    def _hook(payload: dict) -> None:
        # 1) EventBus（.events/events.jsonl）
        try:
            EventBus.get_instance().emit_event(
                str(payload.get("type", "llm.usage")),
                correlation_id="",
                payload=dict(payload),
                context={"origin": "Gateway"},
            )
        except Exception:  # noqa: BLE001 - 事件转发失败不阻断调用
            pass
        # 2) LLMOps TraceStore（未 set_tracer 时为 NullTracer，零开销跳过）
        try:
            from agent.core.llmops.trace import NullTracer, TraceSpan, get_tracer

            tracer = get_tracer()
            if isinstance(tracer, NullTracer):
                return
            tracer.record(
                TraceSpan(
                    model=str(payload.get("model", "")),
                    use="chat",
                    tokens_in=int(payload.get("tokens_in", 0) or 0),
                    tokens_out=int(payload.get("tokens_out", 0) or 0),
                    tokens_cached=int(payload.get("tokens_cached", 0) or 0),
                    latency_ms=float(payload.get("latency_ms", 0) or 0),
                    ok=bool(payload.get("ok", True)),
                    error=str(payload.get("error", "")),
                    meta={"provider": str(payload.get("provider", ""))},
                )
            )
        except Exception:  # noqa: BLE001 - 追踪失败不阻断调用
            pass

    return _hook