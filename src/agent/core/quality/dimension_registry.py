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


class EvalTiming(str, Enum):
    """评估时机：这个维度**什么时候**被评。

    2026-09-15 拆分（见 ``Scope`` 的说明）：原 ``Scope`` 把"何时评"与"在哪修"
    混在一根轴上，导致枚举里缺"每轮都评、但末窗回滚修不到"这一格，漏填即落进
    最激进的 ``WINDOW``。现拆为两根独立轴。
    """

    EVERY_WINDOW = "every_window"        # 每轮窗口均评（默认）
    BOOK_ENDING = "book_ending"          # 全书收尾验收维，仅结局窗口内启用
    FIRST_CHAPTERS = "first_chapters"    # 开头若干章


class Repairability(str, Enum):
    """可修复性：这个维度失败后，**在哪个范围**才能修好。

    决定处置层是否有权动用不可逆动作（末窗回滚）——这是安全属性，
    与"何时评"正交。

    - ``WINDOW``：末窗回滚可修（仅当统计范围也在窗口内时才成立，见 ``stat_scope``）；
    - ``GLOBAL``：全书级问题，末窗回滚修不到 ⇒ 只能上报人工；
    - ``HEAD``：开头问题，末窗回滚修不到 ⇒ 只能上报人工。
    """

    WINDOW = "window"
    GLOBAL = "global"
    HEAD = "head"


class StatScope(str, Enum):
    """统计口径：这个维度的**数值实际量的是哪段文本**。

    2026-09-15 新增（登记单 ``20260915_质检假失败与回退死循环.md`` §八）。

    这是"判得对、但永远修不好"的**可审计形式化**：处置动作授权了哪个范围，
    统计范围就必须落在哪个范围内。此前 ``pacing_abnormal`` /
    ``padding_repetition_abnormal`` 都是"口径 = 全书、动作 = 回退末 5 章"，
    于是早段异常章永远修不掉，同一批被回退 8 次仍必失败。

    红线（``tests/architecture/test_dimension_disposition_matrix.py`` S1–S4）：
    硬指标（``required=True``）的 ``stat_scope`` 与 ``repairability`` 必须相容，
    否则"判而不可修"会以硬闸形式变成删章死循环。
    """

    CHAPTER = "chapter"    # 单章（写作时逐章判定）
    WINDOW = "window"      # 回退窗口内（末 N 章）
    HEAD = "head"          # 开头若干章
    ENDING = "ending"      # 收尾段
    BOOK = "book"          # 全书


