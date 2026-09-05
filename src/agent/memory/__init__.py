"""memory/ 统一记忆层

分层记忆（本包自研，写作流程内使用）：
- ``SemanticMemory``：长期事实记忆（可选向量后端，embed_fn 注入）
- ``ConversationMemory``：会话/决策轨迹（JSONL）
- ``ConsolidatedMemory``：整合快照（Book Bible）
- ``MemoryLayer``：三合一门面（Pipeline/Planner/Editor 注入复用）

llmagent 内核记忆（跨会话长期记忆，SQLite）：
- ``memory_bridge.py``：原生 ``MemoryManager`` 工厂
"""

from agent.memory.base import MemoryEntry, default_scorer, make_scorer
from agent.memory.consolidated import ConsolidatedMemory
from agent.memory.conversation import ConversationEvent, ConversationMemory
from agent.memory.layer import MemoryLayer
from agent.memory.memory_bridge import MemoryManager, create_memory_manager
from agent.memory.semantic import SemanticMemory, build_default_embed_fn

__all__ = [
    "MemoryEntry",
    "default_scorer",
    "make_scorer",
    "SemanticMemory",
    "build_default_embed_fn",
    "ConversationEvent",
    "ConversationMemory",
    "ConsolidatedMemory",
    "MemoryLayer",
    "MemoryManager",
    "create_memory_manager",
]
