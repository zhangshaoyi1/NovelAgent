"""维度契约 × 处置动作 矩阵红线（2026-09-15）

背景（登记单 ``项目文档/优化/20260915_质检假失败与回退死循环.md``）
------------------------------------------------------------------
「一个维度不达标 → 做什么」由两层声明共同决定：

- ``core/quality/dimension_registry.DIMENSIONS``（声明 ``required`` / ``unit`` / ``scope``）；
- ``core/quality/disposition.DEFAULT_RULES`` × ``ACTION_PRECEDENCE``（声明匹配规则与动作）。

两层之间**没有任何编译期/运行期的对账**。后果实测：

- ``pacing_abnormal``（RATIO、``required=False``、``Scope.WINDOW``）匹配不到
  「定向修复」规则（旧判据只认 ``SCORE_0_100``）→ 跌进兜底 → 又因
  ``ROLLBACK_REWRITE(2) > LOCAL_REPAIR(1)`` 被**升级**为「销毁整窗 5 章」，
  与已拍板语义「软维度失败不触发回滚」直接冲突（灵荒薪传 23/24 轮走此路径）；
- 兜底动作本身是 ``ROLLBACK_REWRITE`` → **漏登记 = 升级为最重处置**，
  使登记表形同虚设。

本文件把「两层声明必须语义一致」固化为可检查约束，并作为**棘轮**：
新增维度若未在处置规则表里归好类（落到兜底），或 ``scope``/``required`` 与
动作强度不匹配，红线立即失败——而不是等到线上回退死循环才被发现。

约束
----
- M1  **已登记维度绝不走兜底**：每个 ``DIMENSIONS`` 条目都必须被某条显式规则命中。
      （防止新增维度未归类 → 默认触发不可逆动作）
- M2  **软维不得不可逆**：``required=False`` ⇒ 动作 ∉ IRREVERSIBLE_ACTIONS。
- M3  **作用域可修性**：``scope != WINDOW`` ⇒ 动作 ∉ IRREVERSIBLE_ACTIONS
      （末窗回滚修不到开头/全局结构问题，这是 2026-09-07 事故的既有结论）。
- M4  **硬指标不得降级为放行**：``required=True`` 且 ``scope=WINDOW``
      ⇒ 动作必须是 ROLLBACK_REWRITE（硬指标不达标不可只告警）。
- M5  **兜底动作不得不可逆**：不可逆动作必须由显式规则授权
      （「未归类」不能默认销毁内容）。
- M6  **登记表字段完整**：``unit`` 必须是 ``Unit`` 枚举值，``eval_timing`` /
      ``repairability`` / ``stat_scope`` 已声明，阈值/方向合法。
- M7  **硬闸修不到 ⇒ 上报人工**：``required=True`` 但 ``repairability != WINDOW``
      ⇒ 动作必须是 ESCALATE（不得 ROLLBACK_REWRITE）。这是 2026-09-15
      复盘中"硬闸作用域错配 = 删章死循环"（Q6）的可执行形式。
- M8  **统计口径与动作相容**：`dimension_registry.audit_axis_consistency()`（S1–S4）
      在真实登记表上零违规。
"""

from __future__ import annotations

from types import SimpleNamespace

from agent.agents.evaluator_types import DimensionResult
from agent.core.quality.dimension_registry import (
    DIMENSIONS,
    Direction,
    DimensionSpec,
    EvalTiming,
    Repairability,
    Scope,
    StatScope,
    Unit,
    audit_axis_consistency,
)
from agent.core.quality.disposition import (
    DEFAULT_RULES,
    IRREVERSIBLE_ACTIONS,
    Action,
    DispositionPolicy,
)


