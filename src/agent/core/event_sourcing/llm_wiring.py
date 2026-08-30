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

    EventBus.get_instance().configure(project_dir)

    def _llm_hook(payload: dict) -> None:
        try:
            EventBus.get_instance().emit_event(
                str(payload.get("type", "llm.chat")),
                correlation_id="",
                payload=dict(payload),
                context={"origin": "LLMClient"},
            )
        except Exception:  # noqa: BLE001 - 事件转发失败不阻断 LLM 调用
            pass

    set_llm_event_hook(_llm_hook)