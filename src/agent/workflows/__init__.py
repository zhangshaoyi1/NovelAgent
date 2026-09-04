"""工作流模块（按域组织）

新版结构（2026-09-05 起为唯一结构，旧平铺文件已删除）：

- ``planning/``   ：写作规划阶段（M1 配置 / M2 讨论 / M3 大纲 / M4 角色）
- ``writing/``    ：章节写作阶段（M5 写章 / M6 调整 / M8 介入模式 / agentic_write）
- ``evaluation/`` ：评测审计阶段（M10-M21：回滚/导出/审计/伏笔/架构/书虫/追读/学习/恢复/评审/拆书）
- ``market/``     ：市场分析（M22 环境部署 / M23 短篇扫榜拆文）
- ``pipeline/``   ：流水线编排（agentic_pipeline / mainline / budget / qa_sync）

工作流通过 ``@workflow`` 装饰器自动注册到 ``WorkflowRegistry``：
- 各子包 ``__init__.py`` 显式导出本包全部模块（触发装饰器注册）；
- 本文件只导入五个子包，不再用 ``pkgutil.iter_modules`` 全量扫描目录——
  旧平铺结构与"后注册覆盖先注册"的静默覆盖风险已随旧文件一并移除；
- 消费者通过 ``get_workflow(id)`` 查询，无需硬编码注册表。
"""

from __future__ import annotations

from agent.core.engine.workflow_registry import get_workflow, list_workflows, registry

# 显式导入五子包（触发各自 __init__ 的模块导出 → @workflow 注册）
from agent.workflows import evaluation, market, pipeline, planning, writing  # noqa: F401

__all__ = [
    "get_workflow",
    "list_workflows",
    "registry",
]
