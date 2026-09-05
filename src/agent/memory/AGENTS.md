# AGENTS.md - memory/ 统一记忆层

## 职责

写作流程内的分层记忆（语义/会话/整合）+ llmagent 内核记忆桥接。

## 现有模块

| 文件 | 导出 | 作用 |
|------|------|------|
| `base.py` | `MemoryEntry`, `RetrievalScorer`, `default_scorer`, `make_scorer` | 记忆基元与离线打分器 |
| `semantic.py` | `SemanticMemory`, `build_default_embed_fn` | 长期事实记忆（可选向量后端，失败回退 bigram） |
| `conversation.py` | `ConversationMemory`, `ConversationEvent` | 会话/决策轨迹（JSONL） |
| `consolidated.py` | `ConsolidatedMemory` | 整合快照（Book Bible） |
| `layer.py` | `MemoryLayer` | 三合一门面（Pipeline 默认注入） |
| `memory_bridge.py` | `MemoryManager`, `create_memory_manager` | 原生 llmagent MemoryManager 工厂（跨会话 SQLite 记忆） |

## 依赖规则

- 本包不 import provider SDK / 不直读 API key（红线 R1/R5）；向量函数经 `embed_fn` 注入
- 破坏性变更须同步 `tests/phase2/test_phase2.py`、`tests/phase5/test_phase5.py` 与
  `agentic_pipeline.py` 的默认记忆接线
