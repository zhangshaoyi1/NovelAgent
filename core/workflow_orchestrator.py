"""工作流编排器

职责：把每个功能模块拆为 step 序列，按序执行，支持重试、暂停、恢复。

支持：
    - 检查点：每步完成后写 .state/checkpoint.json，崩溃可恢复
    - 重试：单步失败按 M18 策略重试
    - 嵌套：一个 workflow 可调用另一个
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable


@dataclass
class Step:
    """工作流步骤"""

    id: str
    name: str
    run: Callable[[dict[str, Any]], Any]
    on_error: Callable[[Exception, dict[str, Any]], None] | None = None
    retry: int = 0  # 重试次数（M18）


@dataclass
class Workflow:
    """工作流定义"""

    id: str
    name: str
    steps: list[Step]
    guard: Callable[[], bool] | None = None  # 前置门禁
    on_save: Callable[[dict[str, Any]], None] | None = None  # 每步后持久化检查点

    def add_step(self, step: Step) -> None:
        self.steps.append(step)


@dataclass
class WorkflowResult:
    """工作流执行结果"""

    success: bool
    outputs: dict[str, Any] = field(default_factory=dict)
    error: str | None = None
    failed_step: str | None = None


class WorkflowOrchestrator:
    """工作流编排器"""

    def __init__(self, project_dir: Path) -> None:
        self.project_dir = project_dir
        self.checkpoint_file = project_dir / ".state" / "checkpoint.json"

    def execute(self, workflow: Workflow, ctx: dict[str, Any] | None = None) -> WorkflowResult:
        """执行工作流

        Args:
            workflow: 工作流定义
            ctx: 上下文（步骤间共享数据）

        Returns:
            WorkflowResult
        """
        ctx = ctx or {}

        # 前置门禁
        if workflow.guard is not None and not workflow.guard():
            return WorkflowResult(
                success=False,
                error=f"工作流 {workflow.id} 门禁未通过",
            )

        # TODO: 检查点恢复逻辑
        outputs: dict[str, Any] = {}

        for step in workflow.steps:
            try:
                result = self._run_step_with_retry(step, ctx)
                outputs[step.id] = result
                ctx[f"_last_{step.id}"] = result
                # 持久化检查点
                if workflow.on_save:
                    workflow.on_save(ctx)
                self._save_checkpoint(workflow.id, step.id, ctx)
            except Exception as e:
                if step.on_error:
                    step.on_error(e, ctx)
                return WorkflowResult(
                    success=False,
                    outputs=outputs,
                    error=str(e),
                    failed_step=step.id,
                )

        return WorkflowResult(success=True, outputs=outputs)

    def _run_step_with_retry(self, step: Step, ctx: dict[str, Any]) -> Any:
        """按 M18 策略重试执行步骤"""
        # TODO: 指数退避
        last_exc: Exception | None = None
        for attempt in range(step.retry + 1):
            try:
                return step.run(ctx)
            except Exception as e:
                last_exc = e
        raise last_exc  # type: ignore[misc]

    def _save_checkpoint(self, workflow_id: str, step_id: str, ctx: dict[str, Any]) -> None:
        """保存检查点"""
        self.checkpoint_file.parent.mkdir(parents=True, exist_ok=True)
        data = {
            "workflow_id": workflow_id,
            "last_step": step_id,
            # 不保存不可序列化的对象
            "ctx_keys": [k for k, v in ctx.items() if isinstance(v, (str, int, float, dict, list))],
        }
        self.checkpoint_file.write_text(
            json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
        )
