"""Auto-orchestrator Executor——按计划逐个调用工作流"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Optional

from agent.core.auto_orchestrator.planner import PlanPhase, WritingPlan

logger = logging.getLogger(__name__)


class ExecutionStatus(str, Enum):
    """执行状态"""
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    SKIPPED = "skipped"


@dataclass
class ExecutionResult:
    """执行结果"""
    phase: PlanPhase = PlanPhase.WRITING
    status: ExecutionStatus = ExecutionStatus.PENDING
    error: Optional[str] = None
    duration: float = 0.0
    output: dict = field(default_factory=dict)
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


class Executor:
    """执行器——按计划执行各阶段工作流"""

    def __init__(self, project_dir: str = "") -> None:
        self.project_dir = project_dir
        self._results: list[ExecutionResult] = []

    @property
    def results(self) -> list[ExecutionResult]:
        return list(self._results)

    def set_project_dir(self, project_dir: str) -> None:
        self.project_dir = project_dir

    async def execute_phase(
        self,
        phase: PlanPhase,
        plan: WritingPlan,
    ) -> ExecutionResult:
        """执行单个阶段"""
        import time

        start = time.time()
        result = ExecutionResult(phase=phase, status=ExecutionStatus.RUNNING)

        try:
            if phase == PlanPhase.WORLD_BUILDING:
                output = await self._run_world_building(plan)
            elif phase == PlanPhase.ARCHITECTURE:
                output = await self._run_architecture(plan)
            elif phase == PlanPhase.OUTLINE:
                output = await self._run_outline(plan)
            elif phase == PlanPhase.CHARACTER_DESIGN:
                output = await self._run_character_design(plan)
            elif phase == PlanPhase.WRITING:
                output = await self._run_writing(plan)
            elif phase == PlanPhase.EVALUATION:
                output = await self._run_evaluation(plan)
            else:
                output = {"status": "unknown_phase"}

            result.status = ExecutionStatus.COMPLETED
            result.output = output
        except Exception as e:
            logger.exception("Phase %s execution failed", phase.value)
            result.status = ExecutionStatus.FAILED
            result.error = str(e)

        result.duration = time.time() - start
        self._results.append(result)
        return result

    async def _run_world_building(self, plan: WritingPlan) -> dict:
        """执行世界观构建"""
        return {"status": "ok", "phase": "world_building"}

    async def _run_architecture(self, plan: WritingPlan) -> dict:
        """执行架构生成"""
        return {"status": "ok", "phase": "architecture"}

    async def _run_outline(self, plan: WritingPlan) -> dict:
        """执行大纲生成"""
        return {"status": "ok", "phase": "outline"}

    async def _run_character_design(self, plan: WritingPlan) -> dict:
        """执行角色设计"""
        return {"status": "ok", "phase": "character_design"}

    async def _run_writing(self, plan: WritingPlan) -> dict:
        """执行写章循环"""
        return {"status": "ok", "phase": "writing"}

    async def _run_evaluation(self, plan: WritingPlan) -> dict:
        """执行终评"""
        return {"status": "ok", "phase": "evaluation"}

    def get_latest_result(self) -> Optional[ExecutionResult]:
        """获取最近一次执行结果"""
        return self._results[-1] if self._results else None