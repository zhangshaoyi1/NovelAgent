# event_sourcing/ — 事件溯源

## 职责
通过事件日志追踪系统状态变化，支持故障恢复和审计。

## 包含文件
| 文件 | 职责 |
|------|------|
| `event_bus.py` | 事件总线（EventBus - 事件发布/订阅） |
| `event_consumer.py` | 事件消费者（EventConsumer） |
| `event_model.py` | 事件模型定义（Event） |
| `event_store.py` | 事件存储（FileEventStore - 文件持久化） |
| `recovery.py` | 恢复引擎（RecoveryEngine - 事件回放恢复状态） |
| `retry_events.py` | 事件化重试包装器（将 retry 事件通过 EventBus 发出） |

## 依赖规则
- 依赖 base/、client/
- 不依赖业务层

## 被依赖
- 需要故障恢复能力的工作流