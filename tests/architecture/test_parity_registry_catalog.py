"""双跑对等关卡（收敛债④）：WorkflowRegistry 与 llmagent Catalog 单一对等

保证两条路径永不分叉：
1. **注册对等**：`@workflow` 注册的每个工作流，都在 Catalog 中存在同名 TaskSpec
   （描述一致）；Catalog 中的每个 WORKFLOW Task 都能反查到工作流定义——
   Catalog 是 WorkflowRegistry 的派生视图，不允许第二套独立注册。
2. **执行对等**：经 Catalog（TaskSpec → WorkflowTaskExecutor）执行的结果，
   与直接实例化工作流调用 `run()` 的结果，成功/失败语义一致。
"""

from __future__ import annotations

from pathlib import Path

import pytest

import agent.workflows  # noqa: F401 - 触发 @workflow 注册
from agent.core.engine.workflow_orchestrator import WorkflowOrchestrator
from agent.core.engine.workflow_registry import get_workflow, list_workflows
from agent.tasks.task_registry import TaskRegistry, WorkflowTaskExecutor, create_task_spec
from llmagent.kernel.catalog import Catalog
from llmagent.kernel.task import TaskKind


@pytest.fixture(scope="module")
def catalog_with_workflows(tmp_path_factory) -> tuple[Catalog, TaskRegistry]:
    catalog = Catalog()
    orchestrator = WorkflowOrchestrator(tmp_path_factory.mktemp("parity"))
    registry = TaskRegistry(orchestrator, catalog)
    count = registry.register_all()
    assert count > 0, "至少应派生注册一个工作流"
    return catalog, registry


class TestRegistryParity:
    def test_every_workflow_has_catalog_spec(self, catalog_with_workflows):
        catalog, _ = catalog_with_workflows
        specs = {item["name"]: item for item in catalog.list_all()}
        for wf_id in list_workflows():
            assert wf_id in specs, f"工作流 {wf_id} 未派生注册进 Catalog"

    def test_catalog_workflow_specs_resolve_to_definitions(self, catalog_with_workflows):
        catalog, _ = catalog_with_workflows
        wf_ids = set(list_workflows())
        for item in catalog.list_all():
            if item["kind"] == TaskKind.WORKFLOW.value:
                assert item["name"] in wf_ids, (
                    f"Catalog 中的 WORKFLOW Task '{item['name']}' 无对应工作流定义"
                )

    def test_descriptions_match(self, catalog_with_workflows):
        catalog, _ = catalog_with_workflows
        from agent.core.engine.workflow_registry import get_workflow

        specs = {item["name"]: item for item in catalog.list_all()}
        for wf_id in list_workflows():
            wf_cls = get_workflow(wf_id)
            expected = (getattr(wf_cls, "__doc__", "") or wf_id).strip()
            assert specs[wf_id]["description"] == expected

    def test_duplicate_registration_is_idempotent(self, catalog_with_workflows):
        catalog, registry = catalog_with_workflows
        before = len(catalog.list_all())
        registry.register_all()
        assert len(catalog.list_all()) == before, "重复派生注册应幂等"


class TestExecutionParity:
    def test_catalog_path_reports_missing_run_entry(self, catalog_with_workflows):
        """ExportWorkflow 无统一 run()：执行器应如实报错而非伪造成功"""
        catalog, registry = catalog_with_workflows
        wf_id = next(
            w for w in list_workflows()
            if get_workflow(w).__name__ == "ExportWorkflow"
        )
        result = registry.execute(wf_id, {})
        assert result.success is False
        assert "run()" in (result.error or "")

    def test_executor_maps_failure_to_failed_status(self, catalog_with_workflows):
        """执行器对未注册工作流返回 FAILED 且带错误信息"""
        import asyncio
        import uuid

        from llmagent.kernel.task import TaskRun, TaskStatus

        catalog, registry = catalog_with_workflows
        executor = WorkflowTaskExecutor(registry._orchestrator)
        spec = create_task_spec(name="no_such_workflow_xyz")
        run = TaskRun(run_id=uuid.uuid4().hex, spec=spec, output={"context": {}})
        run = asyncio.run(executor.execute(run))
        assert run.status == TaskStatus.FAILED
        assert "未注册" in run.error

    def test_executor_maps_exception_to_failed_status(self, catalog_with_workflows):
        """工作流 run() 抛异常 → FAILED + 错误消息，不向内核泄漏异常"""
        import asyncio
        import uuid

        from llmagent.kernel.task import TaskRun, TaskStatus
        from agent.core.engine.workflow_registry import registry as wf_registry

        catalog, registry = catalog_with_workflows

        class _BoomWorkflow:
            """爆炸工作流（parity 专用）"""

            def __init__(self, project_dir=None):
                pass

            def run(self):
                raise RuntimeError("boom")

        wf_registry.register("parity_boom_workflow", _BoomWorkflow)
        try:
            assert registry.register_all() > 0  # 重新派生注册（含新工作流）
            executor = WorkflowTaskExecutor(registry._orchestrator)
            spec = create_task_spec(name="parity_boom_workflow")
            run = TaskRun(run_id=uuid.uuid4().hex, spec=spec, output={"context": {}})
            run = asyncio.run(executor.execute(run))
            assert run.status == TaskStatus.FAILED
            assert "boom" in run.error
        finally:
            wf_registry._registry.pop("parity_boom_workflow", None)
