"""Auto-orchestrator Plan Adjuster——动态修改后续计划"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Optional

from agent.core.auto_orchestrator.planner import PlanPhase, WritingPlan
from agent.core.event_sourcing.event_bus import EventBus
from agent.core.event_sourcing.event_model import EventType


class AdjustReason(str, Enum):
    """调整原因"""
    QUALITY_ISSUE = "quality_issue"  # 质量问题
    DEVIATION = "deviation"  # 偏离方向
    USER_FEEDBACK = "user_feedback"  # 用户反馈
    RESOURCE_LIMIT = "resource_limit"  # 资源限制
    RHYTHM_ISSUE = "rhythm_issue"  # 节奏问题
    PLOT_STALL = "plot_stall"  # 情节停滞
    OTHER = "other"


@dataclass
class PlanAdjustment:
    """计划调整"""
    reason: AdjustReason = AdjustReason.OTHER
    description: str = ""
    actions: list[str] = field(default_factory=list)
    target_phase: Optional[PlanPhase] = None
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def to_dict(self) -> dict:
        return {
            "reason": self.reason.value,
            "description": self.description,
            "actions": self.actions,
            "target_phase": self.target_phase.value if self.target_phase else None,
            "timestamp": self.timestamp.isoformat(),
        }


class PlanAdjuster:
    """计划调整器——动态调整后续写作计划"""

    def __init__(self) -> None:
        self._adjustments: list[PlanAdjustment] = []
        self._event_bus = EventBus.get_instance()

    @property
    def adjustments(self) -> list[PlanAdjustment]:
        return list(self._adjustments)

    def adjust(
        self,
        plan: WritingPlan,
        reason: AdjustReason,
        description: str,
        actions: list[str],
    ) -> Optional[PlanAdjustment]:
        """调整计划"""
        adjustment = PlanAdjustment(
            reason=reason,
            description=description,
            actions=actions,
        )

        # 应用调整
        for action in actions:
            self._apply_action(plan, action)

        self._adjustments.append(adjustment)

        # 记录调整事件
        self._event_bus.emit_event(
            EventType.ORCHESTRATOR_ADJUST,
            payload={
                "reason": reason.value,
                "description": description,
                "actions": actions,
            },
        )

        return adjustment

    def suggest_adjustments(
        self,
        plan: WritingPlan,
        quality_issues: list[str],
    ) -> list[str]:
        """根据问题自动建议调整"""
        suggestions: list[str] = []

        for issue in quality_issues:
            if "质量" in issue or "不通过" in issue:
                suggestions.append("增加回溯重写轮次")
                suggestions.append("降低单章字数目标")
            elif "节奏" in issue or "平缓" in issue:
                suggestions.append("调整大纲事件密度")
                suggestions.append("增加冲突场景")
            elif "伏笔" in issue or "回收" in issue:
                suggestions.append("增加伏笔回收密度")
                suggestions.append("减少新伏笔埋设")
            elif "风格" in issue or "漂移" in issue:
                suggestions.append("重新注入初期风格指引")
            elif "资源" in issue or "预算" in issue:
                suggestions.append("降低模型档位")
                suggestions.append("减少校验轮次")

        return suggestions

    def _apply_action(self, plan: WritingPlan, action: str) -> None:
        """应用单个调整动作"""
        if "重新规划" in action:
            plan.current_phase = 0
        elif "跳过大纲" in action:
            if PlanPhase.OUTLINE in plan.phases:
                plan.phases.remove(PlanPhase.OUTLINE)
        elif "增加" in action and "章节" in action:
            plan.target_chapters = min(plan.target_chapters + 10, 500)
        elif "减少" in action and "章节" in action:
            plan.target_chapters = max(plan.target_chapters - 10, 5)
        # 更多动作可扩展