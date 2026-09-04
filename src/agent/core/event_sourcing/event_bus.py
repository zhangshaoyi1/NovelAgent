"""事件总线——同步分发事件到所有已注册的 Provider 和 Consumer"""

from __future__ import annotations

import logging
from typing import Optional

from agent.core.event_sourcing.event_model import Event, EventType, Snapshot
from agent.core.event_sourcing.event_store import (
    EventStoreProvider,
    EventStoreRegistry,
    FileEventStore,
)
from agent.core.event_sourcing.event_consumer import EventConsumerRegistry

logger = logging.getLogger(__name__)


class EventBus:
    """事件总线——单例，同步分发事件"""

    _instance: Optional[EventBus] = None

    def __init__(self) -> None:
        self._store_registry = EventStoreRegistry()
        self._consumer_registry = EventConsumerRegistry()
        self._enabled = True

    @classmethod
    def get_instance(cls) -> EventBus:
        if cls._instance is None:
            cls._instance = EventBus()
        return cls._instance

    @classmethod
    def reset_instance(cls) -> None:
        """重置单例（测试用）"""
        cls._instance = None

    def configure(
        self,
        project_dir: str,
        store_name: str = "file",
    ) -> None:
        """配置事件总线——注册默认 FileEventStore"""
        file_store = FileEventStore(project_dir)
        self._store_registry.register(store_name, file_store)

    @property
    def store_registry(self) -> EventStoreRegistry:
        return self._store_registry

    @property
    def consumer_registry(self) -> EventConsumerRegistry:
        return self._consumer_registry

    def emit(self, event: Event) -> None:
        """同步分发事件到所有 Provider 和 Consumer"""
        if not self._enabled:
            return

        # 持久化
        for provider in self._store_registry.all():
            try:
                provider.append(event)
            except Exception:
                logger.exception("EventStore append failed")

        # 消费
        for consumer in self._consumer_registry.all():
            try:
                if consumer.handles(event.type):
                    consumer.on_event(event)
            except Exception:
                logger.exception("EventConsumer on_event failed")

    def emit_event(
        self,
        event_type: EventType | str,
        correlation_id: str = "",
        payload: dict | None = None,
        context: dict | None = None,
    ) -> Event:
        """便捷方法：创建并分发事件"""
        event = Event(
            type=event_type if isinstance(event_type, str) else event_type.value,
            correlation_id=correlation_id,
            payload=payload or {},
            context=context or {},
        )
        self.emit(event)
        return event

    def save_snapshot(self, snapshot: Snapshot) -> None:
        """保存快照"""
        for provider in self._store_registry.all():
            try:
                provider.save_snapshot(snapshot)
            except Exception:
                logger.exception("Snapshot save failed")

    def load_snapshot(self, correlation_id: str) -> Optional[Snapshot]:
        """加载最近快照"""
        for provider in self._store_registry.all():
            try:
                snap = provider.load_latest_snapshot(correlation_id)
                if snap:
                    return snap
            except Exception:
                continue
        return None

    def set_enabled(self, enabled: bool) -> None:
        self._enabled = enabled