def _failing_dim(name: str) -> DimensionResult:
    """按登记表构造一个**必然不达标但可信**的结果，用于探测处置动作。

    要点（2026-09-15 修正）：
    早期写法是「把 value 往坏方向挪一格再 clamp」，但 ``mainline_progress``
    （COUNT / HIGHER_BETTER / 阈值 0）差一格会 clamp 回 0，恰好等于阈值 →
    ``passed`` 为真，探测失效。故改为：先把 value 取到最差端，若仍判合格
    （阈值过低），则**抬高阈值**制造失败——``plan()`` 只读 ``passed`` /
    ``spec`` / ``required`` / ``confidence``，不读阈值本身，抬高阈值不影响判据。
    """
    spec = DIMENSIONS[name]
    lo, hi = spec.value_range
    threshold = spec.default_threshold
    if spec.direction is Direction.HIGHER_BETTER:
        value = spec.clamp(lo)
        if value >= threshold - spec.soft_margin - 1e-9:
            threshold = value + 1.0
    else:
        raw = hi if hi != float("inf") else threshold + 1.0
        value = spec.clamp(raw)
        if value <= threshold + spec.soft_margin + 1e-9:
            threshold = value - 1.0
    if spec.unit is Unit.COUNT:
        value = float(int(value))
    dim = DimensionResult(
        name, spec.label, value, threshold,
        spec.direction.value, spec.required, "llm/default",
    )
    # 契约自检：探测用例必须真的失败，否则红线判据无效（早期 bug 复现点）。
    assert not dim.passed, f"{name}: 构造用例未构成失败，红线判据无效"
    return dim


def _degraded_probe() -> DimensionResult:
    """降级维度探针：confidence=0，用于命中 ``untrusted_evidence`` 规则。

    正常失败用例（可信）永远命中不到这条「证据不可信」规则，故需单独构造。
    """
    spec = DIMENSIONS["coherence"]
    return DimensionResult.degraded(
        spec.name, spec.label, spec.default_threshold, spec.direction.value,
    )


def _plan_for(name: str):
    return DispositionPolicy().plan([_failing_dim(name)])


def _hard_gate_out_of_window_probe() -> SimpleNamespace:
    """构造「硬指标 + 修复范围不在回退窗口」的形态，探 ``hard_gate_out_of_scope``。

    当前登记表里没有这种维度（正是本次收紧的结果），但规则必须**可达且有效**——
    它是防御未来误声明的兜底闸。故用合成 spec 直接探测，而不是等真实维度出现。
    """
    spec = DimensionSpec(
        name="__probe_hard_global__", label="探针·硬闸但全局",
        unit=Unit.COUNT, direction=Direction.LOWER_BETTER, default_threshold=0.0,
        required=True, repairability=Repairability.GLOBAL, stat_scope=StatScope.BOOK,
    )
    return SimpleNamespace(
        spec=spec, required=True, confidence=1.0,
        name=spec.name, label=spec.label,
    )


class TestM1EveryRegisteredDimIsClassified:
    def test_no_registered_dim_falls_through_to_fallback(self) -> None:
        offenders = [n for n in DIMENSIONS if "fallback" in _plan_for(n).rule_names]
        assert offenders == [], (
            f"以下已登记维度未被任何显式处置规则覆盖，会走兜底：{offenders}。"
            f"新增维度必须同时在 disposition.DEFAULT_RULES 层面归类"
            f"（按 required/unit/scope 声明即可自动归类），否则"
            f"「未归类」会退化为最重处置。"
        )

    def test_rule_table_has_no_unreachable_non_fallback_rule(self) -> None:
        """反向对账：规则表里除兜底外，每条规则都应至少命中一个已登记维度。"""
        matched: set[str] = set()
        for name in DIMENSIONS:
            matched.update(_plan_for(name).rule_names)
        # 只由特殊形态命中的规则，需单独探针（已在登记表里不可复现）。
        matched.update(DispositionPolicy().plan([_degraded_probe()]).rule_names)
        matched.update(
            DispositionPolicy().plan([_hard_gate_out_of_window_probe()]).rule_names
        )
        declared = {r.name for r in DEFAULT_RULES if not r.is_fallback}
        unreachable = sorted(declared - matched)
        assert unreachable == [], (
            f"以下规则当前不命中任何已登记维度（可能是维度被删后的残留）：{unreachable}"
        )


class TestM2SoftDimsAreNeverIrreversible:
    def test_optional_dims_never_rollback(self) -> None:
        offenders = [
            (n, _plan_for(n).action.value)
            for n in DIMENSIONS
            if not DIMENSIONS[n].required and _plan_for(n).action in IRREVERSIBLE_ACTIONS
        ]
        assert offenders == [], (
            f"软维度（required=False）不得触发不可逆动作，实测：{offenders}。"
            f"语义已拍板：软维度失败只告警/定向修复。"
        )


class TestM3ScopeDecidesRepairability:
    def test_non_window_scope_never_rollback(self) -> None:
        offenders = [
            (n, DIMENSIONS[n].scope.value, _plan_for(n).action.value)
            for n in DIMENSIONS
            if DIMENSIONS[n].scope is not Scope.WINDOW
            and _plan_for(n).action in IRREVERSIBLE_ACTIONS
        ]
        assert offenders == [], (
            f"开头/全局结构维不得用末窗回滚处置（回滚修不到），实测：{offenders}"
        )


