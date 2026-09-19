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
    """当前线程**已成功投递**的用量通知次数（单调不减）。

    供包装层判定「本次调用是否已被 provider 级唯一收口**记账**」：
    ``before = usage_epoch(); ...调用...; covered = usage_epoch() > before``。

    ★ 语义红线（2026-09-19 修正）：「计数增长」必须等价于「记账真的发生了」。
    修复前本函数在调用 hook **之前**就 +1，而 hook 内部又 `except: pass` 吞掉
    TraceStore 写盘异常 ⇒ 计数增长 ≠ 记账成功 ⇒ 包装层误判「已被覆盖」而放弃
    兜底补记 ⇒ **静默漏记（0 span，无日志）**。现改为 hook 成功返回后才 +1：
    记账失败 ⇒ 计数不增 ⇒ 包装层兜底补记（纪律 #1 失败显性化 / #14 记账后置为前置）。
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
    """发出一次 LLM 用量事件（转发失败绝不阻断 LLM 调用）。

    ★ 计数时机（2026-09-19 修正）：**仅当 hook 真正完成投递后**才 ``epoch += 1``。
    此前是「先 +1、后调 hook」，而 hook 内部会吞掉 TraceStore 写盘异常 ⇒
    计数增长被下游（``TracedLLMClient._record``）误读为「已记账」⇒ 放弃兜底补记
    ⇒ 该次调用 **0 span 且无任何告警**（探针实证：真 provider 调用 +
    ``TraceStore.record`` 抛 OSError ⇒ 落盘 0 条，业务正常返回）。

    语义：``usage_epoch()`` 是「本线程成功记账次数」，不是「通知发起次数」。
    无 hook 或 hook 抛异常 ⇒ 不增 ⇒ 包装层按兜底路径补记，保证
    「有调用必有 span」（两种失真方向同时封堵：虚增与漏记）。
    """
    with _LOCK:
        hook = _HOOK
    if hook is None:
        return
    # 补调用方语义（provider 层不可见）；显式传入的 use 优先。
    data = dict(payload)
    data.setdefault("use", current_use())
    try:
        recorded = hook(data)
    except Exception:  # noqa: BLE001 - 埋点失败不阻断调用
        # 失败不计数：让包装层看到「未记账」从而兜底补记（不得静默漏记）。
        return None
    # hook 返回值的三态约定（**显式哨兵，不靠猜**）：
    #   True  ⇒ 已写入 span（唯一收口生效）            ⇒ 计数
    #   False ⇒ 明确未写入（未装配 TraceStore / 写盘失败）⇒ **不计数**，包装层兜底补记
    #   None  ⇒ 旧式 hook（无返回值）：按**已记账**处理 ⇒ 计数（维持既有行为，
    #           避免把「旧 hook 已记账」误判为漏记而**把虚增改回来**）
    if recorded is False:
        return None
    _local.epoch = int(getattr(_local, "epoch", 0)) + 1
    return None


__all__ = [
    "set_llm_usage_hook",
    "notify_llm_usage",
    "usage_epoch",
    "llm_use",
    "current_use",
]
