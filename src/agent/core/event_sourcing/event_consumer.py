"""事件消费者——消费事件以实现各种功能"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from typing import Optional

from agent.core.event_sourcing.event_model import Event, EventType

logger = logging.getLogger(__name__)


class EventConsumer(ABC):
    """事件消费者抽象基类"""

    @abstractmethod
    def handles(self, event_type: str) -> bool:
        """是否处理该事件类型"""
        ...

    @abstractmethod
    def on_event(self, event: Event) -> None:
        """处理事件"""
        ...

    @property
    @abstractmethod
    def name(self) -> str:
        """消费者名称"""
        ...


class EventConsumerRegistry:
    """事件消费者注册表"""

    def __init__(self) -> None:
        self._consumers: dict[str, EventConsumer] = {}

    def register(self, consumer: EventConsumer) -> None:
        self._consumers[consumer.name] = consumer

    def unregister(self, name: str) -> None:
        self._consumers.pop(name, None)

    def get(self, name: str) -> Optional[EventConsumer]:
        return self._consumers.get(name)

    def all(self) -> list[EventConsumer]:
        return list(self._consumers.values())


class StateRecoveryConsumer(EventConsumer):
    """状态重建消费者——监听事件维护状态重建所需信息"""

    def __init__(self) -> None:
        self._last_state: dict = {}
        self._last_progress: dict = {}
        self._event_count: int = 0

    @property
    def name(self) -> str:
        return "state_recovery"

    def handles(self, event_type: str) -> bool:
        return event_type in (
            EventType.STATE_TRANSITION.value,
            EventType.STATE_SNAPSHOT.value,
            EventType.WORKFLOW_STARTED.value,
            EventType.WORKFLOW_COMPLETED.value,
        )

    def on_event(self, event: Event) -> None:
        self._event_count += 1
        if event.type == EventType.STATE_TRANSITION.value:
            self._last_state = event.payload.get("state", {})
        elif event.type == EventType.STATE_SNAPSHOT.value:
            self._last_progress = event.payload.get("progress", {})

    @property
    def event_count(self) -> int:
        return self._event_count

    @property
    def last_state(self) -> dict:
        return self._last_state


class SupervisorConsumer(EventConsumer):
    """监督消费者——转发事件到监督引擎"""

    def __init__(self) -> None:
        self._events: list[Event] = []

    @property
    def name(self) -> str:
        return "supervisor"

    def handles(self, event_type: str) -> bool:
        return event_type in (
            EventType.CHAPTER_WRITTEN.value,
            EventType.WORKFLOW_COMPLETED.value,
            EventType.SUPERVISOR_CHECK.value,
        )

    def on_event(self, event: Event) -> None:
        self._events.append(event)

    def get_pending_events(self) -> list[Event]:
        """获取待处理事件并清空"""
        events = list(self._events)
        self._events.clear()
        return events


class MetricsConsumer(EventConsumer):
    """统计消费者——收集事件统计信息"""

    def __init__(self) -> None:
        self._counts: dict[str, int] = {}

    @property
    def name(self) -> str:
        return "metrics"

    def handles(self, event_type: str) -> bool:
        return True  # 处理所有事件

    def on_event(self, event: Event) -> None:
        self._counts[event.type] = self._counts.get(event.type, 0) + 1

    def get_counts(self) -> dict[str, int]:
        return dict(self._counts)

    def get_count(self, event_type: str) -> int:
        return self._counts.get(event_type, 0)