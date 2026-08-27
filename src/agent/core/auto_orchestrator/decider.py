"""Auto-orchestrator Decider——LLM 自主决策引擎"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Optional


class InterventionMode(str, Enum):
    """介入档位"""
    AUTO = "auto"  # 全自主，默认
    LIGHT = "light"  # 关键节点暂停
    HEAVY = "heavy"  # 每章前问方向


@dataclass
class Decision:
    """决策记录"""
    issue: str = ""
    resolution: str = ""
    reasoning: str = ""
    options: list[str] = field(default_factory=list)
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def to_dict(self) -> dict:
        return {
            "issue": self.issue,
            "resolution": self.resolution,
            "reasoning": self.reasoning,
            "options": self.options,
            "timestamp": self.timestamp.isoformat(),
        }


class ConflictResolver:
    """冲突解决器——LLM 自主决策冲突"""

    def __init__(self) -> None:
        self._decisions: list[Decision] = []

    @property
    def decisions(self) -> list[Decision]:
        return list(self._decisions)

    def resolve(
        self,
        issue: str,
        options: list[str],
        context: dict[str, Any] | None = None,
    ) -> Decision:
        """LLM 自主决策（模拟实现，实际使用 LLM 调用）

        关键决策原则：
        1. 优先选择推进情节的选项
        2. 保持设定一致性
        3. 记录决策理由供后续追踪
        """
        # 模拟 LLM 决策：选择第一个选项（在实际实现中应调用 LLM）
        resolution = options[0] if options else "default"
        decision = Decision(
            issue=issue,
            resolution=resolution,
            reasoning=f"自动选择 '{resolution}'：基于当前上下文最优解",
            options=options,
        )
        self._decisions.append(decision)
        return decision


class Decider:
    """决策引擎——管理所有自主决策点"""

    def __init__(self, mode: InterventionMode = InterventionMode.AUTO) -> None:
        self.mode = mode
        self._resolver = ConflictResolver()

    @property
    def resolver(self) -> ConflictResolver:
        return self._resolver

    def should_intervene(self, importance: str = "normal") -> bool:
        """判断是否需要用户介入"""
        if self.mode == InterventionMode.AUTO:
            return False
        elif self.mode == InterventionMode.LIGHT:
            return importance in ("critical", "high")
        else:  # HEAVY
            return True

    def decide(
        self,
        issue: str,
        options: list[str],
        context: dict[str, Any] | None = None,
        importance: str = "normal",
    ) -> Decision:
        """做出决策（如果档位需要介入，返回 None 由上层处理）"""
        if self.should_intervene(importance):
            # 需要用户介入，返回空决策
            return Decision(issue=issue, resolution="", reasoning="需要用户介入")

        return self._resolver.resolve(issue, options, context)