"""LLM 用量埋点出口（client 层）

分层约定：client 层不依赖 core/workflows，故只暴露可注入 hook；
由上层（``agent.core.event_sourcing.llm_wiring``）注入转发回调，把每次
LLM 调用的 token 用量落到：

- ``<project>/.events/events.jsonl``（EventBus，事件类型 ``llm.usage``）
- ``<project>/.state/llmops/trace.jsonl``（LLMOps TraceStore，若已 set_tracer）

**唯一收口**：``gateway_adapter._GatewayModelProvider.complete()`` —— 所有
``create_gateway()`` 路径（writer/planner/质检/去AI味/摘要）的唯一 provider
级出口。此处"唯一"不是注释而是**可验证断言**（见下）。

## 为什么要 ``usage_epoch()``

2026-09-18 事故：``TracedLLMClient._record`` 与本节 hook 各写一次 TraceSpan，
同一物理调用被记两次（token 逐字相同、时间戳相差 ≈1ms）⇒ ``totals()``
**虚增 100%** ⇒ 假熔断 ⇒ 自动降档 ⇒ boost 层评审维度被削。

修复语义（防复发的是结构而非注释）：

- 本模块每次真正投递通知时把**线程内单调计数** +1（``usage_epoch()`` 可读）。
- ``TracedLLMClient._record`` 在调用前后各读一次 epoch：**若调用期间计数增长，
  说明 provider 级收口已记账，包装层不再补记**（纯代理）。
- 仅当计数未增长（底层不是 Gateway，如测试替身 / 尚未接线；或语义缓存命中而
  未真正调用 provider）时，包装层才补记 —— 兜底而非重复。

## 为什么要 ``llm_use()``

收口在 provider 层，但"creative / utility"是**调用方语义**，provider 层不可见。
包装层在调用前用 ``llm_use(use)`` 声明，本模块在通知 payload 上补 ``use``
字段，使唯一收口仍能写出正确的 ``TraceSpan.use``（``by_use()`` 不降级）。
"""

from __future__ import annotations

import contextvars
import threading
from contextlib import contextmanager
from typing import Any, Iterator

_HOOK: Any = None
_LOCK = threading.Lock()

# 线程内单调计数：只增不减，用于「本次调用是否已被 provider 级收口记账」的判定。
_local = threading.local()

# 调用方语义（creative / utility / chat），随调用栈传递；provider 层不可见，
# 故由包装层在发起调用前设置。contextvar 而非 thread-local：与 asyncio 兼容。
_USE: contextvars.ContextVar[str] = contextvars.ContextVar("novelagent_llm_use", default="chat")


def set_llm_usage_hook(hook: Any) -> None:
    """注入用量事件回调（payload: dict，含 type/ok/provider/model/tokens_in/tokens_out）。

    重复调用仅覆盖，无副作用；传 None 可卸载。
    """
    global _HOOK
    with _LOCK:
        _HOOK = hook


def usage_epoch() -> int:
    """当前线程已投递的用量通知次数（单调不减）。

    供包装层判定「本次调用是否已被 provider 级唯一收口记账」：
    ``before = usage_epoch(); ...调用...; covered = usage_epoch() > before``。
    """
    return int(getattr(_local, "epoch", 0))


def current_use() -> str:
    """当前调用栈声明的用途（未声明时为 ``"chat"``）。"""
    try:
        return str(_USE.get() or "chat")
    except Exception:  # noqa: BLE001 - 取不到用途不影响埋点
        return "chat"


@contextmanager
def llm_use(use: str) -> Iterator[None]:
    """在作用域内声明本次 LLM 调用的用途（``creative`` / ``utility`` / ...）。"""
    token = _USE.set(str(use or "chat"))
    try:
        yield
    finally:
        _USE.reset(token)


def notify_llm_usage(payload: dict[str, Any]) -> None:
    """发出一次 LLM 用量事件（转发失败绝不阻断 LLM 调用）。"""
    with _LOCK:
        hook = _HOOK
    if hook is None:
        return
    # 补调用方语义（provider 层不可见）；显式传入的 use 优先。
    data = dict(payload)
    data.setdefault("use", current_use())
    _local.epoch = int(getattr(_local, "epoch", 0)) + 1
    try:
        hook(data)
    except Exception:  # noqa: BLE001 - 埋点失败不阻断调用
        pass  # noqa: SILENT_DEGRADE


__all__ = [
    "set_llm_usage_hook",
    "notify_llm_usage",
    "usage_epoch",
    "llm_use",
    "current_use",
]
