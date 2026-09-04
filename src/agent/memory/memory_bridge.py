"""MemoryManager 工厂：创建原生 llmagent MemoryManager 实例

提供便捷的 ``create_memory_manager()`` 工厂函数。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

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