class Scope(str, Enum):
    """维度作用域（**兼容视图，2026-09-15 起由两根轴派生，勿再直接使用**）。

    原设计把"何时评"（``eval_timing``）与"可修复性"（``repairability``）压成
    一根轴，于是枚举里**没有**"全书统计 + 每轮都评 + 末窗回滚修不到"这一格。
    漏填该字段的维度（如 ``padding_repetition_abnormal``）会默认落到 ``WINDOW``
    —— 而 ``WINDOW`` 恰恰是所有取值里**最激进**的（唯一授权不可逆回滚的），
    这与"不可逆动作必须由显式规则授权"直接冲突。

    现保留本枚举与 :attr:`DimensionSpec.scope` 作为**派生只读视图**，仅供既有
    调用点（``_DIM_SCOPE`` / ``specs_by_scope``）与报告展示；新代码请直接读
    ``eval_timing`` / ``repairability``。新增 ``BOOK_LEVEL`` 正是补上缺失的槽位。
    """

    WINDOW = "window"                  # 每轮窗口均评；末窗回滚可修
    BOOK_ENDING = "book_ending"        # 全书收尾验收维，仅结局窗口启用
    FIRST_CHAPTERS = "first_chapters"  # 开头若干章；末窗回滚**修不到**
    BOOK_LEVEL = "book_level"          # 全书级统计、每轮均评；末窗回滚**修不到**


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
    # ---- 三根正交轴（2026-09-15：从单一 scope 拆出，见各枚举 docstring）----
    #: 何时评。默认 EVERY_WINDOW（不是最激进的取值，安全）。
    eval_timing: EvalTiming = EvalTiming.EVERY_WINDOW
    #: 在哪修。**默认 GLOBAL 而非 WINDOW**——漏填时给"上报人工"而不是"销毁整窗"。
    #: 这是本次复盘的直接教训：默认值必须是所有取值里**最保守**的那个。
    repairability: Repairability = Repairability.GLOBAL
    #: 统计口径。``None`` 表示未声明（红线会对 COMPUTED 维拦下，见 §S3）。
    stat_scope: StatScope | None = None
    source: SourceKind = SourceKind.LLM
    prompt_label: str = ""          # LLM 评分 prompt 中的维度描述
    safe_default: float = 0.0       # LLM 不可用时的降级值
    summary_reason: str = ""        # 人话归因模板
    counted_by_issues: bool = False  # True=以 LLM 列举的 issues 条数重算（原 COUNT_DIMS）

    @property
    def scope(self) -> Scope:
        """派生兼容视图（由 ``eval_timing`` + ``repairability`` 决定）。

        优先级：时机 > 可修复性。即"仅结局窗口评"优先报 ``BOOK_ENDING``，
        其次"开头"报 ``FIRST_CHAPTERS``，再按可修复性区分 ``WINDOW`` /
        ``BOOK_LEVEL``（后者 = 每轮评但末窗修不到，即此前缺失的那一格）。
        """
        if self.eval_timing is EvalTiming.BOOK_ENDING:
            return Scope.BOOK_ENDING
        if self.eval_timing is EvalTiming.FIRST_CHAPTERS:
            return Scope.FIRST_CHAPTERS
        if self.repairability is Repairability.WINDOW:
            return Scope.WINDOW
        return Scope.BOOK_LEVEL

    @property
    def stat_scope_within_window(self) -> bool:
        """统计范围是否落在"末窗回滚"能触及的范围内（CHAPTER ⊆ WINDOW）。"""
        return self.stat_scope in (StatScope.CHAPTER, StatScope.WINDOW)

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
            # 兼容视图（由下面两根轴派生，仅增不减地保留）
            "scope": self.scope.value,
            "eval_timing": self.eval_timing.value,
            "repairability": self.repairability.value,
            "stat_scope": self.stat_scope.value if self.stat_scope is not None else None,
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
        # LLM 只看当前窗口章节 → 统计口径 WINDOW；末窗回滚可修 → repairability=WINDOW
        repairability=Repairability.WINDOW, stat_scope=StatScope.WINDOW,
        prompt_label=(
            "人设稳定性（角色言行/动机是否与角色档案、**弧光轨迹**冲突——"
            "沿弧光登记轨迹的有序推进属设计内成长，**不算矛盾**；"
            "仅倒退/跳档/无契机/与档案直接冲突才计；逐项列举崩坏处数量）"
        ),
        safe_default=0.0, summary_reason="人设出现前后矛盾，建议核对角色档案并统一言行/动机",
        counted_by_issues=True,
    ),
    "setting_consistency_high": _spec(
        name="setting_consistency_high", label="设定一致",
        unit=Unit.COUNT, direction=Direction.LOWER_BETTER, default_threshold=0.0,
        required=True, source=SourceKind.LLM,
        repairability=Repairability.WINDOW, stat_scope=StatScope.WINDOW,
        prompt_label=(
            "设定一致性（境界/金手指/世界观规则是否被打破——"
            "以**设定台账＋设计意图**为准，设计轨内允许的变化不算打破；"
            "逐项列举冲突数量）"
        ),
        safe_default=0.0, summary_reason="设定被打破，建议回查世界观设定并修复冲突",
        counted_by_issues=True,
    ),
    "logic_holes": _spec(
        name="logic_holes", label="逻辑漏洞",
        unit=Unit.COUNT, direction=Direction.LOWER_BETTER, default_threshold=0.0,
        # 2026-09-15（登记单 ``20260915_回退熔断账实不符与降级当通过`` §二.R1）：
        # required: True → False —— **退出回退授权**，阈值 0 保留为"报告线"。
        #
        # 原配置（required=True + 阈值 0 + repairability=WINDOW）命中
        # ``disposition._hard_gate_in_window`` ⇒ 授权 ROLLBACK_REWRITE（不可逆，销毁末窗 5 章）。
        # 而 prompt 是「逐项列举漏洞数量」——**值域无自然零点、无上界**：
        # 灵荒薪传 27 轮实测分布 0×4 / 1×2 / 2×4 / 3×6 / 4×3 / 5×6 / 6 / 8，
        # 中位数 3，`==0` 仅 15%。要求 LLM 写 3000+ 字零逻辑瑕疵 = **判据不可达**，
        # 等于给"销毁 5 章"配了一个 ~85% 触发率的扳机（实测 4 批 21 次开章净增 0 章）。
        #
        # 与已拍板语义「软维度失败不触发回滚」互为镜像：那条治「判而不可修」，
        # 本条治「判而不可达」——动作强度既不能低于证据等级，也不能高于判据可达性。
        # 失败后走 ``_soft_dim → LOCAL_REPAIR``（只重写末章，可逆）+ warn 告警，
        # 仍计入 overall_pass（不达标照样看得见），只是不再销毁整窗。
        # 注：character_stability_high（可达 33%）/ setting_consistency_high（可达 19%）
        # 本次**不动**——它们的失败由 RollbackBudget 熔断护栏兜底停批上报，见同登记单 §七。
        required=False, source=SourceKind.LLM,
        repairability=Repairability.WINDOW, stat_scope=StatScope.WINDOW,
        prompt_label="逻辑漏洞（情节硬伤/因果不成立，逐项列举漏洞数量）",
        safe_default=0.0, summary_reason="存在逻辑漏洞，建议修复因果硬伤",
        counted_by_issues=True,
    ),
    # ---------------------------------------------------- LLM 评分维（0-100）
    "coherence": _spec(
        name="coherence", label="连贯性",
        unit=Unit.SCORE_0_100, direction=Direction.HIGHER_BETTER, default_threshold=85.0,
        soft_margin=5.0, source=SourceKind.LLM,
        repairability=Repairability.WINDOW, stat_scope=StatScope.WINDOW,
        prompt_label="连贯性（章节衔接/叙事流畅度，0-100 评分）",
        safe_default=100.0, summary_reason="连贯性偏低，建议检查章节衔接与叙事流畅度",
    ),
    "readability": _spec(
        name="readability", label="追读力",
        unit=Unit.SCORE_0_100, direction=Direction.HIGHER_BETTER, default_threshold=80.0,
        source=SourceKind.LLM,
        repairability=Repairability.WINDOW, stat_scope=StatScope.WINDOW,
        prompt_label="追读力/可读性（让人想继续读的欲望，0-100 评分）",
        safe_default=100.0, summary_reason="追读力不足，建议在章末加强悬念/钩子",
    ),
    # ---------------------------------------------------- 确定性计算维
    "foreshadow_recycle_rate": _spec(
        name="foreshadow_recycle_rate", label="伏笔闭环",
        unit=Unit.RATIO_0_1, direction=Direction.HIGHER_BETTER, default_threshold=0.90,
        source=SourceKind.COMPUTED,
        # 读的是 foreshadows.md 这本**全书台账**（到期口径仍跨全书）→ stat_scope=BOOK；
        # 修复手段是"在后续章节安排回收"，不是回退末窗 → repairability=GLOBAL。
        # 2026-09-15：标注为 GLOBAL 使其 scope 从 WINDOW 变为 BOOK_LEVEL（旧值是把
        # "每轮都评"误当"末窗可修"）；因 required=False，动作仍是 LOCAL_REPAIR 不变。
        repairability=Repairability.GLOBAL, stat_scope=StatScope.BOOK,
        summary_reason="伏笔回收率不足，建议安排已埋伏笔的回收或标注废弃",
    ),
    "pacing_abnormal": _spec(
        name="pacing_abnormal", label="节奏异常",
        unit=Unit.RATIO_0_1, direction=Direction.LOWER_BETTER, default_threshold=0.03,
        source=SourceKind.COMPUTED,
        # 2026-09-15：_metric_pacing 已收窄到回退窗口 → 口径与动作一致
        repairability=Repairability.WINDOW, stat_scope=StatScope.WINDOW,
        summary_reason="异常章节比例偏高（注水/赶进度），建议平衡章节篇幅",
    ),
    "padding_repetition_abnormal": _spec(
        name="padding_repetition_abnormal", label="注水·重复句占比",
        unit=Unit.RATIO_0_1, direction=Direction.LOWER_BETTER, default_threshold=0.30,
        required=True, source=SourceKind.COMPUTED,
        # 2026-09-15：_metric_repetition 已收窄到回退窗口；此前"硬闸 + 全书口径"
        # 是"判而不可修"的死循环形态（本项即由 stat_scope 红线追出的实例）。
        repairability=Repairability.WINDOW, stat_scope=StatScope.WINDOW,
        summary_reason="重复句占比偏高，建议删减车轱辘话/合并相似句",
    ),
    # ---------------------------------------------------- 书级·文体卫生（2026-09-12）
    "text_hygiene_blocking": _spec(
        name="text_hygiene_blocking", label="文体卫生",
        unit=Unit.COUNT, direction=Direction.LOWER_BETTER, default_threshold=0.0,
        required=True, source=SourceKind.COMPUTED,
        # 写章时逐章扫描（pre-save 阻断）→ 单章口径；重写该章即可修
        repairability=Repairability.WINDOW, stat_scope=StatScope.CHAPTER,
        summary_reason="正文存在生成残留（残缺比喻/成语误用/短语复读/密度失控），"
                       "请按质检逐条修复后提交",
    ),
    "debut_continuity": _spec(
        name="debut_continuity", label="登场连续",
        unit=Unit.COUNT, direction=Direction.LOWER_BETTER, default_threshold=0.0,
        source=SourceKind.COMPUTED,
        # 逐章判定（本章是否以"再次"口吻引入未登记实体）→ 单章口径
        repairability=Repairability.WINDOW, stat_scope=StatScope.CHAPTER,
        summary_reason="实体以『再次/依旧』口吻登场但无首次登场记录，"
                       "请补写引入或改写措辞",
    ),
    # ---------------------------------------------------- G8：全书结构维（收尾窗口）
    "mainline_progress": _spec(
        name="mainline_progress", label="主线推进",
        unit=Unit.COUNT, direction=Direction.HIGHER_BETTER, default_threshold=0.0,
        eval_timing=EvalTiming.BOOK_ENDING, repairability=Repairability.GLOBAL,
        stat_scope=StatScope.BOOK, source=SourceKind.COMPUTED,
        summary_reason="支线推进不足：已访问支线数未达下限，建议推进/切换更多支线后再收尾",
    ),
    "ending_convergence": _spec(
        name="ending_convergence", label="结局收敛",
        unit=Unit.RATIO_0_1, direction=Direction.HIGHER_BETTER, default_threshold=0.90,
        eval_timing=EvalTiming.BOOK_ENDING, repairability=Repairability.GLOBAL,
        stat_scope=StatScope.ENDING, source=SourceKind.COMPUTED,
        summary_reason="结局收敛不达标：未进入结局模式或结局段伏笔回收不足，"
                       "建议末段集中回收伏笔并向架构结局收束",
    ),
}


