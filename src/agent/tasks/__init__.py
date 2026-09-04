"""任务模块（Phase 4 重构）

提供原生 TaskSpec + Executor 模式的工作流注册与执行。
每个任务文件定义一个 TaskSpec 和对应的 Executor 实现，
通过 TaskRegistry 注册到 llmagent Catalog。

与旧 workflows/ 目录共存：
- workflows/: 旧式 @workflow 装饰器 + WorkflowOrchestrator
- tasks/: 新式 TaskSpec + Executor（注册到 Catalog）
"""

from __future__ import annotations

from agent.tasks.task_registry import TaskRegistry, create_task_spec

__all__ = [
    "TaskRegistry",
    "create_task_spec",
]