class TestM4HardGatesAreNotDowngraded:
    def test_window_hard_gate_rolls_back(self) -> None:
        offenders = [
            (n, _plan_for(n).action.value)
            for n in DIMENSIONS
            if DIMENSIONS[n].required and DIMENSIONS[n].scope is Scope.WINDOW
            and _plan_for(n).action is not Action.ROLLBACK_REWRITE
        ]
        assert offenders == [], (
            f"窗口内硬指标不达标必须可触发回滚重写，实测：{offenders}"
        )


class TestM5FallbackNeverDestroysContent:
    def test_fallback_action_is_not_irreversible(self) -> None:
        fallback = [r for r in DEFAULT_RULES if r.is_fallback]
        assert len(fallback) == 1, "兜底规则应恰好一条"
        assert fallback[0].action not in IRREVERSIBLE_ACTIONS, (
            "兜底动作不得不可逆：不可逆动作必须由显式规则授权，"
            "否则「漏登记维度」会静默升级为销毁内容"
        )


class TestM6RegistryFieldsAreDeclared:
    def test_every_dim_declares_the_four_semantics(self) -> None:
        problems: list[str] = []
        for name, spec in DIMENSIONS.items():
            if not isinstance(spec.unit, Unit):
                problems.append(f"{name}: unit 未声明（{spec.unit!r}）")
            if not isinstance(spec.scope, Scope):
                problems.append(f"{name}: scope 视图异常（{spec.scope!r}）")
            if not isinstance(spec.eval_timing, EvalTiming):
                problems.append(f"{name}: eval_timing 未声明（{spec.eval_timing!r}）")
            if not isinstance(spec.repairability, Repairability):
                problems.append(f"{name}: repairability 未声明（{spec.repairability!r}）")
            if spec.source.value == "computed" and not isinstance(spec.stat_scope, StatScope):
                problems.append(f"{name}: COMPUTED 维未声明 stat_scope（{spec.stat_scope!r}）")
            if not isinstance(spec.direction, Direction):
                problems.append(f"{name}: direction 未声明（{spec.direction!r}）")
            if not spec.label:
                problems.append(f"{name}: label 为空")
            lo, hi = spec.value_range
            if not (lo <= spec.default_threshold <= hi):
                problems.append(f"{name}: 阈值 {spec.default_threshold} 超出量纲值域")
        assert problems == [], "维度契约字段不完整：\n" + "\n".join(problems)


class TestM7HardGateOutOfScopeEscalates:
    """Q6 红线：硬闸但修复范围不在回退窗口 ⇒ 上报人工，**不得**删章重写。

    2026-09-15 复盘（登记单 §八）：「required=True」只说明"必须达标"，不说明
    "回退末 N 章就能达标"。两者混同的后果就是"判得对、但永远修不好"的删章死循环
    —— ``padding_repetition_abnormal`` 曾以"硬闸 + 全书口径"的形态存活，
    被母登记单 §二 的阈值取字段名脚本 bug 误判为无问题而漏过。
    """

    def test_probe_hard_gate_global_scope_escalates(self) -> None:
        plan = DispositionPolicy().plan([_hard_gate_out_of_window_probe()])
        assert plan.action is Action.ESCALATE, (
            f"硬指标但修复范围不在窗口内必须上报人工，实测 {plan.action.value}"
        )
        assert plan.is_irreversible is False
        assert "hard_gate_out_of_scope" in plan.rule_names

    def test_no_registered_dim_is_hard_gate_out_of_window(self) -> None:
        """当前登记表里不应存在"硬闸 + 非窗口修复范围"的维度（收紧后的现状）。"""
        offenders = [
            n for n, s in DIMENSIONS.items()
            if s.required and s.repairability is not Repairability.WINDOW
        ]
        assert offenders == [], (
            f"以下硬指标授权不了回退修复，动作会变成上报人工：{offenders}"
            f"——若确为有意设计请同步本条红线"
        )