def make_prefixed_specs(
    prefix: str,
    *,
    dim_threshold: float,
    total_threshold: float,
    eval_timing: EvalTiming = EvalTiming.EVERY_WINDOW,
    repairability: Repairability = Repairability.WINDOW,
    stat_scope: StatScope = StatScope.WINDOW,
    group_label: str,
) -> dict[str, DimensionSpec]:
    """程序化生成带前缀的六维 + 综合维（迷爱看 / 黄金三章共用一套）。

    Args:
        prefix: 维度名前缀（``appeal_`` / ``golden_``）。
        dim_threshold: 单维触底线（如 40）。
        total_threshold: 综合合格线（如 60）。
        eval_timing: 评估时机；黄金三章传 ``FIRST_CHAPTERS``。
        repairability: 可修复性；黄金三章传 ``HEAD``（末窗回滚修不到开头）。
        stat_scope: 统计口径；黄金三章传 ``HEAD``。
        group_label: 中文分组前缀（``迷`` / ``金三``）。
    """
    out: dict[str, DimensionSpec] = {}
    for key, (short, prompt_desc) in APPEAL_SUBDIMENSIONS.items():
        out[f"{prefix}{key}"] = _spec(
            name=f"{prefix}{key}", label=f"{group_label}·{short}",
            unit=Unit.SCORE_0_100, direction=Direction.HIGHER_BETTER,
            default_threshold=dim_threshold, eval_timing=eval_timing,
            repairability=repairability, stat_scope=stat_scope,
            source=SourceKind.LLM,
            prompt_label=prompt_desc,
            safe_default=dim_threshold,
            summary_reason=f"{prompt_desc.split('（')[0]}不足，建议针对该项优化",
        )
    out[f"{prefix}total"] = _spec(
        name=f"{prefix}total", label=f"{group_label}·综合",
        unit=Unit.SCORE_0_100, direction=Direction.HIGHER_BETTER,
        default_threshold=total_threshold, eval_timing=eval_timing,
        repairability=repairability, stat_scope=stat_scope,
        source=SourceKind.LLM,
        safe_default=total_threshold,
        summary_reason=f"{group_label}综合分不足，建议按维度明细逐项优化",
    )
    return out


