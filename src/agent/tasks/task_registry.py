"""TaskRegistry：WorkflowRegistry → llmagent Catalog 的唯一桥（收敛后的单注册体系）

职责边界（2026-09-05 收敛）：
- `WorkflowRegistry`（`@workflow` 装饰器）仍是工作流定义的**唯一真相源**；
- 本模块把定义派生为 llmagent `TaskSpec` 注册进 Catalog，并挂载可执行的
  `WorkflowTaskExecutor`（按 spec.name 反查定义并调用其 `run()`）——
  Catalog 不再是第二套会静默失效的注册表，而是可观测、可执行的派生视图；
- 禁止在别处手工向 Catalog 重复注册 WorkflowRegistry 已有的工作流。
"""

from __future__ import annotations

import asyncio
import inspect
import uuid
from typing import Any

from agent.core.engine.workflow_orchestrator import (
    WorkflowOrchestrator,
    WorkflowResult,
)
from agent.core.engine.workflow_registry import (
    get_workflow,
    list_workflows,
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


class WorkflowTaskExecutor:
    """llmagent Executor 协议实现：按 TaskSpec.name 反查工作流定义并执行。

    绑定一个 WorkflowOrchestrator（提供 project_dir 与检查点语义）；
    工作流实例以 ``wf_cls(project_dir=...)`` 构造（各 M 工作流的首参约定），
    构造失败时退化为无参构造。执行统一走 ``instance.run()``。
    """

    kind: Any = None  # TaskKind.WORKFLOW，延迟赋值避免顶层 import 内核

    def __init__(self, orchestrator: WorkflowOrchestrator) -> None:
        self._orchestrator = orchestrator
        from llmagent.kernel.task import TaskKind

        WorkflowTaskExecutor.kind = TaskKind.WORKFLOW

    async def execute(self, run: Any) -> Any:
        """执行工作流 TaskRun（llmagent Executor 协议）"""
        from llmagent.kernel.task import TaskStatus

        wf_id = run.spec.name
        wf_cls = get_workflow(wf_id)
        if wf_cls is None:
            run.status = TaskStatus.FAILED
            run.error = f"工作流 {wf_id} 未注册"
            return run

        ctx = (run.output or {}).get("context") or {}
        try:
            try:
                instance = wf_cls(project_dir=self._orchestrator.project_dir)
            except TypeError:
                instance = wf_cls()
            run_fn = getattr(instance, "run", None)
            if not callable(run_fn):
                # 部分 @workflow 类（export/import 等）是能力标记，只暴露领域方法，
                # 无统一 run() 入口——如实报错，不伪造成功
                run.status = TaskStatus.FAILED
                run.error = (
                    f"工作流 {wf_id} 无统一 run() 入口，请走对应 CLI 命令调用其领域方法"
                )
                return run
            if ctx:
                result = run_fn(ctx=ctx)
            elif "ctx" in inspect.signature(run_fn).parameters:
                result = run_fn(ctx={})
            else:
                result = run_fn()
        except Exception as e:  # noqa: BLE001 - 失败映射为 TaskStatus.FAILED
            run.status = TaskStatus.FAILED
            run.error = str(e)
            return run

        run.status = TaskStatus.SUCCEEDED if getattr(result, "success", True) else TaskStatus.FAILED
        run.output = {
            "success": getattr(result, "success", True),
            "outputs": getattr(result, "outputs", {}) or {},
        }
        if getattr(result, "error", None):
            run.error = str(result.error)
        return run


class TaskRegistry:
    """WorkflowRegistry → llmagent Catalog 的派生注册表（唯一桥）

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
        """把 WorkflowRegistry 全部工作流派生为 TaskSpec 注册进 Catalog

        同时挂载 WorkflowTaskExecutor（按 kind 挂载，幂等），保证
        Catalog 中的每个工作流 Task 都是**可执行**的，而非仅作展示。

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

            # 创建 TaskSpec 并注册（executor 挂载一次即可，重复挂载幂等）
            spec = create_task_spec(name=wf_id, description=description)
            self._specs[wf_id] = spec

            if self._catalog is not None:
                try:
                    self._catalog.register(spec, WorkflowTaskExecutor)
                    registered += 1
                except Exception:
                    # SchemaGate 拒绝等注册失败：不让派生视图阻断生产链路
                    pass

        return registered

    def register_one(self, spec: Any) -> bool:
        """注册单个 TaskSpec 到 Catalog（用于非 WorkflowRegistry 来源的 Task）"""
        if self._catalog is None:
            return False
        try:
            self._catalog.register(spec, WorkflowTaskExecutor)
            return True
        except Exception:
            return False

    def execute(
        self,
        workflow_id: str,
        ctx: dict[str, Any] | None = None,
    ) -> WorkflowResult:
        """经 Catalog 派生路径执行工作流（真实可执行，不再静默回落）

        流程：Catalog.get(spec) → WorkflowTaskExecutor → 工作流 run()，
        TaskStatus 映射回 WorkflowResult。
        """
        if self._catalog is None:
            return WorkflowResult(
                success=False, error="Catalog 未配置，无法经 Task 路径执行"
            )

        from llmagent.kernel.task import TaskRun, TaskStatus

        spec = self._catalog.get(workflow_id)
        run = TaskRun(
            run_id=uuid.uuid4().hex,
            spec=spec,
            output={"context": ctx or {}},
        )
        executor = WorkflowTaskExecutor(self._orchestrator)
        run = asyncio.run(executor.execute(run))
        return WorkflowResult(
            success=run.status == TaskStatus.SUCCEEDED,
            outputs=run.output.get("outputs", {}),
            error=run.error or None,
        )


__all__ = [
    "TaskRegistry",
    "WorkflowTaskExecutor",
    "create_task_spec",
]
