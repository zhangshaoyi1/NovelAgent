# AGENTS.md - core/event_sourcing/ 事件溯源系统

## 职责

提供可观测性基础设施，所有断网重建/续写/监督/监控能力的基础。

## 核心模块

| 文件 | 导出 | 作用 |
|------|------|------|
| `event_model.py` | `Event`, `EventType`, `EventContext` | 事件模型定义 |
| `event_bus.py` | `EventBus` | 事件总线（发布/订阅） |
| `event_store.py` | `EventStoreProvider`, `FileEventStore`, `EventStoreRegistry` | 事件存储 |
| `event_consumer.py` | `EventConsumer`, `EventConsumerRegistry`, `StateRecoveryConsumer`, `SupervisorConsumer`, `MetricsConsumer` | 事件消费者 |
| `recovery.py` | `RecoveryEngine`, `RecoveryReport` | 恢复引擎 |

## 依赖规则

- 依赖 base、client
- 通过延迟导入避免循环依赖