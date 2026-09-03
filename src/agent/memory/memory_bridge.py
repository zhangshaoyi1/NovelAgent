"""Memory Bridge：直接使用 llmagent MemoryManager（Phase 3 重构）

已移除 MemoryBridge 和 AgentMemoryManager 包装。
业务代码直接使用 ``llmagent.kernel.memory.MemoryManager``。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Optional

from llmagent.kernel.memory import MemoryManager


def create_memory_manager(db_path: str | Path) -> MemoryManager:
    """创建原生 llmagent MemoryManager 实例

    Args:
        db_path: SQLite 数据库路径（通常是 project_dir/.state/memory.db）

    Returns:
        MemoryManager 实例
    """
    from llmagent.kernel.memory import MemoryStore

    store = MemoryStore(db_path)
    return MemoryManager(store=store)


__all__ = [
    "MemoryManager",
    "create_memory_manager",
]