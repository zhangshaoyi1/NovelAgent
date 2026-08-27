"""Auto-orchestrator Planner——输入思路，输出完整写作计划"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Optional


class PlanPhase(str, Enum):
    """写作计划阶段"""
    WORLD_BUILDING = "world_building"
    ARCHITECTURE = "architecture"
    OUTLINE = "outline"
    CHARACTER_DESIGN = "character_design"
    WRITING = "writing"
    EVALUATION = "evaluation"


@dataclass
class WritingPlan:
    """完整写作计划"""
    brief: str = ""
    genres: list[str] = field(default_factory=list)
    target_chapters: int = 30
    target_words: int = 100000
    style: str = ""
    phases: list[PlanPhase] = field(default_factory=list)
    current_phase: int = 0
    metadata: dict = field(default_factory=dict)
    decisions: list[dict] = field(default_factory=list)

    @property
    def is_complete(self) -> bool:
        return self.current_phase >= len(self.phases)


class AutoPlanner:
    """自动规划器——根据用户输入生成写作计划"""

    # 默认阶段管线
    DEFAULT_PIPELINE: list[PlanPhase] = [
        PlanPhase.WORLD_BUILDING,
        PlanPhase.ARCHITECTURE,
        PlanPhase.OUTLINE,
        PlanPhase.CHARACTER_DESIGN,
        PlanPhase.WRITING,
        PlanPhase.EVALUATION,
    ]

    def __init__(self) -> None:
        self._plan: Optional[WritingPlan] = None

    @property
    def plan(self) -> Optional[WritingPlan]:
        return self._plan

    def create_plan(
        self,
        brief: str,
        genres: list[str] | None = None,
        target_chapters: int = 30,
        target_words: int = 100000,
        style: str = "",
    ) -> WritingPlan:
        """根据用户输入创建写作计划"""
        self._plan = WritingPlan(
            brief=brief,
            genres=genres or ["xiuxian"],
            target_chapters=target_chapters,
            target_words=target_words,
            style=style,
            phases=list(self.DEFAULT_PIPELINE),
            metadata={
                "created_at": __import__("datetime").datetime.now().isoformat(),
                "estimated_chapters": target_chapters,
                "estimated_words": target_words,
            },
        )
        return self._plan

    def get_current_phase(self) -> Optional[PlanPhase]:
        """获取当前阶段"""
        if self._plan is None:
            return None
        if self._plan.current_phase < len(self._plan.phases):
            return self._plan.phases[self._plan.current_phase]
        return None

    def advance_phase(self) -> bool:
        """推进到下一阶段"""
        if self._plan is None:
            return False
        if self._plan.current_phase < len(self._plan.phases):
            self._plan.current_phase += 1
            return True
        return False

    def record_decision(self, decision: dict) -> None:
        """记录决策"""
        if self._plan is not None:
            self._plan.decisions.append(decision)