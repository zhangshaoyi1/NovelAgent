# AGENTS.md - memory/ 统一记忆层

## 职责

提供统一的记忆管理能力，包括语义记忆、对话记忆和合并记忆。

## 核心模块

| 文件 | 导出 | 作用 |
|------|------|------|
| `base.py` | `MemoryEntry`, `RetrievalScorer`, `default_scorer`, `make_scorer` | 记忆基元与评分器 |
| `semantic.py` | `SemanticMemory` | 语义记忆 |
| `conversation.py` | `ConversationMemory`, `ConversationEvent` | 对话记忆 |
| `consolidated.py` | `ConsolidatedMemory` | 合并记忆 |
| `layer.py` | `MemoryLayer` | 记忆层门面 |
| `memory_bridge.py` | `MemoryManager`, `create_memory_manager` | 原生 llmagent MemoryManager 桥接 |

## 三层架构

1. **SemanticMemory**: 语义级长时记忆
2. **ConversationMemory**: 对话级短时记忆
3. **ConsolidatedMemory**: 合并策略（整合前两者）

## 依赖规则

- 使用原生 llmagent MemoryManager 管理持久化记忆
- 对外暴露 MemoryLayer 门面