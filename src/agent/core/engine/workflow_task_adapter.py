"""Workflow → Task 适配器（Phase 4 重构 - 已迁移至 tasks/）

已移除 TaskifiedWorkflow 和 CatalogSetup 适配器类。
请使用 ``agent.tasks.TaskRegistry`` 替代。
"""

from __future__ import annotations

from agent.tasks.task_registry import TaskRegistry, create_task_spec


__all__ = [
    "TaskRegistry",
    "create_task_spec",
]