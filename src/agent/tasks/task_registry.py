"""TaskRegistry：原生 Catalog 工作流注册（Phase 4 重构）

将旧 WorkflowRegistry 中的工作流注册到 llmagent Catalog，
无需 TaskifiedWorkflow / CatalogSetup 适配器层。
"""

from __future__ import annotations

import asyncio
from typing import Any, Optional

from agent.core.engine.workflow_orchestrator import Workflow, WorkflowOrchestrator
from agent.core.engine.workflow_registry import (
    WorkflowRegistry,
    get_workflow,
    list_workflows,
    registry as old_registry,
)


def create_task_spec(
    name: str,
    description: str = "",
    timeout_s: float = 600.0,
) -> Any:
    """创建原生 llmagent TaskSpec

    Args:
        name: 任务名称（对应 workflow id）
        description: 任务描述
        timeout_s: 超时秒数

    Returns:
        llmagent.kernel.task.TaskSpec 实例
    """
    from llmagent.kernel.task import FailurePolicy, TaskKind, TaskSpec

    return TaskSpec(
        name=name,
        kind=TaskKind.WORKFLOW,
        description=description,
        input_schema={
            "type": "object",
            "properties": {
                "context": {"type": "object", "description": "工作流上下文"},
            },
        },
        output_schema={
            "type": "object",
            "properties": {
                "success": {"type": "boolean"},
                "outputs": {"type": "object"},
            },
        },
        failure_policy=FailurePolicy(
            max_retries=1,
            ignore_failure=False,
        ),
        timeout_s=timeout_s,
    )


class TaskRegistry:
    """原生任务注册表：将旧 WorkflowRegistry 注册到 llmagent Catalog

    替代旧的 TaskifiedWorkflow + CatalogSetup 适配器层。
    """

    def __init__(
        self,
        orchestrator: WorkflowOrchestrator,
        catalog: Any | None = None,
    ) -> None:
        self._orchestrator = orchestrator
        self._catalog = catalog
        self._specs: dict[str, Any] = {}

    @property
    def catalog(self) -> Any | None:
        return self._catalog

    def set_catalog(self, catalog: Any) -> None:
        self._catalog = catalog

    def register_all(self) -> int:
        """注册所有旧 WorkflowRegistry 中的工作流到 Catalog

        Returns:
            已注册的工作流数量
        """
        registered = 0
        workflow_ids = list_workflows()

        for wf_id in workflow_ids:
            wf_cls = get_workflow(wf_id)
            if wf_cls is None:
                continue

            # 从类获取描述
            description = (getattr(wf_cls, "__doc__", "") or wf_id).strip()

            # 创建 TaskSpec
            spec = create_task_spec(name=wf_id, description=description)
            self._specs[wf_id] = spec

            # 注册到 Catalog
            if self._catalog is not None:
                try:
                    self._catalog.register(spec)
                    registered += 1
                except Exception:
                    pass

        return registered

    def register_one(self, spec: Any) -> bool:
        """注册单个 TaskSpec 到 Catalog"""
        if self._catalog is None:
            return False
        try:
            self._catalog.register(spec)
            return True
        except Exception:
            return False

    def execute(
        self,
        workflow_id: str,
        ctx: dict[str, Any] | None = None,
    ) -> Any:
        """通过 Catalog 或 WorkflowOrchestrator 执行工作流

        Args:
            workflow_id: 工作流 ID
            ctx: 工作流上下文

        Returns:
            WorkflowResult
        """

        # 尝试通过 Catalog 执行
        if self._catalog is not None:
            try:
                from llmagent.kernel.catalog import Catalog
                from llmagent.kernel.task import TaskRun, TaskStatus

                run = TaskRun(
                    task_id=workflow_id,
                    output={"context": ctx or {}},
                )
                run = self._catalog.execute(run)
                # 转换 TaskRun 为 WorkflowResult
                from agent.core.engine.workflow_orchestrator import WorkflowResult

                return WorkflowResult(
                    success=run.status == TaskStatus.SUCCEEDED,
                    outputs=run.output or {},
                    error=getattr(run, "error", None),
                )
            except Exception:
                pass

        # 回退到旧 WorkflowOrchestrator
        wf_cls = get_workflow(workflow_id)
        if wf_cls is None:
            from agent.core.engine.workflow_orchestrator import WorkflowResult

            return WorkflowResult(
                success=False,
                error=f"工作流 {workflow_id} 未注册",
            )

        # 构造 Workflow 对象并执行
        workflow = Workflow(
            id=workflow_id,
            name=workflow_id,
            steps=[],
        )
        return self._orchestrator.execute(workflow, ctx)


__all__ = [
    "TaskRegistry",
    "create_task_spec",
]