# 事件系统（Event Sourcing）——可观测性基础设施
# 所有断网重建/续写/监督/监控能力的基础

from agent.core.event_sourcing.event_model import Event, EventType, EventContext
from agent.core.event_sourcing.event_bus import EventBus
from agent.core.event_sourcing.event_store import (
    EventStoreProvider,
    FileEventStore,
    EventStoreRegistry,
)
from agent.core.event_sourcing.event_consumer import (
    EventConsumer,
    EventConsumerRegistry,
    StateRecoveryConsumer,
    SupervisorConsumer,
    MetricsConsumer,
)
from agent.core.event_sourcing.recovery import RecoveryEngine, RecoveryReport

__all__ = [
    "Event", "EventType", "EventContext",
    "EventBus",
    "EventStoreProvider", "FileEventStore", "EventStoreRegistry",
    "EventConsumer", "EventConsumerRegistry",
    "StateRecoveryConsumer", "SupervisorConsumer", "MetricsConsumer",
    "RecoveryEngine", "RecoveryReport",
]