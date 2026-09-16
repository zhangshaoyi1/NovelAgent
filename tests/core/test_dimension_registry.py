"""HA-Eval L2 · 维度契约层回归测试

锁定三件事：
1. 量纲错配在**构造期**暴露（旧实现里计数维 3.0 与评分维 3.0 完全同形，无从分辨）；
2. 派生别名与重构前的硬编码常量**逐项等价**（零行为漂移）；
3. ``DimensionResult.to_dict()`` 输出字段**完全不变**（to_markdown / Web UI / .state 落盘依赖）。
"""

from __future__ import annotations

import pytest

from agent.agents.evaluator_types import DimensionResult
from agent.core.quality.dimension_registry import (
    COUNT_DIMS,
    DIMENSIONS,
    MAX_PLAUSIBLE_COUNT,
    STRICT_DIMENSION_CONTRACT,
    Direction,
    DimensionContractError,
    DimensionSpec,
    EvalTiming,
    Repairability,
    Scope,
    StatScope,
    Unit,
    _DIM_SCOPE,
    _EVAL_DIM_LABELS,
    _SOFT_MARGIN,
    _SUMMARY_REASONS,
    audit_axis_consistency,
    clamp_value,
    enforce_contract,
    get_spec,
    safe_default_for,
    specs_by_scope,
    specs_by_timing,
    validate_value,
    value_plausibility_anomaly,
)


# ============================================================
# 1. 量纲契约：错配在构造期暴露
# ============================================================
class TestUnitContract:
    def test_count_dim_rejects_non_integer(self):
        """缺陷条数必须是整数——3.7 条漏洞没有意义。"""
        spec = get_spec("logic_holes")
        assert spec.unit is Unit.COUNT
        with pytest.raises(DimensionContractError, match="必须为整数"):
            DimensionResult("logic_holes", "逻辑漏洞", 3.7, 0.0, "<=", True, "llm")

    def test_count_dim_rejects_negative(self):
        spec = get_spec("logic_holes")
        assert validate_value(spec, -1.0) is not None

    def test_score_dim_rejects_out_of_range(self):
        spec = get_spec("coherence")
        assert spec.unit is Unit.SCORE_0_100
        with pytest.raises(DimensionContractError, match="超出"):
            DimensionResult("coherence", "连贯性", 150.0, 85.0, ">=", False, "llm")

    def test_ratio_dim_rejects_out_of_range(self):
        with pytest.raises(DimensionContractError):
            DimensionResult(
                "foreshadow_recycle_rate", "伏笔闭环", 1.5, 0.9, ">=", False, "computed"
            )

    def test_valid_values_pass(self):
        DimensionResult("logic_holes", "逻辑漏洞", 3.0, 0.0, "<=", True, "llm")
        DimensionResult("coherence", "连贯性", 88.0, 85.0, ">=", False, "llm")
        DimensionResult("foreshadow_recycle_rate", "伏笔闭环", 0.95, 0.9, ">=", False, "computed")

    def test_unregistered_dim_is_tolerant(self):
        """未登记维度不得误伤——保持向后宽容。"""
        r = DimensionResult("some_new_dim", "新维度", 999.0, 0.0, "<=", False, "computed")
        assert r.spec is None
        assert r.unit == ""


