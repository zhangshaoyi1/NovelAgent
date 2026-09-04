# AGENTS.md - session/ 会话管理

## 职责

原生 llmagent SessionManager 的再导出层，提供会话管理能力。

## 核心模块

| 导出 | 来源 | 作用 |
|------|------|------|
| `SessionManager` | `llmagent.kernel.session` | 会话管理器 |
| `Session` | `llmagent.kernel.session` | 会话聚合根 |
| `SessionContext` | `llmagent.kernel.session` | 会话上下文 |
| `TaskContext` | `llmagent.kernel.session` | 任务上下文 |
| `ChatContext` | `llmagent.kernel.session` | 聊天上下文 |

## 使用方式

```python
from agent.session import SessionManager, Session
```

## 依赖规则

- 直接再导出 `llmagent` 原生模块，不引入新依赖