DIMENSIONS.update(make_prefixed_specs(
    "appeal_", dim_threshold=40.0, total_threshold=60.0,
    eval_timing=EvalTiming.EVERY_WINDOW, repairability=Repairability.WINDOW,
    stat_scope=StatScope.WINDOW, group_label="迷",
))
DIMENSIONS.update(make_prefixed_specs(
    "golden_", dim_threshold=40.0, total_threshold=60.0,
    eval_timing=EvalTiming.FIRST_CHAPTERS, repairability=Repairability.HEAD,
    stat_scope=StatScope.HEAD, group_label="金三",
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
    """按**兼容视图** ``scope`` 取维度（新代码请用 ``specs_by_timing`` / ``specs_by_repairability``）。"""
    return [s for s in DIMENSIONS.values() if s.scope is scope]


def specs_by_timing(timing: EvalTiming) -> list[DimensionSpec]:
    return [s for s in DIMENSIONS.values() if s.eval_timing is timing]


def specs_by_repairability(r: Repairability) -> list[DimensionSpec]:
    return [s for s in DIMENSIONS.values() if s.repairability is r]


def audit_axis_consistency() -> list[str]:
    """审计"统计口径 × 可修复性 × 是否硬闸"三者是否相容；返回违规说明列表。

    2026-09-15 新增（登记单 ``20260915_质检假失败与回退死循环.md`` §八）。
    这些不变式的**可执行形式**（红线测试直接调用本函数）：

    - **S1 硬闸口径必在窗口内**：``required=True`` 的维度，其 ``stat_scope``
      必须是 ``CHAPTER``/``WINDOW``。否则"末窗回滚"这个唯一被授权的修复动作
      永远碰不到被判定的数据 ⇒ 判而不可修的删章死循环。
    - **S2 授权范围 ⊇ 统计范围**：``repairability is WINDOW``（授权末窗回滚）
      要求 ``stat_scope ∈ {CHAPTER, WINDOW}``。
    - **S3 COMPUTED 维必须显式声明口径**：``source is COMPUTED`` 而
      ``stat_scope is None`` ⇒ 违规（漏填不得静默通过）。
    - **S4 口径与时机相容**：``stat_scope is HEAD`` 要求
      ``eval_timing is FIRST_CHAPTERS``；``stat_scope is ENDING`` 要求
      ``eval_timing is BOOK_ENDING``。反之亦然（避免"量了开头却按窗口处置"）。
    """
    problems: list[str] = []
    for name, s in sorted(DIMENSIONS.items()):
        if s.required and not s.stat_scope_within_window:
            problems.append(
                f"S1 {name}: 硬指标（required=True）的 stat_scope="
                f"{s.stat_scope.value if s.stat_scope else None}，"
                f"末窗回滚修不到 ⇒ 会变成'判而不可修'的死循环"
            )
        if s.repairability is Repairability.WINDOW and not s.stat_scope_within_window:
            problems.append(
                f"S2 {name}: repairability=WINDOW（授权末窗回滚）但 stat_scope="
                f"{s.stat_scope.value if s.stat_scope else None} 超出窗口"
            )
        if s.source is SourceKind.COMPUTED and s.stat_scope is None:
            problems.append(
                f"S3 {name}: COMPUTED 维未声明 stat_scope（漏填不得静默通过）"
            )
        if s.stat_scope is StatScope.HEAD and s.eval_timing is not EvalTiming.FIRST_CHAPTERS:
            problems.append(
                f"S4 {name}: stat_scope=HEAD 但 eval_timing={s.eval_timing.value}"
                f"（量了开头却不在开头评）"
            )
        if s.eval_timing is EvalTiming.FIRST_CHAPTERS and s.stat_scope is not StatScope.HEAD:
            problems.append(
                f"S4 {name}: eval_timing=FIRST_CHAPTERS 但 stat_scope="
                f"{s.stat_scope.value if s.stat_scope else None}"
            )
        if s.stat_scope is StatScope.ENDING and s.eval_timing is not EvalTiming.BOOK_ENDING:
            problems.append(
                f"S4 {name}: stat_scope=ENDING 但 eval_timing={s.eval_timing.value}"
            )
    return problems


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

#: 评测取样窗口 = 回滚窗口（SSOT，2026-09-15）。
#:
#: 根因 D「评的样本 ≠ 判的对象」：评委此前只读末 3 章正文，而回滚窗口是 5 章
#: ——窗口内第 4/5 章的问题照样被算进分数，评委却拿不到这两章原文，判据与判的
#: 对象错位（判得对、却永远修不对）。
#: 此处只登记一次：``EvaluatorAgent.rollback_window`` 与
#: ``ReaderAppealScorer.eval_window`` 均以此为准，**禁止各自写字面量**；
#: 架构红线 ``test_eval_window_aligned_to_rollback_window`` 会拦住漂移。
EVAL_WINDOW_CHAPTERS: int = 5

#: 原 ``reader_appeal._EVAL_DIM_LABELS``
_EVAL_DIM_LABELS: dict[str, str] = {
    name: spec.prompt_label
    for name, spec in DIMENSIONS.items()
    if spec.prompt_label
}

#: 原 ``evaluator_dims._DIM_SCOPE``（仅登记非 ``window`` 的兼容视图值）。
#: 2026-09-15 起 ``foreshadow_recycle_rate`` 也落入此表（值 ``book_level``）——
#: 它每轮都评（``eval_timing=every_window``），但口径是全书台账、末窗回滚修不到，
#: 旧值 ``window`` 是把"每轮都评"误当成了"末窗可修"。``_scope_allows`` 只在
#: ``book_ending`` 上做闸门，故该变化不影响启用时机。
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