# ============================================================
# 2. 量纲自洽契约（2026-09-16，登记单 ``20260916_计数维被当分数返回``）
# ------------------------------------------------------------
# 计数维曾被 LLM 填入 0–100 分数（全库 7/309，取值双峰且 13–84 完全空档），
# 而它们是 required + 阈值 0 + 授权整窗回退 ⇒ 单次单位混淆 = 销毁 5 章。
# ============================================================
class TestCountPlausibilityContract:
    def test_count_dim_value_bounded(self):
        """★ 红线（登记单 §六.2）：计数维的「合理条数上界」必须被声明且可判。

        声明在 ``DimensionSpec.plausible_range``（SSOT）；越界值**必须**被判为异常，
        好让上层不可信化——而不是被静默钳制成上界（那等于伪造"20 处崩坏"）。
        """
        for name in COUNT_DIMS:
            spec = get_spec(name)
            lo, hi = spec.plausible_range
            assert (lo, hi) == (0.0, float(MAX_PLAUSIBLE_COUNT)), (
                f"{name} 未声明量纲自洽上界 ⇒ 分数会被当成条数"
            )

    def test_score_like_value_is_anomaly(self):
        """实测异常样本（09-16 11:32 那次回退的触发轮）必须被判为量纲混淆。"""
        for name, bad in (
            ("character_stability_high", 95.0),
            ("setting_consistency_high", 85.0),
            ("logic_holes", 100.0),
        ):
            reason = value_plausibility_anomaly(name, bad)
            assert reason and "量纲混淆" in reason, f"{name}={bad} 未被拦下"

    def test_legitimate_counts_pass(self):
        """实测合法条数（≤12）不得误伤——上界取 20 已留 ~67% 余量。"""
        for name in COUNT_DIMS:
            for v in (0.0, 1.0, 3.0, 5.0, 12.0, 20.0):
                assert value_plausibility_anomaly(name, v) is None, f"{name}={v} 被误判"

    def test_negative_count_is_anomaly(self):
        assert value_plausibility_anomaly("logic_holes", -1.0) is not None

    def test_non_count_dims_unaffected(self):
        """评分维/比率维不适用计数条数上界（它们的值域本就不同）。"""
        assert value_plausibility_anomaly("coherence", 95.0) is None
        assert value_plausibility_anomaly("foreshadow_recycle_rate", 0.95) is None

    def test_unknown_dim_is_tolerant(self):
        assert value_plausibility_anomaly("some_new_dim", 999.0) is None

    def test_clamp_is_not_the_guard(self):
        """钉住分工：``clamp_value`` 对计数维**原样放行** 95（值域是 ``[0, inf)``）。

        所以钳制不能当处置——95 会被当成"95 处崩坏"直接流向阈值 0 的硬闸。
        守卫必须是 ``value_plausibility_anomaly``（判"不可信"，不改值）。
        """
        assert clamp_value("character_stability_high", 95.0) == 95.0, (
            "clamp 不应改值——改了就等于伪造一个确凿结论"
        )
        assert value_plausibility_anomaly("character_stability_high", 95.0) is not None

    def test_value_range_by_unit(self):
        assert get_spec("coherence").value_range == (0.0, 100.0)
        assert get_spec("logic_holes").value_range == (0.0, float("inf"))
        assert get_spec("pacing_abnormal").value_range == (0.0, 1.0)


# ============================================================
# 2. 作用域：让 L4 处置层能自动推导修复范围
# ============================================================
class TestScope:
    def test_book_ending_dims(self):
        names = {s.name for s in specs_by_scope(Scope.BOOK_ENDING)}
        assert names == {"mainline_progress", "ending_convergence"}

    def test_first_chapters_dims_are_golden(self):
        names = {s.name for s in specs_by_scope(Scope.FIRST_CHAPTERS)}
        assert names and all(n.startswith("golden_") for n in names)

    def test_all_axes_queryable(self):
        assert {s.name for s in specs_by_timing(EvalTiming.BOOK_ENDING)} == {
            "mainline_progress", "ending_convergence"
        }
        assert {s.name for s in specs_by_scope(Scope.BOOK_LEVEL)} == {
            "foreshadow_recycle_rate"
        }

    def test_window_is_default(self):
        assert get_spec("coherence").scope is Scope.WINDOW
        assert get_spec("logic_holes").scope is Scope.WINDOW


# ============================================================
# 2b. 两根正交轴（2026-09-15 拆分）：何时评 × 在哪修 + 统计口径
# ============================================================
class TestAxisSplit:
    """锁定「单轴 Scope 混淆两义」的修复。

    拆分动机：``Scope`` 把"何时评"与"在哪修"压成一根轴，枚举里因此**没有**
    "每轮都评 + 末窗回滚修不到"这一格，漏填即落进最激进的 ``WINDOW``
    （唯一授权不可逆回滚的取值）—— ``padding_repetition_abnormal`` 正是栽在这。
    """

    def test_timing_and_repairability_are_independent(self):
        assert get_spec("mainline_progress").eval_timing is EvalTiming.BOOK_ENDING
        assert get_spec("mainline_progress").repairability is Repairability.GLOBAL
        # 每轮都评，但末窗修不到 —— 拆分前这一格无处安放
        assert get_spec("foreshadow_recycle_rate").eval_timing is EvalTiming.EVERY_WINDOW
        assert get_spec("foreshadow_recycle_rate").repairability is Repairability.GLOBAL

    def test_derived_scope_view(self):
        """``scope`` 是派生只读视图：时机优先，其次可修复性。"""
        assert get_spec("golden_total").scope is Scope.FIRST_CHAPTERS
        assert get_spec("ending_convergence").scope is Scope.BOOK_ENDING
        assert get_spec("coherence").scope is Scope.WINDOW
        assert get_spec("foreshadow_recycle_rate").scope is Scope.BOOK_LEVEL

    def test_repairability_default_is_conservative(self):
        """漏填 ``repairability`` 必须落到 GLOBAL（上报人工），不得是 WINDOW（销毁内容）。"""
        bare = DimensionSpec(
            name="x", label="x", unit=Unit.COUNT,
            direction=Direction.LOWER_BETTER, default_threshold=0.0,
        )
        assert bare.repairability is Repairability.GLOBAL
        assert bare.eval_timing is EvalTiming.EVERY_WINDOW

    def test_axis_consistency_audit_is_clean(self):
        """S1–S4：硬闸口径必在窗口内 / 授权范围 ⊇ 统计范围 / COMPUTED 必声明 / 口径与时机相容。"""
        assert audit_axis_consistency() == []

    def test_audit_catches_hard_gate_book_scope(self):
        """反向验证：审计必须能抓住"硬闸 + 全书口径"（Q6 形态）。"""
        from dataclasses import replace as _replace

        bad = _replace(
            get_spec("padding_repetition_abnormal"),
            stat_scope=StatScope.BOOK,
        )
        import agent.core.quality.dimension_registry as reg

        reg.DIMENSIONS["__probe_bad__"] = bad
        try:
            problems = reg.audit_axis_consistency()
        finally:
            del reg.DIMENSIONS["__probe_bad__"]
        assert any("__probe_bad__" in p and "S1" in p for p in problems), problems


