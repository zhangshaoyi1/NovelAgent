"""工作流模块

每个功能模块（M1/M2/M14/M3/M4/M5 等）对应一个工作流文件。
工作流由 WorkflowOrchestrator 编排。

动态发现机制（DeepSeek Harness 风格）：
- 所有工作流通过 @workflow 装饰器自动注册到 WorkflowRegistry
- 本模块导入所有工作流文件触发装饰器注册
- 消费者通过 get_workflow(id) 查询，无需硬编码注册表
"""

from __future__ import annotations

import importlib
import pkgutil
from pathlib import Path

# 动态导入所有 workflow 模块，触发 @workflow 装饰器注册
_workflows_dir = Path(__file__).parent
_self_module = __name__

for _finder, _name, _ispkg in pkgutil.iter_modules([str(_workflows_dir)]):
    if _name == "__init__":
        continue
    importlib.import_module(f".{_name}", _self_module)


# 提供便捷查询接口（委托到 WorkflowRegistry）
from agent.core.engine.workflow_registry import get_workflow, list_workflows, registry

__all__ = [
    "get_workflow",
    "list_workflows",
    "registry",
]