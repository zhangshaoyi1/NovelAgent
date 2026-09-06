"""降级可见化工具（F-1）

项目 G3 哲学允许「失败降级不阻断」，但降级必须**可见**——否则就是静默失效
（deslop/预算/legacy 路径等反复出现的"悄悄不工作"问题，见《两天提交分析与框架根因反思》）。

约定（架构红线，见 tests/architecture/test_degrade_visibility.py）：
1. 每个 except 降级块要么调用 ``degrade()``（或 logger.*），要么在 except 行标注
   ``# noqa: SILENT_DEGRADE`` 显式豁免（存量豁免，增量拦截）；
2. 新增降级点必须用 ``degrade()`` 记录位置、原因与异常，不允许无声 pass。

为什么 warning 级：logging 模块在无任何 handler 时，warning 及以上会经
lastResort 直接落到 stderr——即使上层未配置日志也天然可见。
"""

from __future__ import annotations

import logging
from typing import Optional

_LOGGER = logging.getLogger("agent.degrade")


def degrade(
    where: str,
    reason: str,
    exc: BaseException | None = None,
    *,
    level: int = logging.WARNING,
    event: Optional[str] = None,
    payload: Optional[dict] = None,
) -> None:
    """记录一次降级（统一出口）。

    Args:
        where: 降级发生位置（模块.函数/要点），如 "m5.load_context.rag"
        reason: 降级原因（人类可读），如 "RAG 检索失败，降级为空，不阻断写章"
        exc: 原始异常（可选；附带异常类型信息，DEBUG 级出 traceback）
        level: 日志级别（默认 WARNING；有意静默的降级可传 INFO/DEBUG）
        event: 事件名（可选；接入 event_bus 时使用，如 "degrade.rag"）
        payload: 事件附带数据（可选）
    """
    logger = logging.getLogger(f"agent.degrade.{where}")
    if exc is not None:
        logger.log(level, "[degrade] %s：%s（异常：%r）", where, reason, exc)
        logger.debug("[degrade] %s traceback", where, exc_info=exc)
    else:
        logger.log(level, "[degrade] %s：%s", where, reason)

    if event:
        try:
            from agent.core.event_sourcing.event_bus import EventBus

            EventBus.get_instance().emit_event(
                event,
                correlation_id="degrade",
                payload={"where": where, "reason": reason, **(payload or {})},
            )
        except Exception:  # noqa: BLE001, SILENT_DEGRADE - 事件发送失败不影响降级本身
            _LOGGER.debug("[degrade] 事件 %s 发送失败", event, exc_info=True)  # noqa: SILENT_DEGRADE