# ============================================================
# 3. 派生别名与重构前硬编码常量逐项等价
# ============================================================
class TestDerivedAliases:
    def test_soft_margin_matches_legacy(self):
        legacy = {
            "character_stability_high": 0.0,
            "setting_consistency_high": 0.0,
            "logic_holes": 0.0,
            "coherence": 5.0,
            "readability": 0.0,
            "foreshadow_recycle_rate": 0.0,
            "pacing_abnormal": 0.0,
            "mainline_progress": 0.0,
            "ending_convergence": 0.0,
        }
        for name, expected in legacy.items():
            assert _SOFT_MARGIN[name] == expected, name

    def test_count_dims_matches_legacy(self):
        assert COUNT_DIMS == frozenset(
            {"character_stability_high", "setting_consistency_high", "logic_holes"}
        )

    def test_eval_dim_labels_matches_legacy(self):
        # 2026-09-16（设计产出三端可达）：两个一致性维的 prompt_label 明写
        # 「弧光轨迹 / 设定台账＋设计意图」为判定依据 —— 否则评委看不到设计轨，
        # 会把**设计内转变**判成人设崩坏（回退死循环的判据侧成因）。
        # 这是**有意**的文案变更，故此处基线随之更新。
        legacy_labels = {
            "character_stability_high":
                "人设稳定性（角色言行/动机是否与角色档案、**弧光轨迹**冲突——"
                "沿弧光登记轨迹的有序推进属设计内成长，**不算矛盾**；"
                "仅倒退/跳档/无契机/与档案直接冲突才计；逐项列举崩坏处数量。"
                "**value 只填整数条数（无硬伤填 0），禁止填 0–100 分数/百分比**）",
            "setting_consistency_high":
                "设定一致性（境界/金手指/世界观规则是否被打破——"
                "以**设定台账＋设计意图**为准，设计轨内允许的变化不算打破；"
                "逐项列举冲突数量。"
                "**value 只填整数条数（无冲突填 0），禁止填 0–100 分数/百分比**）",
            "logic_holes":
                "逻辑漏洞（情节硬伤/因果不成立，逐项列举漏洞数量。"
                "**value 只填整数条数（无漏洞填 0），禁止填 0–100 分数/百分比**）",
            "coherence":
                "连贯性（章节衔接/叙事流畅度，0-100 评分）",
            "readability":
                "追读力/可读性（让人想继续读的欲望，0-100 评分）",
        }
        for name, expected in legacy_labels.items():
            assert _EVAL_DIM_LABELS[name] == expected, name

    def test_dim_scope_matches_legacy(self):
        # 2026-09-15：``foreshadow_recycle_rate`` 由 window → book_level。
        # 它每轮都评（eval_timing=every_window），但口径是全书台账、末窗回滚修不到；
        # 旧值 window 是把"每轮都评"误当成了"末窗可修"（拆分前 Scope 单轴混淆两义）。
        # 因 required=False，处置动作仍是 LOCAL_REPAIR，行为零变化。
        assert _DIM_SCOPE == {
            "mainline_progress": "book_ending",
            "ending_convergence": "book_ending",
            "foreshadow_recycle_rate": "book_level",
            **{f"golden_{k}": "first_chapters"
               for k in ("hook_strength", "payoff_density", "immersion",
                         "character_arc", "world_novelty", "emotion_curve", "total")},
        }

    def test_summary_reasons_cover_legacy_keys(self):
        for name in ("character_stability_high", "setting_consistency_high",
                     "foreshadow_recycle_rate", "coherence", "readability",
                     "pacing_abnormal", "logic_holes", "appeal_hook_strength",
                     "golden_hook_strength", "padding_repetition_abnormal",
                     "mainline_progress", "ending_convergence"):
            assert _SUMMARY_REASONS.get(name), name

    def test_safe_default_matches_legacy(self):
        """原 ``_default_for``/``_score``：硬计数维 0、评分维 100。"""
        assert safe_default_for("coherence") == 100.0
        assert safe_default_for("readability") == 100.0
        assert safe_default_for("logic_holes") == 0.0
        assert safe_default_for("character_stability_high") == 0.0

    def test_clamp_matches_legacy(self):
        """原 ``_clamp``：评分维钳到 [0,100]，计数维非负整数化。"""
        assert clamp_value("coherence", 150.0) == 100.0
        assert clamp_value("coherence", -5.0) == 0.0
        assert clamp_value("logic_holes", 3.9) == 3.0
        assert clamp_value("logic_holes", -2.0) == 0.0


