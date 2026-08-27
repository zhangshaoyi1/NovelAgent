"""多智能体团队（Phase 2）

- PlannerAgent：架构师（产出 Master Plan）
- EditorAgent：主编 / 一致性仲裁
- EvaluatorAgent：评测员（全书「不崩」终审 + 自动回溯修复）

⚠ 旧版文件名（*_agent.py）已废弃，新代码使用新文件名（无后缀）。
"""

from __future__ import annotations

from agent.agents.editor import EditConflict, EditReport, EditorAgent
from agent.agents.evaluator import (
    DimensionResult,
    EvaluatorAgent,
    NovelHealthReport,
    RepairPlan,
)
from agent.agents.planner import (
    Arc,
    CharacterSketch,
    MasterPlan,
    PlannedForeshadow,
    PlannerAgent,
    QualityTargets,
)

__all__ = [
    "PlannerAgent",
    "MasterPlan",
    "Arc",
    "CharacterSketch",
    "PlannedForeshadow",
    "QualityTargets",
    "EditorAgent",
    "EditReport",
    "EditConflict",
    "EvaluatorAgent",
    "NovelHealthReport",
    "DimensionResult",
    "RepairPlan",
]