"""维度契约登记表（HA-Eval L2 · 单一事实来源）

背景
----
2026-09-08 事故暴露的元问题：一个评估维度的语义散落在 **8 处**，改一个维度要同步
8 个地方，任何一处漏改即静默错配：

===================  ==========================================================
语义                  原位置
===================  ==========================================================
维度中文标签 / prompt  ``reader_appeal._EVAL_DIM_LABELS``
是否计数维            ``reader_appeal.COUNT_DIMS``
容差带                ``evaluator_types._SOFT_MARGIN``
值域钳制              ``reader_appeal._clamp``
降级安全默认          ``reader_appeal._default_for`` / ``evaluator_metrics._score``
合格线阈值            ``evaluator.EvaluatorAgent.qt``
作用域（window/收尾）  ``evaluator_dims._DIM_SCOPE``
人话归因              ``evaluator._SUMMARY_REASONS``
===================  ==========================================================

本模块把上述全部收敛为一张 :data:`DIMENSIONS` 登记表，其余各处改为**派生别名**
（见文件末尾），从根上消除"改一处漏七处"。

设计要点
--------
1. **量纲是一等公民**：``Unit`` 区分 COUNT（缺陷条数）/ SCORE_0_100 / RATIO_0_1 /
   BOOLEAN。计数维与评分维不再共用裸 float 通道，串值在构造期即被
   :class:`DimensionContractError` 拦截。
2. **作用域声明式**：``Scope`` 让 L4 处置层能按"开头问题 / 全局结构问题 / 窗口问题"
   自动选择处置动作，替代 ``evaluator.py`` 里靠 name prefix 硬编码的三处特判。
3. **渐进收紧**：``STRICT_DIMENSION_CONTRACT`` 关闭时只警告不抛错，用于生产环境
   先跑一轮观察真实误报，确认后再转严格模式。

依赖方向：本模块属 ``agent.core.quality``（领域层），仅依赖标准库。
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Iterable


class Unit(str, Enum):
    """量纲。决定 value 的合法值域与语义。"""

    COUNT = "count"            # 缺陷条数，[0, +inf)，必须整数
    SCORE_0_100 = "score"      # 0-100 评分
    RATIO_0_1 = "ratio"        # 0-1 比例
    BOOLEAN = "bool"           # 0/1


class Direction(str, Enum):
    HIGHER_BETTER = ">="
    LOWER_BETTER = "<="


class Scope(str, Enum):
    """维度作用域：决定失败时该由谁、在哪个范围内修复。"""

    WINDOW = "window"                  # 每轮窗口均评；末窗回滚可修
    BOOK_ENDING = "book_ending"        # 全书收尾验收维，仅结局窗口启用
    FIRST_CHAPTERS = "first_chapters"  # 开头若干章；末窗回滚**修不到**


class SourceKind(str, Enum):
    COMPUTED = "computed"  # 确定性计算，无 LLM
    LLM = "llm"            # 由 LLM 评分
    OFFLINE = "offline"    # LLM 不可用的降级占位


class DimensionContractError(ValueError):
    """维度契约违规（量纲/值域/方向不符合登记表声明）。"""


#: 值域表。COUNT 上界不设限（缺陷条数理论无上限）。
_VALUE_RANGES: dict[Unit, tuple[float, float]] = {
    Unit.COUNT: (0.0, float("inf")),
    Unit.SCORE_0_100: (0.0, 100.0),
    Unit.RATIO_0_1: (0.0, 1.0),
    Unit.BOOLEAN: (0.0, 1.0),
}


@dataclass(frozen=True)
class DimensionSpec:
    """单个评估维度的完整声明（SSOT）。"""

    name: str
    label: str
    unit: Unit
    direction: Direction
    default_threshold: float
    required: bool = False          # 硬指标：不可放宽
    soft_margin: float = 0.0        # 容差带（仅 0-100 评分维用于吸收 LLM 噪声）
    scope: Scope = Scope.WINDOW
    source: SourceKind = SourceKind.LLM
    prompt_label: str = ""          # LLM 评分 prompt 中的维度描述
    safe_default: float = 0.0       # LLM 不可用时的降级值
    summary_reason: str = ""        # 人话归因模板
    counted_by_issues: bool = False  # True=以 LLM 列举的 issues 条数重算（原 COUNT_DIMS）

    @property
    def value_range(self) -> tuple[float, float]:
        return _VALUE_RANGES[self.unit]

    @property
    def is_count(self) -> bool:
        return self.unit is Unit.COUNT

    def clamp(self, value: float) -> float:
        """按量纲钳制到合法值域（替代原 ``reader_appeal._clamp``）。"""
        lo, hi = self.value_range
        v = max(lo, min(hi, value)) if hi != float("inf") else max(lo, value)
        return float(int(v)) if self.unit is Unit.COUNT else float(v)

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "label": self.label,
            "unit": self.unit.value,
            "direction": self.direction.value,
            "default_threshold": self.default_threshold,
            "required": self.required,
            "soft_margin": self.soft_margin,
            "scope": self.scope.value,
            "source": self.source.value,
            "counted_by_issues": self.counted_by_issues,
            "safe_default": self.safe_default,
        }


# ============================================================
# 登记表（SSOT）
# ============================================================
def _spec(**kw: Any) -> DimensionSpec:
    return DimensionSpec(**kw)


#: 迷爱看 / 黄金三章共用的六维（短标签 → prompt 描述）
APPEAL_SUBDIMENSIONS: dict[str, tuple[str, str]] = {
    "hook_strength": ("钩子强度", "章末钩子强度（让读者想翻下一章的抓力）"),
    "payoff_density": ("爽点密度", "爽点密度（反转/打脸/成长/揭密的爽感浓度）"),
    "immersion": ("代入感", "代入感（视角稳定、细节可信、情绪可被带入）"),
    "character_arc": ("人物弧光", "人物弧光（角色有成长/转变，不是工具人）"),
    "world_novelty": ("世界观新颖度", "世界观新颖度（设定有新意、有记忆点）"),
    "emotion_curve": ("情绪曲线", "情绪曲线（节奏起伏有呼吸感，不Flat不注水）"),
}

DIMENSIONS: dict[str, DimensionSpec] = {
    # ---------------------------------------------------- 硬指标：LLM 计数维
    "character_stability_high": _spec(
        name="character_stability_high", label="人设稳定",
        unit=Unit.COUNT, direction=Direction.LOWER_BETTER, default_threshold=0.0,
        required=True, source=SourceKind.LLM,
        prompt_label="人设稳定性（角色言行/动机是否前后矛盾，逐项列举崩坏处数量）",
        safe_default=0.0, summary_reason="人设出现前后矛盾，建议核对角色档案并统一言行/动机",
        counted_by_issues=True,
    ),
    "setting_consistency_high": _spec(
        name="setting_consistency_high", label="设定一致",
        unit=Unit.COUNT, direction=Direction.LOWER_BETTER, default_threshold=0.0,
        required=True, source=SourceKind.LLM,
        prompt_label="设定一致性（境界/金手指/世界观规则是否被打破，逐项列举冲突数量）",
        safe_default=0.0, summary_reason="设定被打破，建议回查世界观设定并修复冲突",
        counted_by_issues=True,
    ),
    "logic_holes": _spec(
        name="logic_holes", label="逻辑漏洞",
        unit=Unit.COUNT, direction=Direction.LOWER_BETTER, default_threshold=0.0,
        required=True, source=SourceKind.LLM,
        prompt_label="逻辑漏洞（情节硬伤/因果不成立，逐项列举漏洞数量）",
        safe_default=0.0, summary_reason="存在逻辑漏洞，建议修复因果硬伤",
        counted_by_issues=True,
    ),
    # ---------------------------------------------------- LLM 评分维（0-100）
    "coherence": _spec(
        name="coherence", label="连贯性",
        unit=Unit.SCORE_0_100, direction=Direction.HIGHER_BETTER, default_threshold=85.0,
        soft_margin=5.0, source=SourceKind.LLM,
        prompt_label="连贯性（章节衔接/叙事流畅度，0-100 评分）",
        safe_default=100.0, summary_reason="连贯性偏低，建议检查章节衔接与叙事流畅度",
    ),
    "readability": _spec(
        name="readability", label="追读力",
        unit=Unit.SCORE_0_100, direction=Direction.HIGHER_BETTER, default_threshold=80.0,
        source=SourceKind.LLM,
        prompt_label="追读力/可读性（让人想继续读的欲望，0-100 评分）",
        safe_default=100.0, summary_reason="追读力不足，建议在章末加强悬念/钩子",
    ),
    # ---------------------------------------------------- 确定性计算维
    "foreshadow_recycle_rate": _spec(
        name="foreshadow_recycle_rate", label="伏笔闭环",
        unit=Unit.RATIO_0_1, direction=Direction.HIGHER_BETTER, default_threshold=0.90,
        source=SourceKind.COMPUTED,
        summary_reason="伏笔回收率不足，建议安排已埋伏笔的回收或标注废弃",
    ),
    "pacing_abnormal": _spec(
        name="pacing_abnormal", label="节奏异常",
        unit=Unit.RATIO_0_1, direction=Direction.LOWER_BETTER, default_threshold=0.03,
        source=SourceKind.COMPUTED,
        summary_reason="异常章节比例偏高（注水/赶进度），建议平衡章节篇幅",
    ),
    "padding_repetition_abnormal": _spec(
        name="padding_repetition_abnormal", label="注水·重复句占比",
        unit=Unit.RATIO_0_1, direction=Direction.LOWER_BETTER, default_threshold=0.30,
        required=True, source=SourceKind.COMPUTED,
        summary_reason="重复句占比偏高，建议删减车轱辘话/合并相似句",
    ),
    # ---------------------------------------------------- G8：全书结构维（收尾窗口）
    "mainline_progress": _spec(
        name="mainline_progress", label="主线推进",
        unit=Unit.COUNT, direction=Direction.HIGHER_BETTER, default_threshold=0.0,
        scope=Scope.BOOK_ENDING, source=SourceKind.COMPUTED,
        summary_reason="支线推进不足：已访问支线数未达下限，建议推进/切换更多支线后再收尾",
    ),
    "ending_convergence": _spec(
        name="ending_convergence", label="结局收敛",
        unit=Unit.RATIO_0_1, direction=Direction.HIGHER_BETTER, default_threshold=0.90,
        scope=Scope.BOOK_ENDING, source=SourceKind.COMPUTED,
        summary_reason="结局收敛不达标：未进入结局模式或结局段伏笔回收不足，"
                       "建议末段集中回收伏笔并向架构结局收束",
    ),
}


def make_prefixed_specs(
    prefix: str,
    *,
    dim_threshold: float,
    total_threshold: float,
    scope: Scope = Scope.WINDOW,
    group_label: str,
) -> dict[str, DimensionSpec]:
    """程序化生成带前缀的六维 + 综合维（迷爱看 / 黄金三章共用一套）。

    Args:
        prefix: 维度名前缀（``appeal_`` / ``golden_``）。
        dim_threshold: 单维触底线（如 40）。
        total_threshold: 综合合格线（如 60）。
        scope: 作用域；黄金三章传 ``FIRST_CHAPTERS``。
        group_label: 中文分组前缀（``迷`` / ``金三``）。
    """
    out: dict[str, DimensionSpec] = {}
    for key, (short, prompt_desc) in APPEAL_SUBDIMENSIONS.items():
        out[f"{prefix}{key}"] = _spec(
            name=f"{prefix}{key}", label=f"{group_label}·{short}",
            unit=Unit.SCORE_0_100, direction=Direction.HIGHER_BETTER,
            default_threshold=dim_threshold, scope=scope, source=SourceKind.LLM,
            prompt_label=prompt_desc,
            safe_default=dim_threshold,
            summary_reason=f"{prompt_desc.split('（')[0]}不足，建议针对该项优化",
        )
    out[f"{prefix}total"] = _spec(
        name=f"{prefix}total", label=f"{group_label}·综合",
        unit=Unit.SCORE_0_100, direction=Direction.HIGHER_BETTER,
        default_threshold=total_threshold, scope=scope, source=SourceKind.LLM,
        safe_default=total_threshold,
        summary_reason=f"{group_label}综合分不足，建议按维度明细逐项优化",
    )
    return out


DIMENSIONS.update(make_prefixed_specs(
    "appeal_", dim_threshold=40.0, total_threshold=60.0,
    scope=Scope.WINDOW, group_label="迷",
))
DIMENSIONS.update(make_prefixed_specs(
    "golden_", dim_threshold=40.0, total_threshold=60.0,
    scope=Scope.FIRST_CHAPTERS, group_label="金三",
))


# ============================================================
# 查询
# ============================================================
def get_spec(name: str) -> DimensionSpec | None:
    """按维度名取声明；未登记返回 ``None``（调用方应保持宽容，不校验）。"""
    return DIMENSIONS.get(name)


def spec_for(name: str) -> DimensionSpec:
    """按维度名取声明；未登记抛 :class:`DimensionContractError`。

    用于**新增维度必须登记**的强约束场景（L4 处置层）：未登记的维度无法推导
    处置动作，早失败好过静默放行。
    """
    spec = DIMENSIONS.get(name)
    if spec is None:
        raise DimensionContractError(
            f"维度 {name!r} 未在 core/quality/dimension_registry.DIMENSIONS 登记；"
            f"新增维度必须先登记 unit/direction/scope，否则处置层无法推导修复范围"
        )
    return spec


def specs_by_scope(scope: Scope) -> list[DimensionSpec]:
    return [s for s in DIMENSIONS.values() if s.scope is scope]


def all_names() -> list[str]:
    return sorted(DIMENSIONS)


# ============================================================
# 契约校验
# ============================================================
#: 严格模式开关。``NOVEL_AGENT_DIM_CONTRACT_WARN=1`` 时只告警不抛错，
#: 供生产环境先跑一轮观察真实误报，确认后转严格（默认严格）。
STRICT_DIMENSION_CONTRACT: bool = (
    os.environ.get("NOVEL_AGENT_DIM_CONTRACT_WARN", "").strip().lower()
    not in ("1", "true", "yes", "on")
)

#: 非严格模式下收集的违规（供 doctor 回放），严格模式下为空。
CONTRACT_VIOLATIONS: list[str] = []


def validate_value(spec: DimensionSpec, value: float) -> str | None:
    """校验 value 是否符合 spec 的量纲/值域；返回违规原因，通过返回 ``None``。"""
    lo, hi = spec.value_range
    if not (lo <= value <= hi):
        return (
            f"{spec.name}: value={value} 超出 {spec.unit.value} 值域 [{lo}, {hi}]"
        )
    if spec.unit is Unit.COUNT and float(value) != float(int(value)):
        return f"{spec.name}: COUNT 维必须为整数，实测 {value}"
    return None


def enforce_contract(spec: DimensionSpec, value: float) -> None:
    """执行契约校验：严格模式抛错，告警模式记录到 :data:`CONTRACT_VIOLATIONS`。

    这是"量纲错配在构造期暴露"的落点——把 3.0（缺陷条数）喂给 0-100 评分维时，
    若值域内（如 3.0 ∈ [0,100]）不会触发本层，而由 L3 的
    :mod:`validators` 评分维下限检测负责（SCORE 维 < 10 判异常）。
    """
    reason = validate_value(spec, value)
    if reason is None:
        return
    if STRICT_DIMENSION_CONTRACT:
        raise DimensionContractError(reason)
    CONTRACT_VIOLATIONS.append(reason)


# ============================================================
# 派生别名（兼容旧调用点；新代码请直接用 DIMENSIONS / get_spec）
# ------------------------------------------------------------
# 以下常量由登记表派生，与重构前逐项等价（有测试锁定）。
# 标记为 deprecated：M2 之后的一个里程碑内删除。
# ============================================================

#: 原 ``evaluator_types._SOFT_MARGIN``
_SOFT_MARGIN: dict[str, float] = {
    name: spec.soft_margin for name, spec in DIMENSIONS.items()
}

#: 原 ``reader_appeal.COUNT_DIMS``
COUNT_DIMS: frozenset[str] = frozenset(
    name for name, spec in DIMENSIONS.items() if spec.counted_by_issues
)

#: 原 ``reader_appeal._EVAL_DIM_LABELS``
_EVAL_DIM_LABELS: dict[str, str] = {
    name: spec.prompt_label
    for name, spec in DIMENSIONS.items()
    if spec.prompt_label
}

#: 原 ``evaluator_dims._DIM_SCOPE``（仅登记非默认作用域的维度）
_DIM_SCOPE: dict[str, str] = {
    name: spec.scope.value
    for name, spec in DIMENSIONS.items()
    if spec.scope is not Scope.WINDOW
}

#: 原 ``evaluator._SUMMARY_REASONS``
_SUMMARY_REASONS: dict[str, str] = {
    name: spec.summary_reason for name, spec in DIMENSIONS.items() if spec.summary_reason
}

#: 默认合格线（原 ``EvaluatorAgent.qt`` 的默认值）
DEFAULT_THRESHOLDS: dict[str, float] = {
    name: spec.default_threshold for name, spec in DIMENSIONS.items()
}


def safe_default_for(name: str) -> float:
    """LLM 不可用时的降级值（原 ``reader_appeal._default_for`` / ``_score``）。"""
    spec = get_spec(name)
    return spec.safe_default if spec is not None else 0.0


def clamp_value(name: str, value: float) -> float:
    """按量纲钳制（原 ``reader_appeal._clamp``）。"""
    spec = get_spec(name)
    return spec.clamp(value) if spec is not None else float(value)


def iter_specs(names: Iterable[str] | None = None) -> list[DimensionSpec]:
    if names is None:
        return list(DIMENSIONS.values())
    return [DIMENSIONS[n] for n in names if n in DIMENSIONS]
