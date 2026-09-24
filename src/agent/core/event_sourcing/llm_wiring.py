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
                pass  # noqa: SILENT_DEGRADE
        return _hook

    set_llm_event_hook(_hook_factory("LLMClient"))
    set_rag_event_hook(_hook_factory("RAG"))
    # LLMOps 用量埋点：gateway_adapter 唯一收口 → EventBus + TraceStore
    set_llm_usage_hook(_usage_hook_factory())


def wire_prompt_capture(project_dir: str) -> None:
    """按当前书的 per-book 开关装配「提示词全文捕获」。

    - 开启：注入 client 层捕获 flag（后续用量事件附带 prompt/response 全文），
      并在 EventBus 上注册 ``PromptFileConsumer``（落 prompt → prompts.jsonl）。
    - 关闭：默认 no-op（不附带全文、不注册消费者）。

    须在 ``wire_llm_event_hook`` 之后（或其本身就含 EventBus.configure）调用，
    且位于进程内「当前激活书籍」的 AgentService 初始化路径上，使 flag 与
    consumer 与当前书对齐。若后续切换书籍，需再次按新书调用本函数覆盖。
    """
    from agent.client.llm_usage import set_llm_capture_prompts

    # per-book 开关（core 层读取；client 层不依赖 core，故在此中转）。
    from agent.core.event_sourcing.prompt_capture import (
        PromptFileConsumer,
        capture_enabled,
    )

    enabled = capture_enabled(project_dir)
    if not enabled:
        # 关闭时显式复位 flag 并卸载消费者（避免切换书籍后残留旧状态）。
        set_llm_capture_prompts(False)
        EventBus.get_instance().consumer_registry.unregister("prompt_file")
        return
    set_llm_capture_prompts(True)
    EventBus.get_instance().consumer_registry.register(
        PromptFileConsumer(project_dir)
    )


def _usage_hook_factory():
    """用量 hook：llm.usage 事件落 EventBus + TraceSpan 落全局 TraceStore。

    这是 **TraceStore 的唯一写入收口**（2026-09-18 记账双收口事故后确立）：
    包装层（``TracedLLMClient``）不再无条件写 span，只在「本次调用未触发
    本 hook」时兜底补记。判据见 ``agent.client.llm_usage.usage_epoch``，
    红线见 ``tests/test_trace_single_sink.py``。
    """

    def _hook(payload: dict) -> bool:
        """投递一次用量事件。

        Returns:
            **TraceStore 是否真的写入了 span**。该返回值经
            ``llm_usage.notify_llm_usage`` 决定 ``usage_epoch()`` 是否增长 ——
            包装层据此判定「是否需要兜底补记」。若本 hook 吞掉写盘异常却
            返回 True，包装层会误判「已记账」而放弃补记 ⇒ 静默漏记
            （2026-09-19 实证：`TraceStore.record` 抛 OSError ⇒ 0 span、无日志）。
        """
        # 1) EventBus（.events/events.jsonl）
        try:
            EventBus.get_instance().emit_event(
                str(payload.get("type", "llm.usage")),
                correlation_id="",
                payload=dict(payload),
                context={"origin": "Gateway"},
            )
        except Exception:  # noqa: BLE001 - 事件转发失败不阻断调用
            pass  # noqa: SILENT_DEGRADE
        # 2) LLMOps TraceStore（未 set_tracer 时为 NullTracer，零开销跳过）
        try:
            from agent.core.llmops.trace import NullTracer, TraceSpan, get_tracer

            tracer = get_tracer()
            if isinstance(tracer, NullTracer):
                # 未装配 TraceStore：本次**没有**写入 span ⇒ 报 False，
                # 由包装层兜底补记（保证「有调用必有 span」）。
                return False
            tracer.record(
                TraceSpan(
                    model=str(payload.get("model", "")),
                    # use 由调用方声明（包装层经 llm_use 传递）；缺省 chat。
                    # 此前硬编码 "chat" 导致 creative/utility 语义在收口处丢失。
                    use=str(payload.get("use") or "chat"),
                    tokens_in=int(payload.get("tokens_in", 0) or 0),
                    tokens_out=int(payload.get("tokens_out", 0) or 0),
                    tokens_cached=int(payload.get("tokens_cached", 0) or 0),
                    latency_ms=float(payload.get("latency_ms", 0) or 0),
                    ok=bool(payload.get("ok", True)),
                    error=str(payload.get("error", "")),
                    meta={
                        "provider": str(payload.get("provider", "")),
                        # 缓存命中留痕（HA-Eval L1）：provider 层通常取不到，
                        # 缺省 False；包装层兜底补记时会带真实值。
                        "cache_hit": bool(payload.get("cache_hit", False)),
                        "cache_key": str(payload.get("cache_key", "") or ""),
                    },
                )
            )
        except Exception:  # noqa: BLE001 - 追踪失败不阻断调用
            # 写盘失败（磁盘满/权限/文件锁）：**必须报 False**，不得让上层
            # 误以为已记账。业务调用仍不受影响（异常不外抛）。
            return False
        return True

    return _hook