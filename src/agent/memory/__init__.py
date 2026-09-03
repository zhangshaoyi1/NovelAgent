"""Memory Layer（Phase 2）—— 统一记忆层

对外暴露：MemoryLayer（门面）、SemanticMemory / ConversationMemory /
ConsolidatedMemory（三层）、MemoryEntry / ConversationEvent / base（基元）。

Phase 3 重构：
- MemoryBridge / AgentMemoryManager 已移除，直接使用 llmagent MemoryManager
"""

from __future__ import annotations

from agent.memory.base import (
    MemoryEntry,
    RetrievalScorer,
    default_scorer,
    make_scorer,
)
from agent.memory.consolidated import ConsolidatedMemory
from agent.memory.conversation import ConversationEvent, ConversationMemory
from agent.memory.layer import MemoryLayer
from agent.memory.memory_bridge import MemoryManager, create_memory_manager
from agent.memory.semantic import SemanticMemory

__all__ = [
    "MemoryLayer",
    "SemanticMemory",
    "ConversationMemory",
    "ConsolidatedMemory",
    "MemoryEntry",
    "ConversationEvent",
    "RetrievalScorer",
    "default_scorer",
    "make_scorer",
    "MemoryManager",
    "create_memory_manager",
]