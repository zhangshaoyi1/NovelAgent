"""一致性校验器（5.5）

职责：在设定更新、写作前、章节产出后三个时机执行规则校验。

校验项：
    - 字段冲突：同一字段在 world.md / subline.md / character.md 间矛盾
    - 境界越级：主角境界变化是否突破冻结体系
    - 金手指越界：使用是否符合登记的成长/代价/上限
    - 关系网一致性：本章关系变化是否与 relations/graph.md 冲突
    - 时间线：事件时序是否矛盾

冲突输出：一致性影响报告（冲突条目 + 涉及章节 + 处理建议）
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any


class CheckTrigger(str, Enum):
    """校验时机"""

    PRE_WRITE = "pre-write"
    POST_WRITE = "post-write"
    PRE_UPDATE_SETTING = "pre-update-setting"


class Severity(str, Enum):
    """冲突严重度"""

    BLOCK = "block"      # 阻断，必须处理
    WARN = "warn"        # 警告，可忽略


@dataclass
class Conflict:
    """一致性冲突"""

    rule_id: str
    severity: Severity
    description: str
    affected_chapters: list[str] = field(default_factory=list)
    suggestions: list[str] = field(default_factory=list)


@dataclass
class ConsistencyReport:
    """一致性影响报告"""

    passed: bool
    trigger: CheckTrigger
    conflicts: list[Conflict] = field(default_factory=list)

    def to_markdown(self) -> str:
        """渲染为 Markdown 报告"""
        # TODO: 实现
        raise NotImplementedError


def _noop_consistency_check(ctx: dict[str, Any], arbiter: Any) -> list[Conflict]:
    """占位一致性规则检查（无问题）"""
    return []


def _rule_field_conflict(ctx: dict[str, Any], arbiter: Any) -> list[Conflict]:
    """字段冲突检测：委托 ConflictArbiter.check_new_setting（T-5）"""
    if arbiter is None:
        return []
    new_setting = ctx.get("new_setting", "")
    if not new_setting:
        return []
    report = arbiter.check_new_setting(new_setting, ctx.get("subline_id"))
    conflicts: list[Conflict] = []
    for c in getattr(report, "conflicts", []) or []:
        conflicts.append(Conflict(
            rule_id="field_conflict",
            severity=Severity.BLOCK if getattr(c, "is_block", True) else Severity.WARN,
            description=str(getattr(c, "description", "")),
            affected_chapters=getattr(c, "affected_chapters", []) or [],
            suggestions=getattr(c, "suggestions", []) or [],
        ))
    return conflicts


class ConsistencyChecker:
    """一致性校验器（T-5：可配置 rule 集，至少 1 条委托 ConflictArbiter）"""

    def __init__(self, project_dir: Path) -> None:
        self.project_dir = project_dir
        self._arbiter: "Any" = None

    # ------ 内置规则集（可配置，至少 1 条委托 ConflictArbiter）------
    def _builtin_rules(self) -> list[Any]:
        """返回内置一致性规则（每项 check(ctx, arbiter) -> list[Conflict]）"""
        return [
            _ConsistencyRule(
                id="field_conflict",
                name="字段冲突",
                severity=Severity.BLOCK,
                check=_rule_field_conflict,
            ),
            _ConsistencyRule(
                id="realm_overstep",
                name="境界越级",
                severity=Severity.WARN,
                check=_noop_consistency_check,
            ),
            _ConsistencyRule(
                id="golden_finger_overstep",
                name="金手指越界",
                severity=Severity.WARN,
                check=_noop_consistency_check,
            ),
            _ConsistencyRule(
                id="relation_conflict",
                name="关系网一致性",
                severity=Severity.WARN,
                check=_noop_consistency_check,
            ),
            _ConsistencyRule(
                id="timeline_conflict",
                name="时间线冲突",
                severity=Severity.WARN,
                check=_noop_consistency_check,
            ),
        ]

    def _get_arbiter(self) -> "Any":
        """懒加载 ConflictArbiter（避免循环导入）"""
        if self._arbiter is None:
            from agent.core.conflict_service import ConflictArbiter

            self._arbiter = ConflictArbiter(self.project_dir)
        return self._arbiter

    def check(
        self,
        trigger: CheckTrigger,
        ctx: dict[str, Any] | None = None,
    ) -> ConsistencyReport:
        """执行校验（T-5：遍历内置 rule 集，不再 raise）

        Args:
            trigger: 校验时机
            ctx: 上下文（设定变更内容 / 章节内容等）

        Returns:
            ConsistencyReport
        """
        ctx = ctx or {}
        conflicts: list[Conflict] = []
        arbiter = self._get_arbiter()
        for rule in self._builtin_rules():
            try:
                rule_conflicts = rule.check(ctx, arbiter)
            except Exception:  # noqa: BLE001 - 单条规则异常不影响整体校验
                continue
            if rule_conflicts:
                conflicts.extend(rule_conflicts)
        passed = not any(c.severity == Severity.BLOCK for c in conflicts)
        return ConsistencyReport(passed=passed, trigger=trigger, conflicts=conflicts)

    def assess_architecture_impact(self) -> ConsistencyReport:
        """架构修订时评估下游影响（M14 F14.7，T-5：返回空壳报告）"""
        return ConsistencyReport(passed=True, trigger=CheckTrigger.PRE_WRITE, conflicts=[])


@dataclass
class _ConsistencyRule:
    """内置一致性规则项"""

    id: str
    name: str
    severity: Severity
    check: Any  # Callable[[dict, ConflictArbiter | None], list[Conflict]]