class TestM8StatisticScopeMatchesAction:
    """S1–S4：统计口径必须落在动作能触及的范围内（`audit_axis_consistency`）。"""

    def test_no_axis_violations(self) -> None:
        problems = audit_axis_consistency()
        assert problems == [], "契约轴不一致：\n" + "\n".join(problems)

    def test_window_repairable_dims_measure_within_window(self) -> None:
        offenders = [
            (n, s.stat_scope.value if s.stat_scope else None)
            for n, s in DIMENSIONS.items()
            if s.repairability is Repairability.WINDOW
            and s.stat_scope not in (StatScope.CHAPTER, StatScope.WINDOW)
        ]
        assert offenders == [], (
            f"授权了末窗回滚（repairability=WINDOW）但统计口径超出窗口的维度："
            f"{offenders}——这正是『判得对、但永远修不好』的形态"
        )


class TestM9RequiredHasSingleSource:
    """``required`` 只有登记表一个真源（2026-09-15 §二.R4）。

    背景：``required`` 曾经既可经构造位置参传入、又登记在 ``DIMENSIONS``。
    ``disposition._hard_gate`` 早已声明「以登记表为唯一真源」，而
    :class:`DimensionResult` 的 ``required`` / ``hard_failed`` / ``gate_decision``
    读的是**实例字段** ⇒ 两者分歧时同一份报告自相矛盾。

    实测分歧（本次修复的起因）：``logic_holes`` 退出回退授权后，
    ``disposition`` 正确给出 ``LOCAL_REPAIR``，而构造点仍传 ``required=True``
    ⇒ 裁决层仍判 ``block``、报告仍标「硬指标」——**半生效**。

    本类同时钉住两条：
    - 运行期：构造字面量与登记表冲突时，以登记表为准（自我修复，不靠人记得）；
    - 静态期：``src/`` 里任何 ``DimensionResult(...)`` 调用都不得写出与登记表
      冲突的字面量（防止新调用点又把两个真源分叉出去）。
    """

    def test_runtime_syncs_required_from_registry(self) -> None:
        """故意传反的 required 必须被登记表纠正。"""
        assert DIMENSIONS["logic_holes"].required is False
        r = DimensionResult("logic_holes", "逻辑漏洞", 3.0, 0.0, "<=", True, "llm")
        assert r.required is False, "登记表 required=False ⇒ 实例字段必须同步，否则裁决层半生效"
        assert r.to_dict()["required"] is False

        assert DIMENSIONS["character_stability_high"].required is True
        r2 = DimensionResult(
            "character_stability_high", "人设稳定", 3.0, 0.0, "<=", False, "llm"
        )
        assert r2.required is True, "登记表 required=True ⇒ 不得被构造字面量降级"

    def test_unregistered_dim_keeps_caller_literal(self) -> None:
        """未登记维度没有登记表可依据 ⇒ 保留调用方字面量（向后宽容）。"""
        r = DimensionResult("some_new_dim", "新维度", 1.0, 0.0, "<=", True, "computed")
        assert r.spec is None
        assert r.required is True

    def test_no_source_construction_contradicts_registry(self) -> None:
        """AST 扫描 ``src/``：构造点字面量不得与登记表分歧。"""
        import ast
        from pathlib import Path

        root = Path(__file__).resolve().parents[2] / "src"
        offenders: list[str] = []
        for path in sorted(root.rglob("*.py")):
            try:
                tree = ast.parse(path.read_text(encoding="utf-8"))
            except (OSError, SyntaxError):  # pragma: no cover - 语法错误由别的红线管
                continue
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call):
                    continue
                fn = node.func
                if (getattr(fn, "id", None) or getattr(fn, "attr", None)) != "DimensionResult":
                    continue
                args = list(node.args)
                kwargs = {k.arg: k.value for k in node.keywords}
                if args and isinstance(args[0], ast.Constant):
                    name = args[0].value
                elif isinstance(kwargs.get("name"), ast.Constant):
                    name = kwargs["name"].value
                else:
                    continue  # 动态名，无法静态判定
                if len(args) >= 6 and isinstance(args[5], ast.Constant):
                    literal = args[5].value
                elif isinstance(kwargs.get("required"), ast.Constant):
                    literal = kwargs["required"].value
                else:
                    continue  # 省略（用默认值）或动态取值
                spec = DIMENSIONS.get(name)
                if spec is not None and literal is not spec.required:
                    rel = path.relative_to(root.parent)
                    offenders.append(
                        f"{rel}:{node.lineno} {name} literal={literal} registry={spec.required}"
                    )
        assert offenders == [], (
            "以下构造点写出了与登记表冲突的 required（required 只有登记表一个真源，"
            "请删掉字面量或改用 DIMENSIONS[name].required）：\n" + "\n".join(offenders)
        )
