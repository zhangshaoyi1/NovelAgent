"""处置策略层（HA-Eval L4）

背景
----
``EvaluatorAgent.evaluate_with_repair`` 原实现里，「体检不达标 → 做什么」是靠
**name prefix 硬编码的三处特判**拼出来的：

- ``golden_*`` 失败 → 禁止回滚（末窗回滚修不到开头）
- ``mainline_*`` / ``ending_*`` 失败 → 禁止回滚（全局结构问题，末窗修不到）
- 其余 → 直接 ``trigger_rollback()``（破坏性删章）+ ``rewriter()``（几十万 token）

每新增一个维度就要再写一个特判，且**证据质量零校验、无代价预估、无 dry-run**，
单条可疑分数即可触发不可逆操作——这正是 2026-09-08 事故演变成真事故的原因。

设计
----
把「失败维度 → 处置动作」变成**声明式规则表** + **守门器**：

1. :data:`DEFAULT_RULES` 按维度属性（置信度 / 作用域 / 量纲 / 是否硬指标）匹配；
2. 多条命中时按 :data:`ACTION_PRECEDENCE` 取**最保守**的动作；
3. 不可逆动作（``ROLLBACK_REWRITE``）必须过 :class:`DispositionGate`：
   置信度门槛 → 双证据 → 代价预估 → dry-run，四道全过才执行。

核心不变式：**证据不可信 ⇒ 不做任何处置动作**（``RETRY_EVAL`` 优先于一切）。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Iterable, Sequence

from agent.core.quality.dimension_registry import DimensionSpec, Scope, Unit


class Action(str, Enum):
    """处置动作。"""

    CONTINUE = "continue"                  # 放行
    LOCAL_REPAIR = "local_repair"          # 只重写末章，不回滚（可逆）
    ROLLBACK_REWRITE = "rollback_rewrite"  # **不可逆**：删章 + 重写
    ESCALATE = "escalate"                  # 上报人工
    RETRY_EVAL = "retry_eval"              # 证据不可信，复评后再说


#: 动作优先级（数值越大越优先）。语义：**越"不敢动"越优先**。
#: 证据不可信时一切处置都无意义，故 RETRY_EVAL 最高。
ACTION_PRECEDENCE: dict[Action, int] = {
    Action.CONTINUE: 0,
    Action.LOCAL_REPAIR: 1,
    Action.ROLLBACK_REWRITE: 2,
    Action.ESCALATE: 3,
    Action.RETRY_EVAL: 4,
}

#: 不可逆动作集合——只有这些动作需要过守门器。
IRREVERSIBLE_ACTIONS: frozenset[Action] = frozenset({Action.ROLLBACK_REWRITE})


@dataclass(frozen=True)
class DispositionRule:
    """一条「失败维度 → 处置动作」规则。

    Args:
        name: 规则名（用于审计与日志）。
        match: 匹配函数，接收 ``DimensionResult``。
        action: 命中后的处置动作。
        reason: 人读的原因（进入报告 / 审计）。
        requires_double_evidence: 是否要求双证据（换温度复评一致）才允许执行。
        max_cost_tokens: 该动作允许的最大预估 token 代价；0 表示不限制。
    """

    name: str
    match: Callable[[Any], bool]
    action: Action
    reason: str
    requires_double_evidence: bool = False
    max_cost_tokens: int = 0
    #: 兜底规则：仅当**没有任何其它规则命中**时才生效。
    #: 否则它（``match`` 恒真）会凭更高的动作优先级抢走决策权。
    is_fallback: bool = False


def _spec_of(d: Any) -> DimensionSpec | None:
    return getattr(d, "spec", None)


def _untrusted(d: Any) -> bool:
    return float(getattr(d, "confidence", 1.0)) <= 0.0


def _scope_is(scope: Scope) -> Callable[[Any], bool]:
    def _m(d: Any) -> bool:
        spec = _spec_of(d)
        return spec is not None and spec.scope is scope
    return _m


def _soft_score(d: Any) -> bool:
    spec = _spec_of(d)
    return (
        spec is not None
        and not spec.required
        and spec.unit is Unit.SCORE_0_100
    )


def _hard_gate(d: Any) -> bool:
    return bool(getattr(d, "required", False))


#: 默认规则表（**顺序即优先级**：先命中者记录，最终按 ACTION_PRECEDENCE 取最高）
DEFAULT_RULES: tuple[DispositionRule, ...] = (
    DispositionRule(
        "untrusted_evidence", _untrusted, Action.RETRY_EVAL,
        "判定证据不可信（疑似缓存碰撞/解析异常），禁止据此处置，需复评确认",
    ),
    DispositionRule(
        "first_chapters_scope", _scope_is(Scope.FIRST_CHAPTERS), Action.ESCALATE,
        "开头若干章不达标：回溯窗口只覆盖末 N 章，修不到开头，上报人工重写",
    ),
    DispositionRule(
        "book_ending_scope", _scope_is(Scope.BOOK_ENDING), Action.ESCALATE,
        "全局结构门禁不达标：支线推进/结局收敛是全局问题，末窗回滚修不到，上报人工",
    ),
    DispositionRule(
        "soft_score_dim", _soft_score, Action.LOCAL_REPAIR,
        "非硬指标评分维不达标：定向重写末章即可，无需销毁整窗",
        max_cost_tokens=200_000,
    ),
    DispositionRule(
        "hard_gate", _hard_gate, Action.ROLLBACK_REWRITE,
        "硬指标不达标：回溯最近窗口并重写",
        requires_double_evidence=True,
        max_cost_tokens=1_500_000,
    ),
    # 兜底：未命中任何规则（如未登记的 RATIO 软维）→ 保持旧语义（回滚重写）
    # is_fallback=True：仅当**没有任何其它规则命中**时才生效，见 match_rules。
    DispositionRule(
        "fallback", lambda d: True, Action.ROLLBACK_REWRITE,
        "维度不达标（兜底规则）：回溯最近窗口并重写",
        requires_double_evidence=True,
        max_cost_tokens=1_500_000,
        is_fallback=True,
    ),
)


@dataclass
class DispositionPlan:
    """一次处置决策。"""

    action: Action
    reason: str
    rule_names: list[str] = field(default_factory=list)
    dims: list[Any] = field(default_factory=list)
    requires_double_evidence: bool = False
    max_cost_tokens: int = 0

    @property
    def is_irreversible(self) -> bool:
        return self.action in IRREVERSIBLE_ACTIONS

    @property
    def labels(self) -> list[str]:
        return [str(getattr(d, "label", getattr(d, "name", "?"))) for d in self.dims]

    def to_dict(self) -> dict[str, Any]:
        return {
            "action": self.action.value,
            "reason": self.reason,
            "rule_names": list(self.rule_names),
            "dimensions": [str(getattr(d, "name", "")) for d in self.dims],
            "requires_double_evidence": self.requires_double_evidence,
            "max_cost_tokens": self.max_cost_tokens,
        }


@dataclass
class Authorization:
    """守门器裁决结果。"""

    ok: bool
    reason: str = ""
    estimated_cost_tokens: int = 0
    estimated_chapters: int = 0
    dry_run: bool = False


class DispositionPolicy:
    """把失败维度映射为处置动作（声明式，替代 name-prefix 特判）。"""

    def __init__(self, rules: Sequence[DispositionRule] = DEFAULT_RULES) -> None:
        self.rules = tuple(rules)

    def match_rules(self, dim: Any) -> list[DispositionRule]:
        hits = []
        fallback: DispositionRule | None = None
        for r in self.rules:
            try:
                if r.match(dim):
                    if r.is_fallback:
                        if fallback is None:
                            fallback = r
                    else:
                        hits.append(r)
            except Exception:  # noqa: BLE001 - 规则匹配异常视为不命中
                continue  # noqa: SILENT_DEGRADE
        # 兜底规则只在没有任何其它规则命中时生效，否则它（match 恒真）会抢走决策。
        if not hits and fallback is not None:
            hits.append(fallback)
        return hits

    def plan(self, failed_dims: Iterable[Any]) -> DispositionPlan:
        """为一批失败维度生成处置计划：取所有命中规则中**最保守**的动作。"""
        dims = list(failed_dims)
        if not dims:
            return DispositionPlan(Action.CONTINUE, "无失败维度")

        best: DispositionRule | None = None
        names: list[str] = []
        for d in dims:
            for r in self.match_rules(d):
                if r.name not in names:
                    names.append(r.name)
                if best is None or ACTION_PRECEDENCE[r.action] > ACTION_PRECEDENCE[best.action]:
                    best = r
        if best is None:  # pragma: no cover - 兜底规则保证非空
            best = self.rules[-1]

        # 若最保守动作是 ROLLBACK_REWRITE，但存在被其"覆盖"的软维，原因需合并说明
        reason = best.reason
        if best.action is Action.ROLLBACK_REWRITE and len(dims) > 1:
            reason = f"{reason}（本轮共 {len(dims)} 个维度不达标：{'、'.join(self._labels(dims))}）"

        return DispositionPlan(
            action=best.action,
            reason=reason,
            rule_names=names,
            dims=dims,
            requires_double_evidence=best.requires_double_evidence,
            max_cost_tokens=best.max_cost_tokens,
        )

    @staticmethod
    def _labels(dims: Sequence[Any]) -> list[str]:
        return [str(getattr(d, "label", getattr(d, "name", "?"))) for d in dims]


class DispositionGate:
    """不可逆动作守门器：四道检查全过才放行。

    Args:
        cost_per_chapter_tokens: 重写单章的预估 token 代价（用于代价预估）。
        require_double_evidence: 是否强制双证据（换温度复评一致）。
    """

    def __init__(
        self,
        *,
        cost_per_chapter_tokens: int = 120_000,
        require_double_evidence: bool = False,
    ) -> None:
        self.cost_per_chapter_tokens = cost_per_chapter_tokens
        self.require_double_evidence = require_double_evidence

    def estimate(
        self, plan: DispositionPlan, *, chapters: int, budget_remaining: int | None = None
    ) -> tuple[int, int]:
        """返回 (预估 token, 预估影响章节数)。"""
        n = chapters if plan.action is Action.ROLLBACK_REWRITE else max(1, min(chapters, 1))
        return self.cost_per_chapter_tokens * n, n

    def authorize(
        self,
        plan: DispositionPlan,
        *,
        chapters: int = 1,
        budget_remaining: int | None = None,
        double_evidence: bool = False,
        dry_run: bool = False,
    ) -> Authorization:
        """裁决是否允许执行该处置动作。

        四道检查：
        ① 置信度（不可信 → 拒绝，应由 RETRY_EVAL 先行拦截）
        ② 双证据（开启时：须复评一致）
        ③ 代价预估（超出规则上限或预算余额 → 拒绝）
        ④ dry-run（只出计划，不放行）
        """
        est_tokens, est_chapters = self.estimate(
            plan, chapters=chapters, budget_remaining=budget_remaining
        )

        # ① 置信度门槛
        untrusted = [d for d in plan.dims if _untrusted(d)]
        if untrusted:
            return Authorization(
                False,
                "存在不可信维度（" + "、".join(
                    str(getattr(d, "label", getattr(d, "name", "?"))) for d in untrusted
                ) + "），禁止执行任何处置动作；请先复评",
                est_tokens, est_chapters,
            )

        # ③ 代价预估（先于 dry-run，保证 dry-run 输出也带代价）
        if plan.max_cost_tokens and est_tokens > plan.max_cost_tokens:
            return Authorization(
                False,
                f"预估代价 {est_tokens:,} token 超出该动作上限 {plan.max_cost_tokens:,}",
                est_tokens, est_chapters,
            )
        if budget_remaining is not None and est_tokens > budget_remaining:
            return Authorization(
                False,
                f"预估代价 {est_tokens:,} token 超出剩余预算 {budget_remaining:,}",
                est_tokens, est_chapters,
            )

        # ② 双证据
        if plan.requires_double_evidence and self.require_double_evidence and not double_evidence:
            return Authorization(
                False,
                "硬指标处置需要双证据：请换温度/顺序复评一次，结果一致后方可执行",
                est_tokens, est_chapters,
            )

        # ④ dry-run
        if dry_run:
            return Authorization(
                False,
                f"[dry-run] 计划：{plan.action.value} —— 影响 {est_chapters} 章、"
                f"预估 {est_tokens:,} token。{plan.reason}",
                est_tokens, est_chapters, dry_run=True,
            )

        return Authorization(True, "放行", est_tokens, est_chapters)
