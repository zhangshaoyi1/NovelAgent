"""EventBusBridge：原生 llmagent.EventBus 包装（Phase 2 重构）

已移除旧 EventBus 双写逻辑。所有 emit 操作直接到 llmagent.EventBus。
旧 EventBus 仍保留供旧代码路径使用（将在后续阶段清理）。
"""

from __future__ import annotations

from typing import Any, Optional


class EventBusBridge:
    """llmagent.EventBus 包装

    提供与旧 EventBus 兼容的接口（emit_event, get_events），
    内部全部委托给 ``llmagent.kernel.event_bus.EventBus``。
    """

    def __init__(
        self,
        new_bus: Any,  # llmagent.kernel.event_bus.EventBus
    ) -> None:
        self._new = new_bus

    @property
    def new(self) -> Any:
        """获取 llmagent.EventBus 实例"""
        return self._new

    def emit(self, event: Any) -> None:
        """emit：直接写入 llmagent.EventBus"""
        try:
            self._new.append(
                run_id=getattr(event, "correlation_id", ""),
                type=getattr(event, "type", ""),
                payload=getattr(event, "payload", {}),
            )
        except Exception:
            pass

    def emit_event(
        self,
        event_type: str = "",
        correlation_id: str = "",
        payload: dict | None = None,
        context: dict | None = None,
    ) -> Any:
        """便捷方法：创建并分发事件到 llmagent.EventBus"""
        payload_dict = payload or {}
        if context:
            payload_dict["_context"] = context
        try:
            self._new.append(
                run_id=correlation_id,
                type=event_type,
                payload=payload_dict,
            )
        except Exception:
            pass
        return None

    def get_events(self, run_id: str) -> list[dict]:
        """查询事件"""
        try:
            result = self._new.get_events(run_id)
            if result:
                return result
        except Exception:
            pass
        return []

    def close(self) -> None:
        """关闭 EventBus"""
        try:
            self._new.close()
        except Exception:
            pass