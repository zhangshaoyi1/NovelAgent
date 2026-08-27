"""Workflow 注册表（DeepSeek Harness 风格）

工作流自动发现与注册机制：
- 每个工作流文件放在 `workflows/` 目录下
- 在文件中使用 `@workflow` 装饰器标注工作流类
- 导入时自动注册到全局 registry
- 消费者通过 `get(id)` 按 id 获取
- 新增工作流只需添加文件 + 装饰器，无需修改中心字典

设计对齐 cli command 模式：
- 装饰器完成注册，无需手动维护 `WORKFLOW_REGISTRY`
- 命令与工作流一一对应，注册表作为唯一真相源
"""

from __future__ import annotations

from typing import Any, Callable, Type, Optional

from agent.core.registry import BaseRegistry


class Workflow:
    """工作流接口标记（编排层只需要知道这是一个工作流）

    实际工作流实现是一个类，具有 `run(ctx)` 方法。
    """
    pass


# 类型别名
WorkflowType = Type[Any]


class WorkflowRegistry(BaseRegistry[WorkflowType]):
    """工作流注册表（全局单例）"""

    def __init__(self) -> None:
        super().__init__()


# 全局实例
registry = WorkflowRegistry()


def workflow(
    workflow_id: str | None = None,
) -> Callable[[WorkflowType], WorkflowType]:
    """工作流注册装饰器

    用法::

        from agent.core.workflow_registry import workflow

        @workflow("m1_config")
        class M1ConfigWorkflow:
            def run(self, ctx):
                ...
    """

    def decorator(cls: WorkflowType) -> WorkflowType:
        name = workflow_id or cls.__name__.lower()
        registry.register(name, cls)
        return cls

    return decorator


def get_workflow(workflow_id: str) -> Optional[WorkflowType]:
    """按 id 获取工作流类"""
    return registry.get(workflow_id)


def list_workflows() -> list[str]:
    """列出所有已注册工作流 id"""
    return registry.list()