# ============================================================
# 4. 兼容性：to_dict 字段不变
# ============================================================
class TestBackwardCompat:
    def test_to_dict_fields_unchanged(self):
        r = DimensionResult(
            "coherence", "连贯性", 88.0, 85.0, ">=", False, "llm",
            soft_margin=5.0, scope="window",
        )
        # 2026-09-11：HA-Eval L4 新增 confidence / trustworthy（只增不删）
        assert set(r.to_dict()) == {
            "name", "label", "value", "threshold", "direction",
            "required", "source", "soft_margin", "scope", "passed",
            "confidence", "trustworthy",
        }

    def test_positional_construction_still_works(self):
        r = DimensionResult("logic_holes", "逻辑漏洞", 0.0, 0.0, "<=", True, "llm/default")
        assert r.name == "logic_holes"
        assert r.passed is True

    def test_spec_auto_bound_from_name(self):
        r = DimensionResult("coherence", "连贯性", 88.0, 85.0, ">=", False, "llm")
        assert r.spec is not None
        assert r.spec.name == "coherence"
        assert r.unit == "score"

    def test_confidence_defaults_to_one(self):
        r = DimensionResult("logic_holes", "逻辑漏洞", 0.0, 0.0, "<=", True, "computed")
        assert r.confidence == 1.0


# ============================================================
# 5. 登记表完整性
# ============================================================
class TestRegistryIntegrity:
    def test_all_specs_are_frozen_and_named(self):
        for name, spec in DIMENSIONS.items():
            assert spec.name == name
            assert isinstance(spec, DimensionSpec)

    def test_count_dims_have_counted_by_issues(self):
        for name in COUNT_DIMS:
            assert get_spec(name).counted_by_issues is True

    def test_required_dims_are_hard_gates(self):
        # 2026-09-15 契约变更（登记单 ``20260915_回退熔断账实不符与降级当通过`` §三.1）：
        # ``logic_holes`` 由 required=True → **False**，退出回退授权。
        # 理由：它的 prompt 是「逐项列举漏洞数量」——值域无自然零点、无上界，
        # 27 轮实测中位数 3、`==0` 仅 15% ⇒ 阈值 0 是**判据不可达**，
        # 却授权 ROLLBACK_REWRITE（不可逆，销毁末窗 5 章）。
        # 失败后改走 ``_soft_dim → LOCAL_REPAIR``（可逆）+ warn，仍计入 overall_pass。
        # 断言反向锁死：它**不得**再回到硬闸（回潮即红）。
        for name in ("character_stability_high", "setting_consistency_high",
                     "padding_repetition_abnormal"):
            assert get_spec(name).required is True
        assert get_spec("logic_holes").required is False, (
            "logic_holes 已拍板退出回退授权（判据不可达），不得改回 required=True"
        )

    def test_appeal_and_golden_generated(self):
        for k in ("hook_strength", "payoff_density", "immersion",
                  "character_arc", "world_novelty", "emotion_curve"):
            assert f"appeal_{k}" in DIMENSIONS
            assert f"golden_{k}" in DIMENSIONS
        assert "appeal_total" in DIMENSIONS
        assert "golden_total" in DIMENSIONS

    def test_direction_matches_required_semantics(self):
        """越低越好的维度（缺陷计数）必须用 LOWER_BETTER。"""
        for name in COUNT_DIMS:
            assert get_spec(name).direction is Direction.LOWER_BETTER
        assert get_spec("coherence").direction is Direction.HIGHER_BETTER

    def test_warn_mode_does_not_raise(self, monkeypatch):
        """告警模式：只记录不抛错，供生产先观察误报。"""
        import agent.core.quality.dimension_registry as reg

        monkeypatch.setattr(reg, "STRICT_DIMENSION_CONTRACT", False)
        reg.CONTRACT_VIOLATIONS.clear()
        enforce_contract(get_spec("coherence"), 150.0)
        assert reg.CONTRACT_VIOLATIONS
        assert STRICT_DIMENSION_CONTRACT is True  # 默认严格
